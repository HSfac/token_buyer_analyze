import aiohttp
import ssl
from typing import List, Optional
from config import settings
from app.models.types import TokenInfo

class BirdeyeFetcher:
    def __init__(self):
        self.base_url = "https://public-api.birdeye.so/defi"
        self.headers = {
            "X-API-KEY": settings.BIRDEYE_API_KEY,
            "x-chain": "solana"
        }
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    async def get_token_transactions(self, token_address: str, limit: int = 50) -> List[str]:
        """
        특정 토큰의 최근 트랜잭션 시그니처 목록을 가져옵니다.
        """
        url = f"{self.base_url}/token/txs"
        params = {
            "address": token_address,
            "limit": min(limit, 50)  # 무료 버전 제한
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self.headers,
                    params=params,
                    ssl=self.ssl_context
                ) as response:
                    if response.status != 200:
                        print(f"Birdeye API error: {response.status} - {await response.text()}")
                        return []
                    
                    data = await response.json()
                    
                    if not data.get("success"):
                        print(f"Birdeye API error: {data.get('message', 'Unknown error')}")
                        return []
                    
                    # 트랜잭션 시그니처 추출
                    signatures = []
                    for item in data.get("data", {}).get("items", []):
                        if isinstance(item, dict) and "signature" in item:
                            signatures.append(item["signature"])
                    
                    return signatures
                    
        except Exception as e:
            print(f"Error fetching token transactions: {str(e)}")
            return []

    async def get_token_info(self, token_address: str) -> Optional[TokenInfo]:
        """
        토큰의 기본 정보를 가져옵니다.
        """
        url = f"{self.base_url}/token/info"
        params = {"address": token_address}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self.headers,
                    params=params,
                    ssl=self.ssl_context
                ) as response:
                    if response.status != 200:
                        print(f"Birdeye API error: {response.status} - {await response.text()}")
                        return None
                    
                    data = await response.json()
                    
                    if not data.get("success"):
                        print(f"Birdeye API error: {data.get('message', 'Unknown error')}")
                        return None
                    
                    token_data = data.get("data", {})
                    return TokenInfo(
                        address=token_address,
                        name=token_data.get("name", ""),
                        symbol=token_data.get("symbol", ""),
                        decimals=token_data.get("decimals", 9),
                        total_supply=float(token_data.get("supply", 0)),
                        price_usd=float(token_data.get("price", 0)),
                        volume_24h=float(token_data.get("v24hUSD", 0)),
                        market_cap=float(token_data.get("mc", 0))
                    )
                    
        except Exception as e:
            print(f"Error fetching token info: {str(e)}")
            return None 