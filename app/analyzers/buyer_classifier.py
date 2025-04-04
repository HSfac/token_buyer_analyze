from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from app.config import settings, SOL_RANGES
from app.models.types import Transaction, BuyerRange, BuyerAnalysis, TimeRange, WalletTransaction, WalletSummary, TransactionType
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

logger = logging.getLogger(__name__)

class BuyerClassifier:
    def __init__(self):
        self.ranges = {
            "0_1": {"wallets": [], "count": 0, "total_sol": 0},
            "1_5": {"wallets": [], "count": 0, "total_sol": 0},
            "5_10": {"wallets": [], "count": 0, "total_sol": 0},
            "10_plus": {"wallets": [], "count": 0, "total_sol": 0}
        }
        self.sol_ranges = SOL_RANGES
        self.logger = logging.getLogger(__name__)

    def _create_time_intervals(self, start_time: datetime, end_time: datetime, interval_seconds: int) -> List[datetime]:
        """주어진 시간 범위를 일정한 간격으로 나눕니다."""
        intervals = []
        current_time = start_time
        while current_time <= end_time:
            intervals.append(current_time)
            current_time += timedelta(seconds=interval_seconds)
        return intervals

    def _get_interval_index(self, timestamp: float, intervals: List[datetime]) -> Optional[int]:
        """타임스탬프가 속하는 시간 간격의 인덱스를 반환합니다."""
        tx_time = datetime.fromtimestamp(timestamp)
        for i in range(len(intervals) - 1):
            if intervals[i] <= tx_time < intervals[i + 1]:
                return i
        return None

    async def analyze_transactions(
        self,
        transactions: List[Transaction],
        start_time: datetime,
        end_time: datetime,
        interval_seconds: int = 30
    ) -> BuyerAnalysis:
        """
        트랜잭션 목록을 분석하여 매수자 정보를 추출합니다.
        비동기 처리로 성능 개선
        """
        # 시작 시간 기록
        start_process_time = time.time()
        self.logger.info(f"분석 시작: {len(transactions)}개 트랜잭션")
        
        # 시간 간격별 데이터 초기화
        time_intervals = self._create_time_intervals(start_time, end_time, interval_seconds)
        volumes = [0] * len(time_intervals)
        buy_volumes = [0] * len(time_intervals)
        sell_volumes = [0] * len(time_intervals)
        timestamps = [interval.isoformat() for interval in time_intervals]

        # 병렬 처리를 위한 작업 분할
        wallet_transactions = await self._aggregate_wallet_transactions(transactions)
        
        # 시간 간격별 거래량을 병렬로 계산
        await self._calculate_volumes(transactions, time_intervals, volumes, buy_volumes, sell_volumes)

        # SOL 금액 범위별 지갑 분류 (병렬 처리)
        buyers_by_sol_range = await self._classify_by_sol_range(wallet_transactions)
        
        # 총 매수량/매도량 계산
        total_buy = sum(tx.amount_sol for tx in transactions if tx.buyer)
        total_sell = sum(tx.amount_sol for tx in transactions if tx.seller)
        
        # 지갑 요약 정보 생성
        wallet_summaries = await self._create_wallet_summaries(wallet_transactions)
        
        # 결과 생성
        result = BuyerAnalysis(
            timestamps=timestamps,
            volumes=volumes,
            buy_volumes=buy_volumes,
            sell_volumes=sell_volumes,
            buyers_by_sol_range=buyers_by_sol_range,
            wallet_summaries=wallet_summaries,
            token=transactions[0].token if transactions else "",
            snapshot_time=datetime.now().isoformat(),
            time_range=TimeRange(start_time=start_time, end_time=end_time),
            total_buy_volume=total_buy,
            total_sell_volume=total_sell,
            net_buy_volume=total_buy - total_sell,
            unique_buyers=len(set(tx.buyer for tx in transactions if tx.buyer)),
            unique_sellers=len(set(tx.seller for tx in transactions if tx.seller))
        )
        
        # 처리 시간 기록
        end_process_time = time.time()
        self.logger.info(f"분석 완료: 소요 시간 {end_process_time - start_process_time:.2f}초")
        
        return result

    async def _aggregate_wallet_transactions(self, transactions: List[Transaction]) -> Dict[str, List[Transaction]]:
        """지갑별 트랜잭션을 집계합니다."""
        wallet_transactions: Dict[str, List[Transaction]] = {}
        
        # 스레드 풀에서 실행할 작업
        def process_transaction(tx):
            result = {}
            if tx.buyer:
                if tx.buyer not in result:
                    result[tx.buyer] = []
                result[tx.buyer].append(tx)
            if tx.seller:
                if tx.seller not in result:
                    result[tx.seller] = []
                result[tx.seller].append(tx)
            return result
        
        # 트랜잭션을 배치로 나누기
        batch_size = 100
        batches = [transactions[i:i + batch_size] for i in range(0, len(transactions), batch_size)]
        
        # 각 배치를 병렬로 처리
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            tasks = []
            for batch in batches:
                tasks.append(loop.run_in_executor(
                    executor, 
                    lambda b=batch: {tx.buyer: [tx] for tx in b if tx.buyer}
                ))
            
            # 모든 작업 완료 대기
            results = await asyncio.gather(*tasks)
            
            # 결과 병합
            for result in results:
                for wallet, txs in result.items():
                    if wallet not in wallet_transactions:
                        wallet_transactions[wallet] = []
                    wallet_transactions[wallet].extend(txs)
        
        return wallet_transactions

    async def _calculate_volumes(self, transactions: List[Transaction], time_intervals: List[datetime], 
                                volumes: List[float], buy_volumes: List[float], sell_volumes: List[float]):
        """시간 간격별 거래량을 계산합니다."""
        
        # 스레드 풀에서 실행할 작업
        def process_batch(batch):
            batch_volumes = [0] * len(time_intervals)
            batch_buy_volumes = [0] * len(time_intervals)
            batch_sell_volumes = [0] * len(time_intervals)
            
            for tx in batch:
                interval_index = self._get_interval_index(tx.timestamp, time_intervals)
                if interval_index is not None:
                    batch_volumes[interval_index] += tx.amount_sol
                    if tx.buyer:
                        batch_buy_volumes[interval_index] += tx.amount_sol
                    if tx.seller:
                        batch_sell_volumes[interval_index] += tx.amount_sol
            
            return batch_volumes, batch_buy_volumes, batch_sell_volumes
        
        # 트랜잭션을 배치로 나누기
        batch_size = 100
        batches = [transactions[i:i + batch_size] for i in range(0, len(transactions), batch_size)]
        
        # 각 배치를 병렬로 처리
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            tasks = []
            for batch in batches:
                tasks.append(loop.run_in_executor(executor, process_batch, batch))
            
            # 모든 작업 완료 대기
            results = await asyncio.gather(*tasks)
            
            # 결과 병합
            for batch_volumes, batch_buy_volumes, batch_sell_volumes in results:
                for i in range(len(time_intervals)):
                    volumes[i] += batch_volumes[i]
                    buy_volumes[i] += batch_buy_volumes[i]
                    sell_volumes[i] += batch_sell_volumes[i]

    async def _classify_by_sol_range(self, wallet_transactions: Dict[str, List[Transaction]]) -> Dict[str, BuyerRange]:
        """SOL 금액 범위별 지갑을 분류합니다."""
        buyers_by_sol_range: Dict[str, BuyerRange] = {}
        
        # 스레드 풀에서 실행할 작업
        def process_range(range_name, min_sol, max_sol):
            range_wallets = []
            range_transactions = []
            
            for wallet, txs in wallet_transactions.items():
                total_buy = sum(tx.amount_sol for tx in txs if hasattr(tx, 'buyer') and tx.buyer == wallet)
                if min_sol <= total_buy < max_sol:
                    range_wallets.append(wallet)
                    range_transactions.extend(txs)
            
            return range_name, BuyerRange(
                wallets=range_wallets,
                count=len(range_wallets),
                total_sol=sum(tx.amount_sol for tx in range_transactions if hasattr(tx, 'buyer') and tx.buyer in range_wallets)
            )
        
        # 각 SOL 범위를 병렬로 처리
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            tasks = []
            for range_name, (min_sol, max_sol) in self.sol_ranges.items():
                tasks.append(loop.run_in_executor(executor, process_range, range_name, min_sol, max_sol))
            
            # 모든 작업 완료 대기
            results = await asyncio.gather(*tasks)
            
            # 결과 병합
            for range_name, buyer_range in results:
                buyers_by_sol_range[range_name] = buyer_range
        
        return buyers_by_sol_range

    async def _create_wallet_summaries(self, wallet_transactions: Dict[str, List[Transaction]]) -> Dict[str, WalletSummary]:
        """지갑별 요약 정보를 생성합니다."""
        wallet_summaries = {}
        
        # 스레드 풀에서 실행할 작업
        def process_wallet(wallet, txs):
            total_buy = sum(tx.amount_sol for tx in txs if hasattr(tx, 'buyer') and tx.buyer == wallet)
            total_sell = sum(tx.amount_sol for tx in txs if hasattr(tx, 'seller') and tx.seller == wallet)
            
            buy_txs = [tx for tx in txs if hasattr(tx, 'buyer') and tx.buyer == wallet]
            sell_txs = [tx for tx in txs if hasattr(tx, 'seller') and tx.seller == wallet]
            
            first_buy = min(buy_txs, key=lambda tx: tx.timestamp).timestamp if buy_txs else None
            first_sell = min(sell_txs, key=lambda tx: tx.timestamp).timestamp if sell_txs else None
            
            return wallet, WalletSummary(
                wallet_address=wallet,
                total_buy=total_buy,
                total_sell=total_sell,
                net_position=total_buy - total_sell,
                transaction_count=len(txs),
                first_buy_time=first_buy,
                first_sell_time=first_sell,
                is_hodler=total_buy > 0 and total_sell == 0
            )
        
        # 각 지갑을 병렬로 처리
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            tasks = []
            for wallet, txs in wallet_transactions.items():
                tasks.append(loop.run_in_executor(executor, process_wallet, wallet, txs))
            
            # 모든 작업 완료 대기
            results = await asyncio.gather(*tasks)
            
            # 결과 병합
            for wallet, summary in results:
                wallet_summaries[wallet] = summary
        
        return wallet_summaries

    async def classify_buyers(self, transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """매수자들을 SOL 범위별로 분류 (비동기 처리)"""
        try:
            # 시작 시간 기록
            start_process_time = time.time()
            self.logger.info(f"매수자 분류 시작: {len(transactions)}개 트랜잭션")
            
            # 지갑별 매수량 집계 (병렬 처리)
            wallet_purchases = {}
            
            # 트랜잭션을 배치로 나누기
            batch_size = 100
            batches = [transactions[i:i + batch_size] for i in range(0, len(transactions), batch_size)]
            
            # 각 배치를 병렬로 처리
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as executor:
                def process_batch(batch):
                    batch_purchases = {}
                    for tx in batch:
                        # 다양한 구조에서 buyer 추출
                        buyer = None
                        amount_sol = 0
                        
                        if isinstance(tx, dict):
                            # 다양한 필드 이름 확인
                            buyer_field_names = ['buyer', 'feePayer', 'fromUserAccount']
                            for field in buyer_field_names:
                                if field in tx and tx[field]:
                                    buyer = tx[field]
                                    break
                                    
                            # 금액 필드 이름 확인
                            amount_field_names = ['amount_sol', 'amountSol', 'amount']
                            for field in amount_field_names:
                                if field in tx:
                                    amount = tx.get(field, 0)
                                    if amount:
                                        try:
                                            amount_sol = float(amount)
                                            break
                                        except (ValueError, TypeError):
                                            continue
                        
                        # 구매자와 금액이 있으면 집계
                        if buyer and amount_sol > 0:
                            if isinstance(buyer, dict):
                                buyer = buyer.get('pubkey', buyer.get('address', None))
                            
                            if isinstance(buyer, str) and buyer:
                                batch_purchases[buyer] = batch_purchases.get(buyer, 0) + amount_sol
                    
                    return batch_purchases
                
                tasks = []
                for batch in batches:
                    tasks.append(loop.run_in_executor(executor, process_batch, batch))
                
                # 모든 작업 완료 대기
                results = await asyncio.gather(*tasks)
                
                # 결과 병합
                for batch_purchases in results:
                    for wallet, amount in batch_purchases.items():
                        wallet_purchases[wallet] = wallet_purchases.get(wallet, 0) + amount
            
            # SOL 범위별 분류
            buyers_by_sol_range = {
                "0_1": {"count": 0, "total_sol": 0, "wallets": []},
                "1_5": {"count": 0, "total_sol": 0, "wallets": []},
                "5_10": {"count": 0, "total_sol": 0, "wallets": []},
                "10_plus": {"count": 0, "total_sol": 0, "wallets": []}
            }
            
            # 각 지갑을 SOL 범위별로 분류
            for wallet, amount in wallet_purchases.items():
                for range_name, (min_sol, max_sol) in self.sol_ranges.items():
                    if (range_name == "10_plus" and amount >= 10) or (min_sol <= amount < max_sol):
                        buyers_by_sol_range[range_name]["wallets"].append(wallet)
                        buyers_by_sol_range[range_name]["total_sol"] += amount
                        buyers_by_sol_range[range_name]["count"] += 1
                        break
            
            # 결과 생성
            result = {
                "buyers_by_sol_range": buyers_by_sol_range,
                "total_wallets": len(wallet_purchases),
                "total_sol": sum(wallet_purchases.values())
            }
            
            # 처리 시간 기록
            end_process_time = time.time()
            self.logger.info(f"매수자 분류 완료: 소요 시간 {end_process_time - start_process_time:.2f}초")
            
            return result
            
        except Exception as e:
            self.logger.error(f"매수자 분류 중 에러 발생: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            raise 