from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi import Request
from typing import Optional
from datetime import datetime, timedelta
import asyncio
from app.fetchers.birdeye import BirdeyeFetcher
from app.fetchers.helius import HeliusFetcher
from app.analyzers.buyer_classifier import BuyerClassifier
from app.models.types import BuyerAnalysis, TokenInfo, TimeRange
from app.visualization.dashboard import create_dashboard
import uvicorn

app = FastAPI(title="Solana Token Buyer Analyzer")

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 템플릿과 정적 파일 설정
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """
    메인 대시보드 페이지를 표시합니다.
    """
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/analyze/{token_address}", response_model=BuyerAnalysis)
async def analyze_token_buyers(
    token_address: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    interval_seconds: Optional[int] = 30,
    limit: Optional[int] = 100
) -> BuyerAnalysis:
    """
    특정 토큰의 매수자 분석을 수행합니다.
    
    Args:
        token_address: 분석할 토큰의 주소
        start_time: 분석 시작 시간 (기본값: 현재 시간 - 24시간)
        end_time: 분석 종료 시간 (기본값: 현재 시간)
        interval_seconds: 분석 간격 (초 단위, 기본값: 30초)
        limit: 가져올 최대 트랜잭션 수 (기본값: 100)
    """
    try:
        # 시간 범위 설정
        if end_time is None:
            end_time = datetime.utcnow()
        if start_time is None:
            start_time = end_time - timedelta(days=1)
        
        time_range = TimeRange(
            start_time=start_time,
            end_time=end_time,
            interval_seconds=interval_seconds
        )
        
        # Birdeye에서 트랜잭션 시그니처 수집
        birdeye = BirdeyeFetcher()
        signatures = await birdeye.get_token_transactions(token_address, limit)
        
        # Helius에서 트랜잭션 상세 정보 수집
        helius = HeliusFetcher()
        transactions = []
        
        for signature in signatures:
            tx = await helius.get_transaction_details(signature, token_address)
            if tx:
                transactions.append(tx)
        
        # 매수자 분류 및 분석
        classifier = BuyerClassifier()
        analysis = classifier.classify_buyers(transactions, token_address, time_range)
        
        return analysis
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/token/{token_address}", response_model=TokenInfo)
async def get_token_info(token_address: str) -> TokenInfo:
    """
    토큰의 기본 정보를 조회합니다.
    """
    try:
        birdeye = BirdeyeFetcher()
        token_info = await birdeye.get_token_info(token_address)
        return TokenInfo(**token_info)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/visualize/{token_address}", response_class=HTMLResponse)
async def visualize_analysis(
    token_address: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    interval_seconds: Optional[int] = 30,
    limit: Optional[int] = 100
):
    """
    토큰 매수자 분석 결과를 시각화합니다.
    """
    try:
        # 분석 수행
        analysis = await analyze_token_buyers(
            token_address,
            start_time,
            end_time,
            interval_seconds,
            limit
        )
        
        # 대시보드 생성
        dashboard = create_dashboard(analysis)
        
        # 대시보드 실행
        dashboard.run_server(debug=False, port=8050)
        
        return f"""
        <html>
            <head>
                <title>토큰 매수자 분석 대시보드</title>
            </head>
            <body>
                <h1>토큰 매수자 분석 대시보드</h1>
                <p>대시보드가 새 탭에서 열립니다...</p>
                <script>
                    window.open('http://localhost:8050', '_blank');
                </script>
            </body>
        </html>
        """
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000) 