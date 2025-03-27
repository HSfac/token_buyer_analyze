# Solana DEX 매집 지갑 탐지 시스템 - 프로젝트 가이드

## 1. 프로젝트 구조

```
solana-token-analyzer/
├── main.py                     # FastAPI 메인 애플리케이션
├── config.py                   # 설정 파일 (API 키, 환경 변수)
├── requirements.txt            # 프로젝트 의존성
├── .env                       # 환경 변수 설정
├── app/
│   ├── fetchers/             # 외부 API 연동 모듈
│   │   ├── birdeye.py        # Birdeye API 연동
│   │   └── helius.py         # Helius API 연동
│   ├── analyzers/            # 데이터 분석 모듈
│   │   └── buyer_classifier.py # 매수자 분류 로직
│   └── models/               # 데이터 모델
│       └── types.py          # Pydantic 모델 정의
└── README.md                  # 프로젝트 개요
```

## 2. 주요 기능

### 2.1 토큰 매수자 분석
- 특정 SPL 토큰의 WSOL 매수 트랜잭션 분석
- SOL 구간별 매수자 분류 (0~1, 1~5, 5~10, 10+ SOL)
- 각 구간별 매수자 수와 총 매수량 집계

### 2.2 API 엔드포인트
1. `/analyze/{token_address}`
   - 특정 토큰의 매수자 분석 수행
   - 파라미터:
     - `token_address`: 분석할 SPL 토큰 주소
     - `limit`: 분석할 트랜잭션 수 (기본값: 100)

2. `/token/{token_address}`
   - 토큰의 기본 정보 조회
   - 반환 정보: 주소, 이름, 심볼, 소수점 자릿수

## 3. 설치 및 실행 방법

### 3.1 환경 설정
1. Python 3.8 이상 설치
2. 가상환경 생성 및 활성화
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 또는
.\venv\Scripts\activate  # Windows
```

3. 의존성 설치
```bash
pip install -r requirements.txt
```

4. `.env` 파일 설정
```env
BIRDEYE_API_KEY=your_birdeye_api_key_here
HELIUS_API_KEY=your_helius_api_key_here
MONGODB_URI=mongodb://localhost:27017
```

### 3.2 서버 실행
```bash
python main.py
```
- 서버가 `http://localhost:8000`에서 실행됩니다.
- API 문서는 `http://localhost:8000/docs`에서 확인할 수 있습니다.

## 4. 데이터 모델

### 4.1 Transaction
- `signature`: 트랜잭션 시그니처
- `timestamp`: 트랜잭션 시간
- `buyer`: 매수자 지갑 주소
- `amount_sol`: 매수 금액 (SOL)

### 4.2 BuyerRange
- `wallets`: 해당 구간의 매수자 지갑 목록
- `count`: 매수자 수
- `total_sol`: 총 매수 금액 (SOL)

### 4.3 BuyerAnalysis
- `token`: 분석 대상 토큰 주소
- `snapshot_time`: 분석 시간
- `buyers_by_sol_range`: SOL 구간별 매수자 정보

## 5. 에러 처리

- API 호출 실패 시 500 에러 반환
- 잘못된 토큰 주소 입력 시 400 에러 반환
- API 키 미설정 시 401 에러 반환

## 6. 성능 고려사항

- 트랜잭션 분석 시 비동기 처리로 성능 최적화
- API 호출 제한 고려 (Birdeye, Helius)
- 대량의 트랜잭션 분석 시 메모리 사용량 주의

## 7. 향후 개선 사항

- 캐싱 시스템 도입
- 실시간 모니터링 기능
- 웹 대시보드 구현
- 알림 시스템 추가
- 매도 트랜잭션 분석 기능 