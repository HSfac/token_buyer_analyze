import aiohttp
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import pytz
import ssl
import json
import asyncio

logger = logging.getLogger(__name__)

class HeliusFetcher:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://mainnet.helius-rpc.com"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        # SSL 컨텍스트 설정
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    async def get_token_transactions(
        self,
        token_address: str,
        limit: int = 100,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        토큰의 트랜잭션 히스토리를 조회합니다.
        
        Args:
            token_address: SPL 토큰 주소
            limit: 조회할 최대 트랜잭션 수
            start_time: 시작 시간 (ISO 형식)
            end_time: 종료 시간 (ISO 형식)
            
        Returns:
            List[Dict]: 트랜잭션 목록
        """
        try:
            logger.info(f"트랜잭션 조회 시작: {token_address}")
            
            # RPC 요청 데이터
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    token_address,
                    {
                        "limit": limit,
                        "commitment": "confirmed"
                    }
                ]
            }
            
            logger.info(f"RPC 요청 데이터: {json.dumps(payload, indent=2)}")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.base_url,
                    json=payload,
                    headers=self.headers,
                    ssl=self.ssl_context
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Helius API 에러: {response.status} - {error_text}")
                        return []
                        
                    data = await response.json()
                    logger.info(f"RPC 응답 데이터: {json.dumps(data, indent=2)}")
                    
                    if "error" in data:
                        logger.error(f"Helius RPC 에러: {data['error']}")
                        return []
                        
                    signatures = data.get("result", [])
                    logger.info(f"조회된 트랜잭션 시그니처 수: {len(signatures)}")
                    
                    transactions = []
                    
                    # 각 트랜잭션의 상세 정보 조회 (Rate Limit 고려)
                    for i, sig in enumerate(signatures):
                        logger.info(f"트랜잭션 상세 정보 조회 중: {i+1}/{len(signatures)}")
                        
                        # Rate Limit 방지를 위한 지연
                        if i > 0 and i % 10 == 0:
                            await asyncio.sleep(1)
                            
                        max_retries = 3
                        retry_count = 0
                        
                        while retry_count < max_retries:
                            tx_data = await self._get_transaction_details(sig["signature"])
                            if tx_data:
                                if self._is_swap_event(tx_data):
                                    logger.info(f"Swap 이벤트 발견: {sig['signature']}")
                                    swap_info = self._extract_swap_info(tx_data)
                                    if swap_info and self._is_wsol_buy(swap_info, token_address):
                                        logger.info(f"WSOL 매수 트랜잭션 발견: {sig['signature']}")
                                        transactions.append({
                                            "signature": sig["signature"],
                                            "timestamp": sig.get("blockTime", 0),
                                            "buyer": swap_info.get("buyer"),
                                            "amount_sol": swap_info.get("amount_sol", 0)
                                        })
                                break
                            elif retry_count < max_retries - 1:
                                retry_count += 1
                                await asyncio.sleep(1)
                            else:
                                break
                                
                    logger.info(f"최종 필터링된 트랜잭션 수: {len(transactions)}")
                    return transactions
                    
        except Exception as e:
            logger.error(f"Helius API 호출 중 에러 발생: {str(e)}")
            return []
            
    async def _get_transaction_details(self, signature: str) -> Optional[Dict[str, Any]]:
        """트랜잭션 상세 정보를 조회합니다."""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": 0
                    }
                ]
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.base_url,
                    json=payload,
                    headers=self.headers,
                    ssl=self.ssl_context
                ) as response:
                    if response.status == 429:  # Rate Limit
                        logger.warning("Rate Limit 도달. 1초 대기...")
                        await asyncio.sleep(1)
                        return None
                    elif response.status != 200:
                        logger.error(f"트랜잭션 상세 정보 조회 실패: {response.status}")
                        return None
                        
                    data = await response.json()
                    if "error" in data:
                        logger.error(f"트랜잭션 상세 정보 RPC 에러: {data['error']}")
                        return None
                        
                    return data.get("result")
                    
        except Exception as e:
            logger.error(f"트랜잭션 상세 정보 조회 중 에러 발생: {str(e)}")
            return None
            
    def _is_swap_event(self, tx: Dict[str, Any]) -> bool:
        """트랜잭션이 Swap 이벤트인지 확인"""
        if not tx:
            return False
            
        # 주요 DEX 프로그램 ID 목록
        DEX_PROGRAMS = [
            "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB",  # Jupiter
            "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP",  # Orca
            "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium
            "27haf8L6oxUeXrHrgEgsexjSY5hbVUWEmvv9Nyxg8vQv"   # Serum
        ]
        
        instructions = tx.get("transaction", {}).get("message", {}).get("instructions", [])
        for ix in instructions:
            program_id = ix.get("program")
            if program_id in DEX_PROGRAMS:
                logger.info(f"DEX Swap 이벤트 발견: {program_id}")
                return True
                
        # 토큰 전송 확인
        token_transfers = tx.get("meta", {}).get("postTokenBalances", [])
        if token_transfers:
            logger.info("토큰 전송 이벤트 발견")
            return True
            
        return False
        
    def _extract_swap_info(self, tx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Swap 이벤트에서 필요한 정보 추출"""
        try:
            if not tx:
                return None
                
            # WSOL 주소
            WSOL_ADDRESS = "So11111111111111111111111111111111111111112"
            
            # 트랜잭션에서 WSOL 전송 정보 추출
            pre_balances = tx.get("meta", {}).get("preBalances", [])
            post_balances = tx.get("meta", {}).get("postBalances", [])
            
            if not pre_balances or not post_balances:
                logger.info("잔액 정보 없음")
                return None
                
            # WSOL 전송량 계산 (lamports)
            balance_change = post_balances[0] - pre_balances[0]
            if balance_change < 0:
                amount_sol = abs(balance_change) / 1e9  # lamports to SOL
                logger.info(f"WSOL 전송량 발견: {amount_sol} SOL")
                
                # 매수자 주소 추출
                account_keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
                buyer = None
                
                # 토큰 전송 정보 확인
                token_transfers = tx.get("meta", {}).get("postTokenBalances", [])
                for transfer in token_transfers:
                    if transfer.get("mint") == WSOL_ADDRESS:
                        buyer = transfer.get("owner")
                        break
                        
                if not buyer and len(account_keys) > 1:
                    buyer = account_keys[1]
                    
                if buyer:
                    return {
                        "buyer": buyer,
                        "amount_sol": amount_sol
                    }
                    
            return None
            
        except Exception as e:
            logger.error(f"Swap 정보 추출 중 에러 발생: {str(e)}")
            return None
            
    def _is_wsol_buy(self, swap_info: Dict[str, Any], target_token: str) -> bool:
        """WSOL로 토큰을 매수한 트랜잭션인지 확인"""
        is_valid = bool(swap_info.get("buyer") and swap_info.get("amount_sol", 0) > 0)
        if is_valid:
            logger.info(f"유효한 WSOL 매수 트랜잭션 발견: {swap_info.get('buyer')} - {swap_info.get('amount_sol')} SOL")
        return is_valid 