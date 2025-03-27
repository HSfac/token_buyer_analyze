import httpx
from datetime import datetime
from typing import List, Optional, Dict, Any
from config import settings
from app.models.types import Transaction, TransactionType

class HeliusFetcher:
    def __init__(self):
        self.base_url = settings.HELIUS_BASE_URL
        self.headers = {
            "Authorization": f"Bearer {settings.HELIUS_API_KEY}",
            "Content-Type": "application/json"
        }
        self.wsol_address = "So11111111111111111111111111111111111111112"

    async def get_transaction_details(self, signature: str, target_token: str) -> Optional[Transaction]:
        """
        특정 시그니처의 트랜잭션 상세 정보를 가져옵니다.
        WSOL로 토큰을 매수/매도한 트랜잭션을 분석합니다.
        """
        url = f"{self.base_url}/transactions/{signature}"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=self.headers)
                response.raise_for_status()
                
                data = response.json()
                
                # SWAP 이벤트 찾기
                for event in data.get("events", []):
                    if event.get("type") == "SWAP":
                        swap_data = event.get("swap", {})
                        token_in = swap_data.get("tokenIn")
                        token_out = swap_data.get("tokenOut")
                        
                        # 매수 트랜잭션 (WSOL -> 토큰)
                        if (token_in == self.wsol_address and token_out == target_token):
                            return self._create_transaction(
                                data, swap_data, target_token, TransactionType.BUY
                            )
                        
                        # 매도 트랜잭션 (토큰 -> WSOL)
                        elif (token_in == target_token and token_out == self.wsol_address):
                            return self._create_transaction(
                                data, swap_data, target_token, TransactionType.SELL
                            )
                
                return None
                
        except httpx.HTTPError as e:
            print(f"Helius API error for signature {signature}: {str(e)}")
            return None
        except Exception as e:
            print(f"Unexpected error processing transaction {signature}: {str(e)}")
            return None

    def _create_transaction(
        self,
        data: Dict[str, Any],
        swap_data: Dict[str, Any],
        target_token: str,
        tx_type: str
    ) -> Transaction:
        """
        트랜잭션 객체를 생성합니다.
        """
        # 지갑 주소 찾기
        wallet_address = None
        for transfer in data.get("tokenTransfers", []):
            if tx_type == TransactionType.BUY:
                if transfer.get("toUserAccount") == target_token:
                    wallet_address = transfer.get("fromUserAccount")
                    break
            else:  # SELL
                if transfer.get("fromUserAccount") == target_token:
                    wallet_address = transfer.get("toUserAccount")
                    break
        
        if not wallet_address:
            return None
            
        # SOL 금액 계산 (lamports to SOL)
        amount_sol = float(swap_data.get("amountIn" if tx_type == TransactionType.SELL else "amountOut", 0)) / 1e9
        
        return Transaction(
            signature=data.get("signature", ""),
            timestamp=datetime.fromtimestamp(data.get("timestamp", 0)),
            token_in=swap_data.get("tokenIn", ""),
            token_out=swap_data.get("tokenOut", ""),
            amount_sol=amount_sol,
            buyer=wallet_address if tx_type == TransactionType.BUY else None,
            seller=wallet_address if tx_type == TransactionType.SELL else None
        ) 