from pydantic_settings import BaseSettings
from typing import Dict, Tuple, ClassVar
from dotenv import load_dotenv
import os

# .env 파일 로드
load_dotenv()

# API 키 설정
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

# API 키 검증
if not BIRDEYE_API_KEY or not HELIUS_API_KEY:
    raise ValueError("API 키가 설정되지 않았습니다. .env 파일을 확인해주세요.")

# SOL 구간 설정
SOL_RANGES: Dict[str, Tuple[float, float]] = {
    "0_1": (0, 1),
    "1_5": (1, 5),
    "5_10": (5, 10),
    "10_plus": (10, float('inf'))
}

class Settings(BaseSettings):
    BIRDEYE_API_KEY: str = BIRDEYE_API_KEY
    HELIUS_API_KEY: str = HELIUS_API_KEY
    MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    DATABASE_NAME: str = "token_analyzer"
    
    # API 엔드포인트
    BIRDEYE_BASE_URL: str = "https://public-api.birdeye.so"
    HELIUS_BASE_URL: str = "https://api.helius.xyz/v0"

settings = Settings() 