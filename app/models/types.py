from pydantic import BaseModel, ConfigDict
from typing import List, Dict, Optional, Any
from datetime import datetime

class TransactionType:
    BUY = "BUY"
    SELL = "SELL"

class TimeRange(BaseModel):
    start_time: datetime
    end_time: datetime
    interval_seconds: Optional[int] = 30  # 30초 단위 분석을 위한 설정

class WalletTransaction(BaseModel):
    wallet_address: str
    transaction_type: str  # BUY or SELL
    amount_sol: float
    timestamp: datetime
    signature: str

class WalletSummary(BaseModel):
    wallet_address: str
    total_buy_sol: float
    total_sell_sol: float
    net_buy_sol: float
    transaction_count: int
    transactions: List[WalletTransaction]

class BuyerRange(BaseModel):
    wallets: List[str]
    count: int
    total_sol: float
    transactions: List[Any]

class BuyerAnalysis(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    token: str
    snapshot_time: datetime
    time_range: TimeRange
    buyers_by_sol_range: Dict[str, BuyerRange]
    wallet_summaries: Dict[str, WalletSummary]
    total_buy_volume: float
    total_sell_volume: float
    net_buy_volume: float
    unique_buyers: int
    unique_sellers: int

class Transaction(BaseModel):
    signature: str
    timestamp: datetime
    token_in: str
    token_out: str
    amount_sol: float
    buyer: str
    seller: str

class TokenInfo(BaseModel):
    address: str
    name: str
    symbol: str
    decimals: int
    total_supply: float
    price_usd: Optional[float]
    volume_24h: Optional[float]
    market_cap: Optional[float] 