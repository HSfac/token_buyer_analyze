from pydantic_settings import BaseSettings
from typing import Dict, Tuple, ClassVar
from dotenv import load_dotenv
import os

load_dotenv()

class Settings(BaseSettings):
    BIRDEYE_API_KEY: str = os.getenv("BIRDEYE_API_KEY", "")
    HELIUS_API_KEY: str = os.getenv("HELIUS_API_KEY", "")
    MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    DATABASE_NAME: str = "token_analyzer"
    
    # API 엔드포인트
    BIRDEYE_BASE_URL: str = "https://public-api.birdeye.so"
    HELIUS_BASE_URL: str = "https://api.helius.xyz/v0"
    
    # SOL 구간 설정
    SOL_RANGES: ClassVar[Dict[str, Tuple[float, float]]] = {
        "0_1": (0, 1),
        "1_5": (1, 5),
        "5_10": (5, 10),
        "10_plus": (10, float('inf'))
    }

settings = Settings() 