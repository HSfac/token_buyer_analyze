from typing import Dict, List
from datetime import datetime
from config import settings
from app.models.types import Transaction, BuyerRange, BuyerAnalysis, TimeRange, WalletTransaction, WalletSummary, TransactionType

class BuyerClassifier:
    def __init__(self):
        self.sol_ranges = {
            "0_1": (0, 1),
            "1_5": (1, 5),
            "5_10": (5, 10),
            "10_plus": (10, float('inf'))
        }

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

    def classify_buyers(
        self,
        transactions: List[Transaction],
        token: str,
        time_range: TimeRange
    ) -> BuyerAnalysis:
        # 지갑별 거래 내역 초기화
        wallet_summaries: Dict[str, WalletSummary] = {}
        
        # SOL 구간별 매수자 정보 초기화
        buyers_by_sol_range = {
            range_key: {
                "wallets": [],
                "count": 0,
                "total_sol": 0.0,
                "transactions": []
            }
            for range_key in self.sol_ranges.keys()
        }
        
        total_buy_volume = 0.0
        total_sell_volume = 0.0
        unique_buyers = set()
        unique_sellers = set()
        
        # 각 트랜잭션 분석
        for tx in transactions:
            if not self._is_within_time_range(tx.timestamp, time_range):
                continue
                
            # 매수 트랜잭션 처리
            if tx.token_in == "WSOL":
                amount = tx.amount_sol
                range_key = self._get_sol_range_key(amount)
                
                # 지갑 정보 업데이트
                if tx.buyer not in wallet_summaries:
                    wallet_summaries[tx.buyer] = WalletSummary(
                        wallet_address=tx.buyer,
                        total_buy_sol=0.0,
                        total_sell_sol=0.0,
                        net_buy_sol=0.0,
                        transaction_count=0,
                        transactions=[]
                    )
                
                wallet_summaries[tx.buyer].total_buy_sol += amount
                wallet_summaries[tx.buyer].net_buy_sol += amount
                wallet_summaries[tx.buyer].transaction_count += 1
                wallet_summaries[tx.buyer].transactions.append(
                    self._create_wallet_transaction(tx, True)
                )
                
                # SOL 구간별 정보 업데이트
                buyers_by_sol_range[range_key]["wallets"].append(tx.buyer)
                buyers_by_sol_range[range_key]["count"] += 1
                buyers_by_sol_range[range_key]["total_sol"] += amount
                buyers_by_sol_range[range_key]["transactions"].append(tx)
                
                total_buy_volume += amount
                unique_buyers.add(tx.buyer)
            
            # 매도 트랜잭션 처리
            elif tx.token_out == "WSOL":
                amount = tx.amount_sol
                
                if tx.seller not in wallet_summaries:
                    wallet_summaries[tx.seller] = WalletSummary(
                        wallet_address=tx.seller,
                        total_buy_sol=0.0,
                        total_sell_sol=0.0,
                        net_buy_sol=0.0,
                        transaction_count=0,
                        transactions=[]
                    )
                
                wallet_summaries[tx.seller].total_sell_sol += amount
                wallet_summaries[tx.seller].net_buy_sol -= amount
                wallet_summaries[tx.seller].transaction_count += 1
                wallet_summaries[tx.seller].transactions.append(
                    self._create_wallet_transaction(tx, False)
                )
                
                total_sell_volume += amount
                unique_sellers.add(tx.seller)
        
        # 중복 지갑 제거
        for range_key in buyers_by_sol_range:
            buyers_by_sol_range[range_key]["wallets"] = list(set(
                buyers_by_sol_range[range_key]["wallets"]
            ))
        
        return BuyerAnalysis(
            token=token,
            snapshot_time=datetime.utcnow(),
            time_range=time_range,
            buyers_by_sol_range=buyers_by_sol_range,
            wallet_summaries=wallet_summaries,
            total_buy_volume=total_buy_volume,
            total_sell_volume=total_sell_volume,
            net_buy_volume=total_buy_volume - total_sell_volume,
            unique_buyers=len(unique_buyers),
            unique_sellers=len(unique_sellers)
        ) 