import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# API 키 설정
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

# API 키 검증
if not BIRDEYE_API_KEY:
    raise ValueError("BIRDEYE_API_KEY가 설정되지 않았습니다.")
if not HELIUS_API_KEY:
    raise ValueError("HELIUS_API_KEY가 설정되지 않았습니다.") 