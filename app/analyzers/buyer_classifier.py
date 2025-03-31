from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from app.config import settings, SOL_RANGES
from app.models.types import Transaction, BuyerRange, BuyerAnalysis, TimeRange, WalletTransaction, WalletSummary, TransactionType
import logging

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

    def analyze_transactions(
        self,
        transactions: List[Transaction],
        start_time: datetime,
        end_time: datetime,
        interval_seconds: int = 30
    ) -> BuyerAnalysis:
        """
        트랜잭션 목록을 분석하여 매수자 정보를 추출합니다.
        """
        # 시간 간격별 데이터 초기화
        time_intervals = self._create_time_intervals(start_time, end_time, interval_seconds)
        volumes = [0] * len(time_intervals)
        buy_volumes = [0] * len(time_intervals)
        sell_volumes = [0] * len(time_intervals)
        timestamps = [interval.isoformat() for interval in time_intervals]

        # 지갑별 거래 정보 수집
        wallet_transactions: Dict[str, List[Transaction]] = {}
        for tx in transactions:
            if tx.buyer:
                if tx.buyer not in wallet_transactions:
                    wallet_transactions[tx.buyer] = []
                wallet_transactions[tx.buyer].append(tx)
            if tx.seller:
                if tx.seller not in wallet_transactions:
                    wallet_transactions[tx.seller] = []
                wallet_transactions[tx.seller].append(tx)

        # 시간 간격별 거래량 계산
        for tx in transactions:
            interval_index = self._get_interval_index(tx.timestamp, time_intervals)
            if interval_index is not None:
                volumes[interval_index] += tx.amount_sol
                if tx.buyer:
                    buy_volumes[interval_index] += tx.amount_sol
                if tx.seller:
                    sell_volumes[interval_index] += tx.amount_sol

        # SOL 금액 범위별 지갑 분류
        buyers_by_sol_range: Dict[str, BuyerRange] = {}
        for range_name, (min_sol, max_sol) in self.sol_ranges.items():
            range_wallets = []
            range_transactions = []
            
            for wallet, txs in wallet_transactions.items():
                total_buy = sum(tx.amount_sol for tx in txs if tx.buyer == wallet)
                if min_sol <= total_buy < max_sol:
                    range_wallets.append(wallet)
                    range_transactions.extend(txs)
            
            buyers_by_sol_range[range_name] = BuyerRange(
                wallets=range_wallets,
                transactions=range_transactions
            )

        return BuyerAnalysis(
            timestamps=timestamps,
            volumes=volumes,
            buy_volumes=buy_volumes,
            sell_volumes=sell_volumes,
            buyers_by_sol_range=buyers_by_sol_range
        )

    def _create_time_intervals(
        self,
        start_time: datetime,
        end_time: datetime,
        interval_seconds: int
    ) -> List[datetime]:
        """
        시작 시간부터 종료 시간까지의 시간 간격 목록을 생성합니다.
        """
        intervals = []
        current_time = start_time
        while current_time <= end_time:
            intervals.append(current_time)
            current_time += timedelta(seconds=interval_seconds)
        return intervals

    def _get_interval_index(
        self,
        timestamp: datetime,
        intervals: List[datetime]
    ) -> Optional[int]:
        """
        주어진 타임스탬프가 속하는 시간 간격의 인덱스를 반환합니다.
        """
        for i, interval in enumerate(intervals):
            if timestamp < interval:
                return i - 1 if i > 0 else 0
        return len(intervals) - 1 if intervals else None

    def _get_sol_range_key(self, amount: float) -> str:
        for key, (min_val, max_val) in self.sol_ranges.items():
            if min_val <= amount < max_val:
                return key
        return "10_plus"

    def _is_within_time_range(self, timestamp: datetime, time_range: TimeRange) -> bool:
        return time_range.start_time <= timestamp <= time_range.end_time

    def _create_wallet_transaction(self, tx: Transaction, is_buy: bool) -> WalletTransaction:
        return WalletTransaction(
            wallet_address=tx.buyer if is_buy else tx.seller,
            transaction_type=TransactionType.BUY if is_buy else TransactionType.SELL,
            amount_sol=tx.amount_sol,
            timestamp=tx.timestamp,
            signature=tx.signature
        )

    def classify_buyers(self, transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """매수자들을 SOL 범위별로 분류"""
        try:
            # 지갑별 매수량 집계
            wallet_purchases = {}
            for tx in transactions:
                buyer = tx.get('buyer')
                if isinstance(buyer, dict):
                    buyer = buyer.get('pubkey')
                if buyer:
                    amount = tx.get('amount_sol', 0)
                    wallet_purchases[buyer] = wallet_purchases.get(buyer, 0) + amount
                    
            # SOL 범위별 분류
            buyers_by_sol_range = {
                "0_1": {"count": 0, "total_sol": 0, "wallets": []},
                "1_5": {"count": 0, "total_sol": 0, "wallets": []},
                "5_10": {"count": 0, "total_sol": 0, "wallets": []},
                "10_plus": {"count": 0, "total_sol": 0, "wallets": []}
            }
            
            for wallet, amount in wallet_purchases.items():
                if 0 <= amount < 1:
                    buyers_by_sol_range["0_1"]["count"] += 1
                    buyers_by_sol_range["0_1"]["total_sol"] += amount
                    buyers_by_sol_range["0_1"]["wallets"].append(wallet)
                elif 1 <= amount < 5:
                    buyers_by_sol_range["1_5"]["count"] += 1
                    buyers_by_sol_range["1_5"]["total_sol"] += amount
                    buyers_by_sol_range["1_5"]["wallets"].append(wallet)
                elif 5 <= amount < 10:
                    buyers_by_sol_range["5_10"]["count"] += 1
                    buyers_by_sol_range["5_10"]["total_sol"] += amount
                    buyers_by_sol_range["5_10"]["wallets"].append(wallet)
                else:
                    buyers_by_sol_range["10_plus"]["count"] += 1
                    buyers_by_sol_range["10_plus"]["total_sol"] += amount
                    buyers_by_sol_range["10_plus"]["wallets"].append(wallet)
                    
            return {
                "buyers_by_sol_range": buyers_by_sol_range,
                "total_wallets": len(wallet_purchases),
                "total_sol": sum(wallet_purchases.values())
            }
            
        except Exception as e:
            self.logger.error(f"매수자 분류 중 에러 발생: {str(e)}")
            raise 