import aiohttp
import logging
from typing import List, Dict, Any, Optional, Tuple, AsyncIterator
from datetime import datetime
import pytz
import ssl
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

logger = logging.getLogger(__name__)

class RateLimiter:
    """API 요청 레이트 제한을 관리하는 클래스"""
    def __init__(self, max_calls: int, period: float = 1.0):
        self.max_calls = max_calls  # 주어진 기간 내 최대 요청 수
        self.period = period  # 기간 (초)
        self.calls = []  # 요청 타임스탬프 기록
        self.semaphore = asyncio.Semaphore(max_calls)  # 동시 요청 제한
        
    async def acquire(self):
        """요청 가능할 때까지 대기"""
        now = time.time()
        # 만료된 요청 제거
        self.calls = [call for call in self.calls if call > now - self.period]
        
        if len(self.calls) >= self.max_calls:
            # 대기 시간 계산
            sleep_time = self.period - (now - self.calls[0])
            if sleep_time > 0:
                logger.info(f"레이트 제한 도달: {sleep_time:.2f}초 대기")
                await asyncio.sleep(sleep_time)
                
        # 세마포어 획득
        await self.semaphore.acquire()
        
        # 요청 시간 기록
        self.calls.append(time.time())
        
    def release(self):
        """요청 완료 후 세마포어 해제"""
        self.semaphore.release()

class HeliusFetcher:
    def __init__(self, api_key: str, plan: str = "developer"):
        self.api_key = api_key
        self.base_url = "https://mainnet.helius-rpc.com"
        self.api_url = "https://api.helius.xyz/v0" 
        self.headers = {
            "Content-Type": "application/json"
        }
        # SSL 컨텍스트 설정
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        
        # aiohttp 세션 초기화
        self.session = aiohttp.ClientSession(
            headers=self.headers,
            timeout=aiohttp.ClientTimeout(total=60)
        )
        
        # 요금제별 레이트 리미터 설정
        if plan.lower() == "professional":
            self.rate_limiter = RateLimiter(50)  # 초당 50 요청
            self.max_batch_size = 1000  # 요청당 최대 결과 수
        elif plan.lower() == "business":
            self.rate_limiter = RateLimiter(20)  # 초당 20 요청
            self.max_batch_size = 1000
        elif plan.lower() == "free":
            self.rate_limiter = RateLimiter(2)  # 초당 2 요청
            self.max_batch_size = 500  # 배치 크기 증가
        else:  # developer 플랜 (기본값)
            self.rate_limiter = RateLimiter(10)  # 초당 10 요청
            self.max_batch_size = 500  # 배치 크기 증가
            
        # 캐시 설정
        self._cache = {}
        self._cache_ttl = 0  # 캐시 비활성화
        
        # 성능 모니터링
        self._metrics = {"latency": [], "requests": 0, "success": 0, "errors": 0}
        self._cache_stats = {"hits": 0, "misses": 0, "total": 0}

    async def get_token_transactions(
        self,
        token_address: str,
        limit: int = 100,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        use_enhanced_api: bool = True,
        batch_size: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """토큰 트랜잭션 조회"""
        logger.info(f"트랜잭션 조회 시작: {token_address}, 제한: {limit}, 배치 크기: {batch_size}")
        logger.info(f"시간 범위: {start_time} ~ {end_time}")
        
        # 배치 크기 설정
        if batch_size is None:
            batch_size = self.max_batch_size

        # 캐시 TTL 활성화 (기본 10분)
        self._cache_ttl = 600
        
        # 캐시 키 생성
        cache_key = f"{token_address}_{limit}_{start_time}_{end_time}_{batch_size}"
        logger.info(f"캐시 키: {cache_key}")
        
        # 캐시 통계 업데이트
        self._cache_stats["total"] += 1
        
        # 캐시 확인
        if cache_key in self._cache:
            cache_data = self._cache[cache_key]
            if time.time() - cache_data['timestamp'] < self._cache_ttl:
                logger.info(f"캐시 히트: {len(cache_data['data'])}개 트랜잭션 반환")
                self._cache_stats["hits"] += 1
                return cache_data['data']
            else:
                logger.info("캐시 만료")
        
        # 성능 모니터링 시작
        start_time_perf = time.time()
        self._metrics["requests"] += 1
        
        # 병렬 처리를 위한 배치 수 계산
        num_batches = (limit + batch_size - 1) // batch_size
        max_concurrent = min(num_batches, 5)  # 최대 5개 동시 요청으로 제한
        
        logger.info(f"병렬 처리: {num_batches}개 배치, 최대 {max_concurrent}개 동시 요청")
        
        # 병렬로 배치 요청 실행
        semaphore = asyncio.Semaphore(max_concurrent)
        tasks = []
        
        async def fetch_batch_with_semaphore(batch_idx):
            async with semaphore:
                offset = batch_idx * batch_size
                current_limit = min(batch_size, limit - offset)
                logger.info(f"배치 {batch_idx+1}/{num_batches} 요청: {current_limit}개 (오프셋: {offset})")
                
                try:
                    batch, next_cursor, total_count = await self._fetch_single_batch(
                        token_address, current_limit, batch_idx+1,
                        start_time, end_time, use_enhanced_api
                    )
                    # WSOL 매수 트랜잭션 필터링
                    filtered_batch = await self._filter_wsol_buys(batch, token_address)
                    logger.info(f"배치 {batch_idx+1} 필터링 완료: {len(filtered_batch)}/{len(batch)}개 트랜잭션")
                    return filtered_batch
                except Exception as e:
                    logger.error(f"배치 {batch_idx+1} 처리 중 오류: {str(e)}")
                    return []
        
        # 배치 요청 작업 생성
        for i in range(num_batches):
            tasks.append(fetch_batch_with_semaphore(i))
        
        # 모든 배치 요청 실행 및 결과 수집
        batch_results = await asyncio.gather(*tasks)
        
        # 결과 병합
        results = []
        for batch in batch_results:
            results.extend(batch)
        
        # 목표 개수 초과시 잘라내기
        if len(results) > limit:
            results = results[:limit]
        
        # 성능 측정 종료
        end_time_perf = time.time()
        latency = end_time_perf - start_time_perf
        self._metrics["latency"].append(latency)
        self._metrics["success"] += 1
        
        # 결과 캐싱
        self._cache[cache_key] = {
            'timestamp': time.time(),
            'data': results
        }
        
        logger.info(f"최종 필터링된 트랜잭션 수: {len(results)}, 소요 시간: {latency:.2f}초")
        if len(results) > 0:
            logger.info(f"처리 속도: {len(results)/latency:.1f}개/초")
        
        return results
                    
    async def _fetch_transaction_batches(
        self, 
        token_address: str, 
        limit: int, 
        batch_size: int,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        use_enhanced_api: bool = True
    ) -> AsyncIterator[List[Dict[str, Any]]]:
        """트랜잭션 배치 조회"""
        logger.info(f"배치 조회 시작: {token_address}, 제한: {limit}, 배치 크기: {batch_size}")
        
        total_fetched = 0
        page = 1
        
        while total_fetched < limit:
            current_batch_size = min(batch_size, limit - total_fetched)
            logger.info(f"배치 {page} 요청: {current_batch_size}개 트랜잭션")
            
            try:
                batch, next_cursor, total_count = await self._fetch_single_batch(
                    token_address, current_batch_size, page,
                    start_time, end_time, use_enhanced_api
                )
                
                if not batch:
                    logger.info("더 이상 트랜잭션이 없음")
                    break
                    
                logger.info(f"배치 {page} 수신: {len(batch)}개 트랜잭션")
                total_fetched += len(batch)
                logger.info(f"현재까지 {total_fetched}/{limit}개 트랜잭션 수집 ({total_fetched/limit*100:.1f}%)")
                
                yield batch
                page += 1
                
            except Exception as e:
                logger.error(f"배치 {page} 조회 실패: {str(e)}")
                break

    async def _fetch_single_batch(
        self, 
        token_address: str,
        batch_size: int,
        page: int,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        use_enhanced_api: bool = True
    ) -> Tuple[List[Dict[str, Any]], Optional[str], int]:
        """단일 배치의 트랜잭션을 가져옵니다"""
        url = f"{self.api_url}/addresses/{token_address}/transactions"
        
        # Helius API 요청당 최대 100개로 제한되므로, 큰 배치는 여러 요청으로 분할
        if batch_size > 100:
            # 분할 요청 (100개씩)
            num_requests = (batch_size + 99) // 100
            tasks = []
            
            for i in range(num_requests):
                sub_limit = min(100, batch_size - i * 100)
                
                # 하위 요청 생성
                async def fetch_sub_batch(idx, limit):
                    sub_params = {
                        "api-key": self.api_key,
                        "type": "SWAP",
                        "limit": limit
                    }
                    
                    if start_time:
                        sub_params["startTime"] = start_time
                    if end_time:
                        sub_params["endTime"] = end_time
                    
                    # 이전 페이지 건너뛰기 위한 커서 계산 (구현 필요시)
                    
                    safe_params = sub_params.copy()
                    if "api-key" in safe_params:
                        safe_params["api-key"] = "***"
                    logger.info(f"페이지 {page}-{idx+1} 요청: {url}?{safe_params}, 배치 크기: {limit}")
                    
                    # 레이트 제한 준수
                    await self.rate_limiter.acquire()
                    
                    try:
                        async with aiohttp.ClientSession() as session:
                            start_request = time.time()
                            async with session.get(
                                url,
                                params=sub_params,
                                headers=self.headers,
                                ssl=self.ssl_context,
                                timeout=60
                            ) as response:
                                request_time = time.time() - start_request
                                if response.status == 200:
                                    data = await response.json()
                                    logger.info(f"응답 시간: {request_time:.2f}초, 데이터 크기: {len(str(data))} 바이트")
                                    return data
                                else:
                                    logger.error(f"API 오류 응답: {response.status} - {await response.text()}")
                                    return []
                    except Exception as e:
                        logger.error(f"API 요청 중 오류: {str(e)}")
                        return []
                    finally:
                        self.rate_limiter.release()
                
                tasks.append(fetch_sub_batch(i, sub_limit))
            
            # 병렬로 모든 하위 요청 실행
            sub_results = await asyncio.gather(*tasks)
            
            # 결과 병합
            combined_data = []
            for result in sub_results:
                if isinstance(result, list) and result:
                    combined_data.extend(result)
                elif isinstance(result, dict) and "data" in result:
                    combined_data.extend(result["data"])
            
            return combined_data, None, len(combined_data)
        
        # 기존 단일 요청 로직 (배치 크기 <= 100)
        api_limit = min(batch_size, 100)
        
        params = {
            "api-key": self.api_key,
            "type": "SWAP",
            "limit": api_limit
        }
        
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
            
        safe_params = params.copy()
        if "api-key" in safe_params:
            safe_params["api-key"] = "***"
        logger.info(f"페이지 {page} 요청: {url}?{safe_params}, 배치 크기: {api_limit}")
        
        try:
            # 레이트 제한 준수
            await self.rate_limiter.acquire()
            
            try:
                async with aiohttp.ClientSession() as session:
                    start_request = time.time()
                    async with session.get(
                        url,
                        params=params,
                        headers=self.headers,
                        ssl=self.ssl_context,
                        timeout=60
                    ) as response:
                        request_time = time.time() - start_request
                        if response.status == 200:
                            data = await response.json()
                            logger.info(f"응답 시간: {request_time:.2f}초, 데이터 형식: {type(data)}")
                            
                            if isinstance(data, list):
                                logger.info(f"리스트 형태의 응답 수신 (길이: {len(data)})")
                                return data, None, len(data)
                            elif isinstance(data, dict) and "data" in data:
                                logger.info(f"객체 형태의 응답 수신 (데이터 길이: {len(data['data'])})")
                                return data["data"], data.get("nextCursor"), data.get("total", len(data["data"]))
                            else:
                                logger.warning(f"예상치 못한 응답 형식: {type(data)}")
                                return [], None, 0
                        else:
                            error_text = await response.text()
                            logger.error(f"API 오류 응답: {response.status} - {error_text}")
                            raise Exception(f"API 오류: {response.status}")
            except aiohttp.ClientError as e:
                logger.error(f"HTTP 요청 실패: {str(e)}")
                raise
        finally:
            self.rate_limiter.release()

    async def _filter_wsol_buys(self, transactions: List[Dict[str, Any]], token_address: str) -> List[Dict[str, Any]]:
        """Enhanced Transaction API에서 WSOL 매수 트랜잭션만 필터링"""
        WSOL_ADDRESS = "So11111111111111111111111111111111111111112"
        filtered = []
        
        # 유효한 트랜잭션만 필터링
        valid_transactions = []
        for tx in transactions:
            if isinstance(tx, dict):
                valid_transactions.append(tx)
            elif isinstance(tx, str):
                try:
                    valid_transactions.append(json.loads(tx))
                except json.JSONDecodeError:
                    logger.error(f"잘못된 형식의 트랜잭션 데이터: {tx[:100]}...")
            else:
                logger.error(f"지원되지 않는 트랜잭션 데이터 타입: {type(tx)}")
        
        if not valid_transactions:
            logger.warning("처리할 유효한 트랜잭션이 없습니다.")
            return []
        
        # 첫 번째 트랜잭션의 구조 로깅 (디버깅용)
        if valid_transactions:
            first_tx = valid_transactions[0]
            logger.info(f"첫 번째 트랜잭션 구조: {json.dumps(first_tx, indent=2)[:500]}...")
            
            # description 필드에서 토큰 정보 추출 시도
            if "description" in first_tx:
                desc = first_tx["description"]
                logger.info(f"트랜잭션 설명: {desc}")
        
        for tx in valid_transactions:
            try:
                # 트랜잭션 구조 로깅 (디버깅용)
                logger.debug(f"트랜잭션 처리: type={type(tx)}, keys={list(tx.keys()) if isinstance(tx, dict) else 'N/A'}")
                
                # 기본 정보 추출
                signature = tx.get("signature", "") if isinstance(tx, dict) else ""
                timestamp = tx.get("timestamp", 0) if isinstance(tx, dict) else 0
                buyer = tx.get("feePayer", "") if isinstance(tx, dict) else ""
                
                # 트랜잭션 타입 체크
                if not isinstance(tx, dict):
                    continue
                    
                tx_type = tx.get("type", "")
                if tx_type != "SWAP":
                    logger.debug(f"SWAP이 아닌 트랜잭션 타입: {tx_type}")
                    continue
                
                # description에서 토큰 정보 추출 시도
                description = tx.get("description", "")
                if description:
                    logger.debug(f"트랜잭션 설명: {description}")
                    # "swapped X SOL for Y TOKEN" 또는 "swapped Y TOKEN for X SOL" 패턴 확인
                    if "swapped" in description and "SOL" in description:
                        parts = description.split("swapped")
                        if len(parts) == 2:
                            swap_info = parts[1].strip()
                            logger.debug(f"스왑 정보: {swap_info}")
                            
                            # SOL이 첫 번째 토큰인 경우 (SOL로 구매)
                            if swap_info.startswith("0.") or swap_info.startswith("1.") or swap_info.startswith("2.") or swap_info.startswith("3.") or swap_info.startswith("4.") or swap_info.startswith("5.") or swap_info.startswith("6.") or swap_info.startswith("7.") or swap_info.startswith("8.") or swap_info.startswith("9."):
                                if token_address in swap_info:
                                    try:
                                        # SOL 금액 추출
                                        sol_amount = float(swap_info.split()[0])
                                        filtered.append({
                                            "signature": signature,
                                            "timestamp": timestamp / 1000 if timestamp else 0,
                                            "buyer": buyer,
                                            "amount_sol": sol_amount,
                                            "source": tx.get("source", "unknown")
                                        })
                                        logger.info(f"description에서 WSOL 구매 발견: {signature}, 금액: {sol_amount} SOL")
                                        continue
                                    except (ValueError, IndexError) as e:
                                        logger.warning(f"SOL 금액 추출 실패: {e}")
                
                # 이벤트 데이터 추출
                events = tx.get("events", [])
                if not events:
                    # 이벤트가 없는 경우 다른 필드에서 정보 추출 시도
                    instructions = tx.get("instructions", [])
                    token_transfers = tx.get("tokenTransfers", [])
                    
                    # 토큰 이체 정보에서 WSOL 구매 여부 확인
                    for transfer in token_transfers:
                        if not isinstance(transfer, dict):
                            continue
                            
                        token_address_from_transfer = transfer.get("mint", "")
                        if token_address_from_transfer == token_address:
                            filtered.append({
                                "signature": signature,
                                "timestamp": timestamp / 1000 if timestamp else 0,
                                "buyer": buyer,
                                "amount_sol": 0,  # 정확한 금액 정보 없음
                                "source": tx.get("source", "unknown")
                            })
                            logger.info(f"토큰 이체에서 WSOL 구매 발견: {signature}")
                            break
                    continue
                
                # 이벤트 기반 처리
                for event in events:
                    if not isinstance(event, dict):
                        continue
                        
                    event_type = event.get("type", "")
                    logger.debug(f"이벤트 타입: {event_type}")
                    
                    if event_type == "SWAP":
                        swap_info = event.get("swap", {})
                        if not swap_info or not isinstance(swap_info, dict):
                            continue
                            
                        # 토큰 정보 추출
                        token_in = None
                        token_out = None
                        amount_in_raw = 0
                        
                        # 다양한 API 응답 구조 처리
                        if "tokenIn" in swap_info and "tokenOut" in swap_info:
                            token_in_info = swap_info.get("tokenIn", {})
                            token_out_info = swap_info.get("tokenOut", {})
                            
                            if isinstance(token_in_info, dict):
                                token_in = token_in_info.get("mint", "")
                                amount_in_raw = token_in_info.get("amount", 0)
                            else:
                                continue
                                
                            if isinstance(token_out_info, dict):
                                token_out = token_out_info.get("mint", "")
                            else:
                                continue
                                
                        elif "sourceMint" in swap_info and "destinationMint" in swap_info:
                            token_in = swap_info.get("sourceMint", "")
                            token_out = swap_info.get("destinationMint", "")
                            amount_in_raw = swap_info.get("sourceAmount", 0)
                        elif "fromMint" in swap_info and "toMint" in swap_info:
                            token_in = swap_info.get("fromMint", "")
                            token_out = swap_info.get("toMint", "")
                            amount_in_raw = swap_info.get("fromAmount", 0)
                        else:
                            logger.warning(f"알 수 없는 SWAP 구조: {swap_info}")
                            continue
                        
                        logger.debug(f"SWAP 정보: token_in={token_in}, token_out={token_out}, amount={amount_in_raw}")
                        
                        # WSOL로 대상 토큰을 구매한 경우
                        if token_in == WSOL_ADDRESS and token_out == token_address:
                            try:
                                amount_in_sol = float(amount_in_raw) / 1e9
                            except (ValueError, TypeError):
                                amount_in_sol = 0
                                logger.warning(f"금액 변환 실패: {amount_in_raw}")
                            
                            if not buyer and "accounts" in tx:
                                accounts = tx.get("accounts", [])
                                buyer = accounts[0] if accounts and isinstance(accounts, list) and len(accounts) > 0 else None
                            
                            if buyer:
                                filtered.append({
                                    "signature": signature,
                                    "timestamp": timestamp / 1000 if timestamp else 0,
                                    "buyer": buyer,
                                    "amount_sol": amount_in_sol,
                                    "source": tx.get("source", "unknown")
                                })
                                logger.info(f"WSOL 구매 트랜잭션 발견: {signature}, 금액: {amount_in_sol} SOL")
                                break
            except Exception as e:
                logger.error(f"트랜잭션 처리 중 에러: {str(e)}, 트랜잭션 타입: {type(tx)}")
                if isinstance(tx, dict):
                    logger.error(f"트랜잭션 키: {list(tx.keys())}")
                    for key, value in tx.items():
                        if key in ["events", "instructions", "tokenTransfers"]:
                            logger.error(f"{key} 구조: {type(value)}, 길이: {len(value) if isinstance(value, (list, dict)) else 'N/A'}")
                            if isinstance(value, list) and len(value) > 0:
                                logger.error(f"첫 번째 {key} 항목: {value[0]}")
        
        logger.info(f"필터링 결과: {len(filtered)}개 WSOL 구매 트랜잭션 발견")
        return filtered
        
    async def get_parsed_transactions_batch(self, signatures: List[str]) -> List[Dict[str, Any]]:
        """여러 트랜잭션을 병렬로 Enhanced API로 파싱"""
        if not signatures:
            return []
            
        url = f"{self.api_url}/transactions"
        
        # 배치 크기 제한 (API 제한에 맞춤)
        max_batch_size = 100
        all_results = []
        
        # 시그니처를 배치로 나누기
        for i in range(0, len(signatures), max_batch_size):
            batch = signatures[i:i + max_batch_size]
            
            # API 요청 파라미터 설정 (Helius 문서 형식에 맞춤)
            params = {
                "api-key": self.api_key,
                "commitment": "confirmed"
            }
            
            payload = {
                "transactions": batch,
                "encoding": "jsonParsed"
            }
            
            try:
                # 레이트 제한 준수
                await self.rate_limiter.acquire()
                
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            url,
                            params=params,
                            json=payload,
                            headers=self.headers,
                            ssl=self.ssl_context,
                            timeout=30
                        ) as response:
                            if response.status != 200:
                                error_text = await response.text()
                                logger.error(f"Helius Enhanced API 에러: {response.status} - {error_text}")
                                continue
                        
                    data = await response.json()
                    all_results.extend(data)
                except Exception as e:
                    logger.error(f"트랜잭션 일괄 파싱 중 에러: {str(e)}")
                finally:
                    # 세마포어 해제
                    self.rate_limiter.release()
            except Exception as e:
                logger.error(f"트랜잭션 일괄 파싱 중 에러: {str(e)}")
                
        return all_results
            
    async def stream_transactions(self, token_address: str, callback):
        """
        웹소켓을 사용하여 실시간 트랜잭션 스트리밍
        비즈니스/프로페셔널 플랜에서만 사용 가능
        """
        # API 키를 쿼리 파라미터로 전달 (Helius 문서 형식에 맞춤)
        ws_url = f"wss://api.helius.xyz/v0/wallet-transactions/{token_address}?api-key={self.api_key}"
        
        async with aiohttp.ClientSession() as session:
            try:
                # 웹소켓 연결 
                async with session.ws_connect(ws_url, ssl=self.ssl_context, heartbeat=30) as ws:
                    logger.info(f"{token_address}에 대한 트랜잭션 스트리밍 시작")
                    
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                
                                # 데이터 형식 확인
                                if not isinstance(data, dict):
                                    logger.warning(f"웹소켓에서 예상치 못한 데이터 형식 수신: {type(data)}")
                                    continue
                                
                                # WSOL 매수 트랜잭션 필터링
                                filtered = await self._filter_wsol_buys([data], token_address)
                                
                                if filtered:
                                    await callback(filtered[0])
                            except json.JSONDecodeError:
                                logger.error(f"웹소켓 데이터 파싱 에러: {msg.data[:100]}...")
                                continue
                                
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.error(f"웹소켓 에러: {msg}")
                        break
                    
            except Exception as e:
                logger.error(f"웹소켓 연결 중 에러: {str(e)}")
                # 재연결 로직
                await asyncio.sleep(5)
                await self.stream_transactions(token_address, callback)
    
    async def setup_webhook(self, token_address: str, webhook_url: str) -> Dict[str, Any]:
        """
        토큰 주소에 대한 웹훅 설정
        """
        url = f"{self.api_url}/webhooks"
        
        # API 요청 파라미터 설정 (Helius 문서 형식에 맞춤)
        params = {
            "api-key": self.api_key
        }
        
        payload = {
            "webhookURL": webhook_url,
            "transactionTypes": ["SWAP"],
            "accountAddresses": [token_address],
            "webhookType": "enhanced"
        }
        
        try:
            # 레이트 제한 준수
            await self.rate_limiter.acquire()
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        params=params,
                        json=payload,
                        headers=self.headers,
                        ssl=self.ssl_context
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            logger.error(f"웹훅 설정 에러: {response.status} - {error_text}")
                            return {"success": False, "error": error_text}
                            
                        data = await response.json()
                        logger.info(f"웹훅 설정 성공: {data}")
                        return {"success": True, "data": data}
            finally:
                # 세마포어 해제
                self.rate_limiter.release()
            
        except Exception as e:
            logger.error(f"웹훅 설정 중 에러: {str(e)}")
            return {"success": False, "error": str(e)}
            
    def get_performance_metrics(self) -> Dict[str, Any]:
        """성능 메트릭 정보 반환"""
        avg_latency = sum(self._metrics["latency"]) / len(self._metrics["latency"]) if self._metrics["latency"] else 0
        
        return {
            "requests": {
                "total": self._metrics["requests"],
                "success": self._metrics["success"],
                "errors": self._metrics["errors"]
            },
            "latency": {
                "average": avg_latency,
                "samples": self._metrics["latency"][-10:] if len(self._metrics["latency"]) > 10 else self._metrics["latency"]
            },
            "cache": {
                "hits": self._cache_stats["hits"],
                "misses": self._cache_stats["misses"],
                "hit_ratio": self._cache_stats["hits"] / self._cache_stats["total"] if self._cache_stats["total"] > 0 else 0
            }
        }
        
    def clear_cache(self):
        """캐시 초기화"""
        self._cache.clear()
        logger.info("Helius 클라이언트 캐시가 초기화되었습니다.") 