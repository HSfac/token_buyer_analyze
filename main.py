from fastapi import FastAPI, Request, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, HTMLResponse
from datetime import datetime, timedelta
import pytz
from typing import Optional, Dict, Any
import logging
from app.fetchers.helius import HeliusFetcher
from app.analyzers.buyer_classifier import BuyerClassifier
from app.config import BIRDEYE_API_KEY, HELIUS_API_KEY
from app.visualization.dashboard import create_dashboard
from app.models.types import BuyerAnalysis, SolRange, TimeRange
import asyncio
import threading
import webbrowser
import os

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")

# API 클라이언트 초기화
helius_client = HeliusFetcher(HELIUS_API_KEY)
buyer_classifier = BuyerClassifier()

# 분석 결과를 저장할 전역 변수
analysis_cache = {}
current_analysis = None  # 현재 분석 결과를 저장할 변수 추가
dashboard_server = None  # 대시보드 서버 인스턴스를 저장할 변수

def run_dashboard():
    """대시보드 서버를 실행합니다."""
    if current_analysis is None:
        raise ValueError("분석 결과가 없습니다.")
    
    # 대시보드 앱 생성
    dashboard_app = create_dashboard(analysis=current_analysis)
    
    # 새로운 스레드에서 대시보드 서버 실행
    def run_server():
        dashboard_app.run_server(host="127.0.0.1", port=8050, debug=False)
    
    # 이전 대시보드 서버가 있다면 종료
    global dashboard_server
    if dashboard_server and dashboard_server.is_alive():
        dashboard_server.join(timeout=1)
    
    # 새로운 대시보드 서버 시작
    dashboard_server = threading.Thread(target=run_server)
    dashboard_server.daemon = True  # 메인 프로그램 종료 시 함께 종료
    dashboard_server.start()
    
    logger.info("대시보드 서버가 시작되었습니다.")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/analyze/{token_address}")
async def analyze_token(
    token_address: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 100
):
    try:
        # 시간 파라미터 처리
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                start_time = start_dt.isoformat()
            except (ValueError, AttributeError):
                raise HTTPException(status_code=400, detail="잘못된 시작 시간 형식")
                
        if end_time:
            try:
                end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                end_time = end_dt.isoformat()
            except (ValueError, AttributeError):
                raise HTTPException(status_code=400, detail="잘못된 종료 시간 형식")
                
        # 트랜잭션 데이터 수집
        transactions = await helius_client.get_token_transactions(
            token_address=token_address,
            start_time=start_time,
            end_time=end_time,
            limit=limit
        )
        
        # 매수자 분류
        classifier = BuyerClassifier()
        analysis_dict = classifier.classify_buyers(transactions)
        
        # BuyerAnalysis 객체 생성
        analysis_result = BuyerAnalysis(
            token=token_address,
            snapshot_time=datetime.now(pytz.UTC).isoformat(),
            time_range=TimeRange(
                start_time=datetime.fromisoformat(start_time) if start_time else datetime.now(pytz.UTC) - timedelta(days=1),
                end_time=datetime.fromisoformat(end_time) if end_time else datetime.now(pytz.UTC),
                interval_seconds=30  # 기본값으로 30초 설정
            ),
            buyers_by_sol_range={
                range_key: SolRange(
                    count=range_data["count"],
                    total_sol=range_data["total_sol"],
                    wallets=range_data["wallets"]
                )
                for range_key, range_data in analysis_dict["buyers_by_sol_range"].items()
            },
            wallet_summaries={},
            total_buy_volume=sum(range_data["total_sol"] for range_data in analysis_dict["buyers_by_sol_range"].values()),
            total_sell_volume=0,
            net_buy_volume=sum(range_data["total_sol"] for range_data in analysis_dict["buyers_by_sol_range"].values()),
            unique_buyers=sum(range_data["count"] for range_data in analysis_dict["buyers_by_sol_range"].values()),
            unique_sellers=0
        )
        
        # 결과를 캐시에 저장
        cache_key = f"{token_address}_{start_time}_{end_time}_{limit}"
        analysis_cache[cache_key] = analysis_result
        global current_analysis
        current_analysis = analysis_result
        
        logger.info(f"분석 결과 캐시 저장 완료: {cache_key}")
        return analysis_result
        
    except Exception as e:
        logger.error(f"토큰 분석 중 에러 발생: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/visualize/{token_address}")
async def visualize_token(
    token_address: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 100
):
    try:
        # 시간 파라미터 처리
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                start_time = start_dt.isoformat()
            except (ValueError, AttributeError):
                raise HTTPException(status_code=400, detail="잘못된 시작 시간 형식")
                
        if end_time:
            try:
                end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                end_time = end_dt.isoformat()
            except (ValueError, AttributeError):
                raise HTTPException(status_code=400, detail="잘못된 종료 시간 형식")
        
        # 캐시 키 생성
        cache_key = f"{token_address}_{start_time}_{end_time}_{limit}"
        logger.info(f"시각화 요청 - 캐시 키: {cache_key}")
        
        # 캐시된 분석 결과가 있는지 확인
        if cache_key not in analysis_cache:
            logger.error(f"캐시된 결과 없음: {cache_key}")
            raise HTTPException(status_code=400, detail="먼저 분석을 실행해주세요.")
            
        # 캐시된 분석 결과 사용
        analysis_result = analysis_cache[cache_key]
        global current_analysis
        current_analysis = analysis_result
        
        logger.info("대시보드 서버 시작")
        # 대시보드 서버 실행
        run_dashboard()
        
        return JSONResponse({"status": "success", "message": "대시보드가 생성되었습니다."})
        
    except HTTPException as e:
        logger.error(f"시각화 중 HTTP 에러 발생: {str(e)}")
        raise e
    except Exception as e:
        logger.error(f"시각화 중 에러 발생: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 