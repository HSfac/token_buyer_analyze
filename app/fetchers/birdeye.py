import aiohttp
import ssl
from typing import List, Dict, Any, Optional
from app.config import settings
from app.models.types import TokenInfo
import asyncio
import time
from datetime import datetime
import pytz
import logging

logger = logging.getLogger(__name__)

class BirdeyeFetcher:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://public-api.birdeye.so"
        self.headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json"
        }
        # SSL 컨텍스트 설정
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        # 요청 제한 설정
        self.request_count = 0
        self.last_request_time = 0
        self.rate_limit = 10  # 초당 10회 요청 제한
        self.rate_limit_window = 1  # 1초

    async def _wait_for_rate_limit(self):
        """
        요청 속도 제한을 준수하기 위해 대기합니다.
        """
        current_time = time.time()
        if current_time - self.last_request_time < self.rate_limit_window:
            self.request_count += 1
            if self.request_count > self.rate_limit:
                wait_time = self.rate_limit_window - (current_time - self.last_request_time)
                print(f"[Birdeye] 요청 제한 도달. {wait_time:.2f}초 대기")
                await asyncio.sleep(wait_time)
        else:
            self.request_count = 1
            self.last_request_time = current_time

    async def get_token_transactions(
        self, 
        token_address: str, 
        limit: int = 1000,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """토큰의 트랜잭션 데이터를 가져옵니다."""
        try:
            print(f"\n[Birdeye] 트랜잭션 데이터 요청 시작: {token_address}")
            if start_time:
                print(f"[Birdeye] 시작 시간: {start_time}")
            if end_time:
                print(f"[Birdeye] 종료 시간: {end_time}")
            
            # API 요청 URL
            url = f"{self.base_url}/defi/txs/token"  # 원래 엔드포인트로 복구
            print(f"[Birdeye] API 요청 URL: {url}")
            
            # 요청 파라미터
            params = {
                "address": token_address,
                "limit": 50,  # API 제한: 한 번에 최대 50개
                "offset": 0,
                "tx_type": "swap",  # type에서 tx_type으로 변경
                "sort_type": "desc"  # 정렬 방식 추가
            }
            print(f"[Birdeye] 요청 파라미터: {params}")
            
            all_transactions = []
            offset = 0
            
            while len(all_transactions) < limit:
                # offset 파라미터 업데이트
                params["offset"] = offset
                
                # API 요청
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, 
                        params=params, 
                        headers=self.headers,
                        ssl=self.ssl_context
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get("success"):
                                transactions = data.get("data", {}).get("items", [])
                                if not transactions:  # 더 이상 데이터가 없으면 중단
                                    print("[Birdeye] 더 이상 트랜잭션이 없습니다.")
                                    break
                                    
                                print(f"[Birdeye] 수신된 트랜잭션 수: {len(transactions)}")
                                
                                # 시간 필터링 적용
                                filtered_transactions = []
                                for tx in transactions:
                                    # 트랜잭션 데이터 디버그 출력
                                    print(f"[Birdeye] 트랜잭션 데이터: {tx}")
                                    
                                    # 타임스탬프 처리
                                    tx_timestamp = tx.get("blockTime", 0)  # blockTime 사용
                                    print(f"[Birdeye] 원본 타임스탬프: {tx_timestamp}")
                                    
                                    # 타임스탬프가 밀리초 단위인 경우 초 단위로 변환
                                    if tx_timestamp > 1000000000000:  # 13자리 이상이면 밀리초
                                        tx_timestamp = tx_timestamp / 1000
                                    
                                    tx_time = datetime.fromtimestamp(tx_timestamp, tz=pytz.UTC)
                                    print(f"[Birdeye] 변환된 시간: {tx_time}")
                                    
                                    # 시작 시간 필터링
                                    if start_time:
                                        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                                        if tx_time < start_dt:
                                            print(f"[Birdeye] 시작 시간 이전 트랜잭션 제외: {tx_time} < {start_dt}")
                                            continue
                                    
                                    # 종료 시간 필터링
                                    if end_time:
                                        end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                                        if tx_time > end_dt:
                                            print(f"[Birdeye] 종료 시간 이후 트랜잭션 제외: {tx_time} > {end_dt}")
                                            continue
                                    
                                    # WSOL로 매수한 트랜잭션만 필터링
                                    token_in = tx.get("tokenIn", {})
                                    token_out = tx.get("tokenOut", {})
                                    
                                    # WSOL 매수 트랜잭션 확인
                                    if token_in.get("address") == "So11111111111111111111111111111111111111112":
                                        # 트랜잭션 데이터 정리
                                        transaction = {
                                            "signature": tx.get("signature", ""),
                                            "timestamp": tx_timestamp,
                                            "type": "SWAP",
                                            "token_transfers": [{
                                                "from": token_in.get("from", ""),
                                                "to": token_out.get("to", ""),
                                                "amount": float(token_in.get("amount", 0)),
                                                "mint": token_in.get("address", "")
                                            }],
                                            "native_balance_change": float(token_in.get("amount", 0)),
                                            "token_amount": float(token_out.get("amount", 0)),
                                            "token_price": float(token_in.get("amount", 0)) / float(token_out.get("amount", 1)) if float(token_out.get("amount", 0)) > 0 else 0
                                        }
                                        filtered_transactions.append(transaction)
                                        print(f"[Birdeye] WSOL 매수 트랜잭션 발견: {transaction['signature']}")
                                        print(f"  - 시간: {tx_time}")
                                        print(f"  - SOL 금액: {transaction['native_balance_change']}")
                                        print(f"  - 토큰 수량: {transaction['token_amount']}")
                                        print(f"  - 토큰 가격: {transaction['token_price']}")
                                    else:
                                        print(f"[Birdeye] WSOL 매수 트랜잭션이 아님: {token_in.get('address')}")
                                
                                all_transactions.extend(filtered_transactions)
                                offset += len(transactions)
                                
                                # 최대 1000개로 제한
                                if len(all_transactions) >= limit:
                                    all_transactions = all_transactions[:limit]
                                    print(f"[Birdeye] 최대 트랜잭션 수({limit})에 도달했습니다.")
                                    break
                                
                                # 필터링된 트랜잭션이 없으면 중단
                                if not filtered_transactions:
                                    print("[Birdeye] 필터링된 트랜잭션이 없습니다. 요청을 중단합니다.")
                                    break
                                
                                # 요청 속도 제한 준수
                                await self._wait_for_rate_limit()
                            
                            else:
                                error_msg = data.get("message", "Unknown error")
                                print(f"[Birdeye] API 응답 실패: {error_msg}")
                                break
                        else:
                            error_text = await response.text()
                            print(f"[Birdeye] API 에러 발생: {response.status}")
                            print(f"[Birdeye] 에러 메시지: {error_text}")
                            break
            
            print(f"[Birdeye] 트랜잭션 데이터 수신 완료: {len(all_transactions)}건")
            return all_transactions
            
        except Exception as e:
            print(f"[Birdeye] 예외 발생: {str(e)}")
            raise

    async def get_token_info(self, token_address: str) -> Optional[Dict[str, Any]]:
        """
        토큰의 기본 정보를 조회합니다.
        
        Args:
            token_address: SPL 토큰 주소
            
        Returns:
            Dict: 토큰 정보
        """
        try:
            url = f"{self.base_url}/defi/token_overview"
            params = {"address": token_address}
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, 
                    params=params, 
                    headers=self.headers,
                    ssl=self.ssl_context
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Birdeye API 에러: {response.status} - {error_text}")
                        return None
                        
                    data = await response.json()
                    if not data.get("success"):
                        logger.error(f"Birdeye API 응답 실패: {data}")
                        return None
                        
                    token_data = data.get("data", {})
                    return {
                        "address": token_address,
                        "name": token_data.get("name"),
                        "symbol": token_data.get("symbol"),
                        "price": token_data.get("price"),
                        "volume_24h": token_data.get("volume24h"),
                        "market_cap": token_data.get("marketCap")
                    }
                    
        except Exception as e:
            logger.error(f"Birdeye API 호출 중 에러 발생: {str(e)}")
            return None 