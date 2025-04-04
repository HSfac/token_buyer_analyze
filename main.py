from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import pytz
from typing import Optional, Dict, Any, List
import logging
from app.fetchers.helius import HeliusFetcher
from app.analyzers.buyer_classifier import BuyerClassifier
from app.config import BIRDEYE_API_KEY, HELIUS_API_KEY
from app.visualization.dashboard import create_dashboard
from app.models.types import BuyerAnalysis, SolRange, TimeRange, Transaction
import asyncio
import threading
import webbrowser
import os
import time
import uuid
import pandas as pd
from pydantic import BaseModel
import queue
import json
from contextlib import asynccontextmanager

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 로그 메시지를 위한 큐
log_queue = queue.Queue()

# 사용자 정의 로그 핸들러
class QueueHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            log_queue.put({"message": msg, "level": record.levelname.lower(), "time": datetime.now().isoformat()})
        except Exception:
            self.handleError(record)

# 로그 핸들러 추가
queue_handler = QueueHandler()
queue_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(queue_handler)

app = FastAPI(title="토큰 매수자 분석 시스템", version="1.0.0")
templates = Jinja2Templates(directory="app/templates")

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 정적 파일 서빙 설정
if os.path.exists("app/static"):
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

# API 클라이언트 초기화 - 요금제 설정 가능
helius_client = HeliusFetcher(HELIUS_API_KEY, plan="professional")  # "developer", "business", "professional"
buyer_classifier = BuyerClassifier()

# 분석 결과를 저장할 전역 변수
analysis_cache = {}
current_analysis = None  # 현재 분석 결과를 저장할 변수 추가
dashboard_server = None  # 대시보드 서버 인스턴스를 저장할 변수
analysis_tasks = {}  # 분석 작업 상태 저장
csv_exports = {}     # CSV 내보내기 상태 저장

# 데이터 디렉토리 확인 및 생성
if not os.path.exists("data"):
    os.makedirs("data")

# 요청 모델
class AnalysisRequest(BaseModel):
    token_address: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    limit: int = 100
    use_enhanced_api: bool = True
    batch_size: Optional[int] = None

# 대용량 분석 요청 모델
class LargeAnalysisRequest(BaseModel):
    token_address: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    limit: int = 1000  # 기본값 1000으로 증가
    use_enhanced_api: bool = True
    batch_size: int = 500  # 배치 크기 기본값 500으로 설정
    export_csv: bool = True  # CSV 내보내기 옵션

# 응답 모델
class AnalysisStatus(BaseModel):
    task_id: str
    status: str
    progress: float
    message: str

def run_dashboard(analysis_result):
    """대시보드 서버를 실행합니다."""
    if analysis_result is None:
        raise ValueError("분석 결과가 없습니다.")
    
    # 이전 대시보드 서버가 있다면 강제로 종료
    global dashboard_server
    if dashboard_server and dashboard_server.is_alive():
        logger.info("이전 대시보드 서버 종료 중...")
        # 서버 종료를 위한 HTTP 요청 전송
        try:
            import requests
            requests.get("http://127.0.0.1:8050/_shutdown", timeout=1)
        except:
            pass
        
        # 스레드 종료 대기
        dashboard_server.join(timeout=5)
        
        # 여전히 실행 중이면 강제 종료
        if dashboard_server.is_alive():
            logger.warning("대시보드 서버가 응답하지 않아 강제 종료합니다.")
            import os
            os.system("pkill -f 'dash'")
    
    # 대시보드 앱 생성
    dashboard_app = create_dashboard(analysis=analysis_result)
    
    # 새로운 스레드에서 대시보드 서버 실행
    def run_server():
        dashboard_app.run_server(host="127.0.0.1", port=8050, debug=False)
    
    # 새로운 대시보드 서버 시작
    dashboard_server = threading.Thread(target=run_server)
    dashboard_server.daemon = True  # 메인 프로그램 종료 시 함께 종료
    dashboard_server.start()
    
    logger.info("대시보드 서버가 시작되었습니다.")
    
    # 서버가 완전히 시작될 때까지 잠시 대기
    time.sleep(2)

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/analyze/{token_address}")
async def analyze_token(
    token_address: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 100,
    batch_size: int = 100  # 배치 크기 파라미터 추가
):
    try:
        # 시간 파라미터 처리
        start_dt, end_dt = await process_time_params(start_time, end_time)
        
        # 시작 시간 기록
        start_process_time = time.time()
        
        # 트랜잭션 데이터 수집 (Enhanced API 사용)
        transactions = await helius_client.get_token_transactions(
            token_address=token_address,
            start_time=start_dt.isoformat() if start_dt else None,
            end_time=end_dt.isoformat() if end_dt else None,
            limit=limit,
            batch_size=batch_size  # 배치 크기 파라미터 전달
        )
        
        # 중간 처리 시간 기록
        fetch_time = time.time() - start_process_time
        logger.info(f"트랜잭션 데이터 수집 완료: {len(transactions)}개, 소요시간: {fetch_time:.2f}초")
        
        # 트랜잭션이 없으면 빈 결과 반환
        if not transactions:
            return JSONResponse({
                "message": "분석할 트랜잭션이 없습니다.",
                "transactions_count": 0
            })
        
        # 매수자 분류 (비동기 처리)
        analysis_dict = await buyer_classifier.classify_buyers(transactions)
        
        # BuyerAnalysis 객체 생성
        analysis_result = BuyerAnalysis(
            token=token_address,
            snapshot_time=datetime.now(pytz.UTC).isoformat(),
            time_range=TimeRange(
                start_time=start_dt if start_dt else datetime.now(pytz.UTC) - timedelta(days=1),
                end_time=end_dt if end_dt else datetime.now(pytz.UTC),
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
        
        # 처리 시간 기록
        end_process_time = time.time()
        total_time = end_process_time - start_process_time
        logger.info(f"분석 완료: 총 소요시간 {total_time:.2f}초")
        
        # 분석 결과 요약 로그 추가
        logger.info(f"분석 결과 요약: 토큰={token_address}")
        logger.info(f"총 구매자 수: {analysis_result.unique_buyers}명")
        logger.info(f"총 구매 금액: {analysis_result.total_buy_volume:.2f} SOL")
        
        # 구매자 분포 요약
        if hasattr(analysis_result, 'buyers_by_sol_range') and analysis_result.buyers_by_sol_range:
            logger.info("구매자 분포:")
            for range_key, range_data in analysis_result.buyers_by_sol_range.items():
                logger.info(f"  {range_key}: {range_data.count}명 ({range_data.total_sol:.2f} SOL)")
        
        return analysis_result
        
    except Exception as e:
        logger.error(f"토큰 분석 중 에러 발생: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/analyze")
async def analyze_token_post(request: AnalysisRequest, background_tasks: BackgroundTasks):
    """비동기로 토큰 분석을 수행하는 POST 엔드포인트"""
    try:
        # 작업 ID 생성
        task_id = f"{request.token_address}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # 작업 상태 초기화
        analysis_tasks[task_id] = {
            "status": "pending",
            "progress": 0.0,
            "message": "분석 준비 중",
            "result": None
        }
        
        # 백그라운드 작업으로 실행
        background_tasks.add_task(
            run_analysis_task, 
            task_id, 
            request.token_address, 
            request.start_time, 
            request.end_time, 
            request.limit,
            request.use_enhanced_api,
            request.batch_size
        )
        
        return {"task_id": task_id, "status": "pending", "message": "분석이 시작되었습니다."}
        
    except Exception as e:
        logger.error(f"토큰 분석 요청 중 에러 발생: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/analyze/status/{task_id}", response_model=AnalysisStatus)
async def get_analysis_status(task_id: str):
    """분석 작업의 상태를 조회하는 엔드포인트"""
    if task_id not in analysis_tasks:
        raise HTTPException(status_code=404, detail="존재하지 않는 작업 ID입니다.")
    
    task_info = analysis_tasks[task_id]
    return {
        "task_id": task_id,
        "status": task_info["status"],
        "progress": task_info["progress"],
        "message": task_info["message"]
    }

@app.get("/analyze/result/{task_id}")
async def get_analysis_result(task_id: str):
    """분석 작업의 결과를 조회하는 엔드포인트"""
    if task_id not in analysis_tasks:
        raise HTTPException(status_code=404, detail="존재하지 않는 작업 ID입니다.")
    
    task_info = analysis_tasks[task_id]
    if task_info["status"] != "completed":
        return {
            "status": task_info["status"],
            "progress": task_info["progress"],
            "message": task_info["message"]
        }
    
    return task_info["result"]

@app.get("/visualize/{token_address}")
async def visualize_token(
    token_address: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 100
):
    """토큰 분석 결과 시각화"""
    try:
        # 시간 파라미터 처리
        start_dt, end_dt = await process_time_params(start_time, end_time)
        start_time_iso = start_dt.isoformat() if start_dt else None
        end_time_iso = end_dt.isoformat() if end_dt else None
        
        # 기존 캐시에서 가장 최근 결과 찾기
        latest_result = None
        latest_timestamp = 0
        
        # 캐시에서 일치하는 항목 검색
        for key, value in analysis_cache.items():
            if token_address in key and str(limit) in key:
                # 타임스탬프 추출 (마지막 부분에서)
                try:
                    key_parts = key.split('_')
                    if len(key_parts) >= 5:
                        timestamp = int(key_parts[-1])
                        if timestamp > latest_timestamp:
                            latest_timestamp = timestamp
                            latest_result = value
                except:
                    continue
        
        # 캐시된 결과가 있으면 사용, 없으면 새로 분석
        if latest_result:
            analysis_result = latest_result
            logger.info(f"캐시에서 {token_address}의 최근 분석 결과를 사용합니다.")
            
            # 콘솔 표시
            print("\n===== 시각화를 위한 분석 결과 (캐시에서) =====")
            print(f"토큰 주소: {token_address}")
            print(f"트랜잭션 수: {limit}")
            print(f"총 구매자 수: {analysis_result.unique_buyers}")
            print(f"총 구매 금액: {analysis_result.total_buy_volume:.2f} SOL")
            
            # 프론트엔드 로그 업데이트
            print("[시각화 로그] 캐시에서 분석 결과를 가져왔습니다.")
            print("[시각화 로그] 분석 결과 요약:")
            print(f"[시각화 로그] 총 구매자 수: {analysis_result.unique_buyers}명")
            print(f"[시각화 로그] 총 구매 금액: {analysis_result.total_buy_volume:.2f} SOL")
            
            # 구매자 분포 로그 추가
            if hasattr(analysis_result, 'buyers_by_sol_range') and analysis_result.buyers_by_sol_range:
                print("[시각화 로그] 구매자 분포:")
                for range_key, range_data in analysis_result.buyers_by_sol_range.items():
                    print(f"[시각화 로그]   {range_key}: {range_data.count}명 ({range_data.total_sol:.2f} SOL)")
            
            print("[시각화 로그] 시각화를 생성합니다...")
        else:
            # 프론트엔드 로그 업데이트
            print("[시각화 로그] 분석을 시작합니다...")
            
            # 토큰 분석 수행
            analysis_result = await analyze_token(
                token_address=token_address,
                limit=limit,
                start_time=start_time_iso,
                end_time=end_time_iso
            )
            
            # 콘솔 표시
            print("\n===== 시각화를 위한 분석 결과 (새로 분석) =====")
            print(f"토큰 주소: {token_address}")
            print(f"트랜잭션 수: {limit}")
            print(f"총 구매자 수: {analysis_result.unique_buyers}")
            print(f"총 구매 금액: {analysis_result.total_buy_volume:.2f} SOL")
            
            # 프론트엔드 로그 업데이트
            print("[시각화 로그] 분석이 완료되었습니다!")
            print("[시각화 로그] 분석 결과 요약:")
            print(f"[시각화 로그] 총 구매자 수: {analysis_result.unique_buyers}명")
            print(f"[시각화 로그] 총 구매 금액: {analysis_result.total_buy_volume:.2f} SOL")
            
            # 구매자 분포 로그 추가
            if hasattr(analysis_result, 'buyers_by_sol_range') and analysis_result.buyers_by_sol_range:
                print("[시각화 로그] 구매자 분포:")
                for range_key, range_data in analysis_result.buyers_by_sol_range.items():
                    print(f"[시각화 로그]   {range_key}: {range_data.count}명 ({range_data.total_sol:.2f} SOL)")
            
            print("[시각화 로그] 시각화를 생성합니다...")
        
        # 구매자 분류 데이터
        print("\n--- 구매자 분류 ---")
        for sol_range, data in analysis_result.buyers_by_sol_range.items():
            print(f"{sol_range}: {data.count}명, {data.total_sol:.2f} SOL")
        
        print("===============================\n")
        
        # 대시보드 서버 실행
        run_dashboard(analysis_result)
        
        # 프론트엔드 로그 업데이트
        print("[시각화 로그] 시각화가 생성되었습니다. 새 탭에서 열립니다...")
        
        # 대시보드 URL 반환
        dashboard_url = "http://127.0.0.1:8050"
        return {"dashboard_url": dashboard_url}
        
    except Exception as e:
        # 프론트엔드 로그 업데이트
        print(f"[시각화 로그] 에러 발생: {str(e)}")
        logger.error(f"시각화 중 에러 발생: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/metrics/performance")
async def get_performance_metrics():
    """성능 메트릭 정보 조회"""
    try:
        metrics = {
            "api_latency": {
                "get_transactions": [],
                "analyze_buyers": []
            },
            "cache_hit_ratio": 0,
            "total_requests": 0,
            "cache_hits": 0
        }
        
        # 클라이언트에서 메트릭 수집
        if hasattr(helius_client, '_metrics'):
            metrics["api_latency"]["get_transactions"] = helius_client._metrics.get("latency", [])
            
        # 분류기에서 메트릭 수집
        if hasattr(buyer_classifier, '_metrics'):
            metrics["api_latency"]["analyze_buyers"] = buyer_classifier._metrics.get("latency", [])
            
        # 캐시 히트율 계산
        if hasattr(helius_client, '_cache_stats'):
            cache_stats = helius_client._cache_stats
            metrics["total_requests"] = cache_stats.get("total", 0)
            metrics["cache_hits"] = cache_stats.get("hits", 0)
            if metrics["total_requests"] > 0:
                metrics["cache_hit_ratio"] = metrics["cache_hits"] / metrics["total_requests"]
                
        return metrics
        
    except Exception as e:
        logger.error(f"메트릭 정보 조회 중 에러 발생: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/cache")
async def clear_cache():
    """캐시 초기화"""
    try:
        # Helius 클라이언트 캐시 초기화
        if hasattr(helius_client, '_cache'):
            helius_client._cache.clear()
            
        # 분석 결과 캐시 초기화
        global analysis_cache
        analysis_cache.clear()
        
        logger.info("캐시가 초기화되었습니다.")
        return {"status": "success", "message": "캐시가 초기화되었습니다."}
        
    except Exception as e:
        logger.error(f"캐시 초기화 중 에러 발생: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/analyze/large")
async def analyze_large_dataset(request: LargeAnalysisRequest, background_tasks: BackgroundTasks):
    """
    대용량 데이터 분석 (최대 10,000개 트랜잭션)을 비동기로 실행
    결과는 백그라운드에서 처리하고 상태를 조회할 수 있음
    """
    try:
        # 작업 ID 생성
        task_id = f"large_{request.token_address}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # 작업 상태 초기화
        analysis_tasks[task_id] = {
            "status": "pending",
            "progress": 0.0,
            "message": "대용량 분석 준비 중...",
            "result": None,
            "csv_export_path": None
        }
        
        # 최대 10,000개 트랜잭션으로 제한
        limit = min(request.limit, 10000)
        
        # 적절한 배치 크기 설정
        batch_size = request.batch_size
        if batch_size is None:
            # 요금제에 따라 기본값 설정
            batch_size = helius_client.max_batch_size
        
        logger.info(f"대용량 분석 시작: 토큰={request.token_address}, 제한={limit}, 배치={batch_size}")
        
        # 백그라운드 작업으로 실행
        background_tasks.add_task(
            run_large_analysis_task,
            task_id,
            request.token_address,
            request.start_time,
            request.end_time,
            limit,
            request.use_enhanced_api,
            batch_size,
            request.export_csv
        )
        
        return {
            "task_id": task_id,
            "status": "pending",
            "message": "대용량 분석이 시작되었습니다. '/analyze/status/{task_id}'로 상태를 확인하세요."
        }
        
    except Exception as e:
        logger.error(f"대용량 분석 요청 중 에러 발생: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/csv/{task_id}")
async def download_csv(task_id: str):
    """분석 결과 CSV 파일 다운로드"""
    if task_id not in analysis_tasks:
        raise HTTPException(status_code=404, detail="존재하지 않는 작업 ID입니다.")
    
    task_info = analysis_tasks[task_id]
    if task_info["status"] != "completed":
        raise HTTPException(status_code=400, detail="분석이 아직 완료되지 않았습니다.")
    
    if not task_info.get("csv_export_path"):
        raise HTTPException(status_code=404, detail="CSV 파일이 생성되지 않았습니다.")
    
    file_path = task_info["csv_export_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="CSV 파일을 찾을 수 없습니다.")
    
    return FileResponse(
        path=file_path,
        filename=os.path.basename(file_path),
        media_type="text/csv"
    )

async def run_large_analysis_task(
    task_id: str,
    token_address: str,
    start_time: Optional[str],
    end_time: Optional[str],
    limit: int,
    use_enhanced_api: bool,
    batch_size: int,
    export_csv: bool
):
    """대용량 분석을 백그라운드에서 실행하는 함수"""
    try:
        task_info = analysis_tasks[task_id]
        task_info["status"] = "running"
        task_info["progress"] = 0.05
        task_info["message"] = "시간 파라미터 처리 중..."
        
        # 시간 파라미터 처리
        start_dt, end_dt = await process_time_params(start_time, end_time)
        
        task_info["progress"] = 0.1
        task_info["message"] = f"대용량 트랜잭션 데이터 수집 중... (최대 {limit}개)"
        
        # 트랜잭션 데이터 수집 시작 시간
        fetch_start = time.time()
        
        # 트랜잭션 데이터 수집 (스트리밍 방식)
        transactions = await helius_client.get_token_transactions(
            token_address=token_address,
            start_time=start_dt.isoformat() if start_dt else None,
            end_time=end_dt.isoformat() if end_dt else None,
            limit=limit,
            use_enhanced_api=use_enhanced_api,
            batch_size=batch_size
        )
        
        fetch_time = time.time() - fetch_start
        
        # 데이터가 없으면 작업 종료
        if not transactions:
            task_info["status"] = "completed"
            task_info["progress"] = 1.0
            task_info["message"] = "분석할 트랜잭션이 없습니다."
            task_info["result"] = {"message": "분석할 트랜잭션이 없습니다.", "transactions_count": 0}
            return
            
        logger.info(f"트랜잭션 데이터 수집 완료: {len(transactions)}개, 소요시간: {fetch_time:.2f}초")
        task_info["progress"] = 0.6
        task_info["message"] = f"{len(transactions)}개 트랜잭션 분석 중..."
        
        # 분석 시작 시간
        analysis_start = time.time()
        
        # 매수자 분류 (비동기 처리)
        analysis_dict = await buyer_classifier.classify_buyers(transactions)
        
        analysis_time = time.time() - analysis_start
        logger.info(f"매수자 분류 완료: 소요시간: {analysis_time:.2f}초")
        
        task_info["progress"] = 0.8
        task_info["message"] = "결과 생성 및 캐싱 중..."
        
        # BuyerAnalysis 객체 생성
        analysis_result = BuyerAnalysis(
            token=token_address,
            snapshot_time=datetime.now(pytz.UTC).isoformat(),
            time_range=TimeRange(
                start_time=start_dt if start_dt else datetime.now(pytz.UTC) - timedelta(days=1),
                end_time=end_dt if end_dt else datetime.now(pytz.UTC),
                interval_seconds=30
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
        
        # 캐시에 결과 저장
        cache_key = f"{token_address}_{start_time}_{end_time}_{limit}_{int(time.time())}"
        analysis_cache[cache_key] = analysis_result
        
        # 전역 변수에도 저장
        global current_analysis
        current_analysis = analysis_result
        
        # CSV 내보내기 처리
        if export_csv:
            task_info["progress"] = 0.9
            task_info["message"] = "CSV 파일 생성 중..."
            
            csv_path = await export_analysis_to_csv(analysis_result, token_address)
            task_info["csv_export_path"] = csv_path
            
            logger.info(f"CSV 파일 생성 완료: {csv_path}")
        
        # 작업 완료 상태 업데이트
        task_info["status"] = "completed"
        task_info["progress"] = 1.0
        task_info["message"] = f"분석이 완료되었습니다. {len(transactions)}개 트랜잭션 처리됨."
        task_info["result"] = analysis_result
        
        # 총 소요 시간
        total_time = fetch_time + analysis_time
        logger.info(f"대용량 분석 완료: 총 소요시간 {total_time:.2f}초")
        
        # 분석 결과 요약 로그 추가
        logger.info(f"대용량 분석 결과 요약: 토큰={token_address}")
        logger.info(f"총 구매자 수: {analysis_result.unique_buyers}명")
        logger.info(f"총 구매 금액: {analysis_result.total_buy_volume:.2f} SOL")
        
        # 구매자 분포 요약
        if hasattr(analysis_result, 'buyers_by_sol_range') and analysis_result.buyers_by_sol_range:
            logger.info("구매자 분포:")
            for range_key, range_data in analysis_result.buyers_by_sol_range.items():
                logger.info(f"  {range_key}: {range_data.count}명 ({range_data.total_sol:.2f} SOL)")
        
    except Exception as e:
        logger.error(f"대용량 분석 작업 중 에러 발생: {str(e)}")
        if task_id in analysis_tasks:
            analysis_tasks[task_id]["status"] = "failed"
            analysis_tasks[task_id]["progress"] = 0
            analysis_tasks[task_id]["message"] = f"에러 발생: {str(e)}"

async def export_analysis_to_csv(analysis: BuyerAnalysis, token_address: str) -> str:
    """분석 결과를 CSV 파일로 내보내기"""
    # 파일명 생성
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"token_analysis_{token_address[:8]}_{timestamp}.csv"
    filepath = os.path.join("data", filename)
    
    # 데이터 준비 - 세 가지 섹션으로 구성
    csv_data = []
    
    # 1. 토큰 정보
    csv_data.append({
        '분석_유형': '토큰 정보',
        '토큰_주소': analysis.token,
        '분석_시간': analysis.snapshot_time,
        '총_매수자_수': sum(range_data.count for range_data in analysis.buyers_by_sol_range.values()),
        '총_매수량_SOL': sum(range_data.total_sol for range_data in analysis.buyers_by_sol_range.values()),
        '구간': '',
        '지갑_주소': '',
        '매수량_SOL': ''
    })
    
    # 2. SOL 구간별 요약 정보
    for range_key, range_data in analysis.buyers_by_sol_range.items():
        csv_data.append({
            '분석_유형': 'SOL 구간 요약',
            '토큰_주소': analysis.token,
            '분석_시간': '',
            '총_매수자_수': '',
            '총_매수량_SOL': '',
            '구간': range_key,
            '지갑_수': range_data.count,
            '구간_총_매수량_SOL': range_data.total_sol
        })
    
    # 3. 개별 지갑 정보
    for range_key, range_data in analysis.buyers_by_sol_range.items():
        avg_amount = range_data.total_sol / range_data.count if range_data.count > 0 else 0
        for wallet in range_data.wallets:
            csv_data.append({
                '분석_유형': '지갑 상세',
                '토큰_주소': '',
                '분석_시간': '',
                '총_매수자_수': '',
                '총_매수량_SOL': '',
                '구간': range_key,
                '지갑_주소': wallet,
                '매수량_SOL': avg_amount  # 정확한 개별 지갑 매수량이 없는 경우 평균값 사용
            })
    
    # DataFrame 생성 및 CSV 저장
    df = pd.DataFrame(csv_data)
    df.to_csv(filepath, index=False, encoding='utf-8-sig')  # 한글 지원을 위한 인코딩
    
    return filepath

async def process_time_params(start_time: Optional[str], end_time: Optional[str]):
    """시간 파라미터를 처리하는 헬퍼 함수"""
    start_dt = None
    end_dt = None
    
    if start_time:
        try:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="잘못된 시작 시간 형식")
            
    if end_time:
        try:
            end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="잘못된 종료 시간 형식")
    
    return start_dt, end_dt

@app.get("/log-stream")
async def log_stream():
    """로그 스트림 제공 (Server-Sent Events)"""
    async def event_generator():
        prev_logs = []
        
        # 큐에 있는 기존 로그 메시지를 먼저 전송
        while not log_queue.empty():
            try:
                log_entry = log_queue.get_nowait()
                prev_logs.append(log_entry)
            except queue.Empty:
                break
        
        for log_entry in prev_logs:
            yield f"data: {json.dumps(log_entry)}\n\n"
            
        # 새로운 로그 메시지를 기다리는 함수
        async def get_log():
            while True:
                try:
                    if not log_queue.empty():
                        log_entry = log_queue.get_nowait()
                        return log_entry
                except queue.Empty:
                    pass
                await asyncio.sleep(0.1)  # 0.1초마다 큐 확인
        
        # 60초 동안만 연결 유지
        start_time = time.time()
        while time.time() - start_time < 60:
            log_entry = await get_log()
            yield f"data: {json.dumps(log_entry)}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 