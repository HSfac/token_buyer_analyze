# 👊 Solana DEX 매집 지갑 탐지 시스템 - PRD (Product Requirements Document)

## 1. 프로젝트 개요

- **프로젝트 명**: Solana DeFi Swap 매집 지갑 탐지 시스템 (Token Buyer Analyzer)
- **목적**: 특정 SPL 토큰을 기준으로, 해당 토큰의 DeFi Swap 트랜잭션을 분석하여 매수 세력(특히 WSOL로 구매한 지갑)을 식별하고 SOL 단위로 구간별로 분류하여 매집 규모를 시각적으로 파악하는 시스템 개발
- **사용자 대상**: 크립토 트레이더, DEX 분석가, 프로젝트 팀, 퀀트 전략가
- **활용 목적**: 초반 매수세 분석, 고래 지갑 추적, 유의미한 매수세 분포 관찰
- **입력 방식**: SPL 토큰 주소 수동 입력

---

## 2. 핵심 기능 요약 (토큰 기준 SOL 매수 분석)

```text
1. 사용자로부터 SPL 토큰 주소 입력
    ↓
2. [Birdeye] 해당 토큰 기준 최근 Signature 리스트 수집
    ↓
3. [Helius] Signature별 트랜잭션 상세 조회
    ↓
4. Swap 이벤트 필터링:
   - tokenIn == WSOL
   - tokenOut == 입력한 토큰
    ↓
5. 매수 지갑 및 amountIn(SOL) 추출
    ↓
6. 다음 기준으로 지갑 분류 및 집계:
   - 0~1 SOL
   - 1~5 SOL
   - 5~10 SOL
   - 10 SOL 이상 (선택)
    ↓
7. 구간별:
   - 참여 지갑 수
   - 총 SOL 매수량
   - 지갑 리스트 출력
```

---

## 3. 기능 상세 명세

### 3.1 토큰 주소 입력
- 사용자로부터 SPL Token 주소 수동 입력 (웹 UI 또는 CLI)

### 3.2 Birdeye 연동 (Signature 수집)
- API: `/token/{address}/txs`
- 입력한 토큰 기준 최근 트랜잭션 Signature 목록 확보
- 시간 필터링 또는 최신 100~1000건 수집 가능

### 3.3 Helius 연동 (Signature 분석)
- API: `/v0/transactions/{signature}`
- 각 Signature 별로 다음 항목 분석:
  - type == 'SWAP'
  - events.swap.tokenIn == WSOL
  - events.swap.tokenOut == 입력한 토큰
  - tokenTransfers[].fromUserAccount / signer
  - amountIn (SOL 기준 매수량)

### 3.4 지갑별 SOL 매수량 집계 및 분류
- 동일 지갑의 매수량 누적
- 아래 구간으로 분류:
  - 0~1 SOL
  - 1~5 SOL
  - 5~10 SOL
  - 10 SOL 이상

### 3.5 결과 출력 (JSON or 화면)
- 각 구간별:
  - 매수한 지갑 리스트
  - 총 지갑 수
  - 누적 SOL 매수량

---

## 4. 출력 예시

```json
{
  "token": "SPL_TOKEN_ADDRESS",
  "snapshot_time": "2025-03-24T11:00:00Z",
  "buyers_by_sol_range": {
    "0_1": {
      "wallets": ["A...", "B..."],
      "count": 2,
      "total_sol": 0.87
    },
    "1_5": {
      "wallets": ["C...", "D..."],
      "count": 2,
      "total_sol": 3.9
    },
    "5_10": {
      "wallets": ["E..."],
      "count": 1,
      "total_sol": 6.1
    }
  }
}
```

---

## 5. 기술 스택

| 항목 | 기술 |
|------|------|
| 언어 | Python |
| API 서버 | FastAPI (선택) |
| 외부 API | Birdeye REST, Helius REST |
| 데이터 저장 | MongoDB 또는 임시 메모리 분석 |
| 분석 실행 | CLI or 주기적 실행 스크립트 |

---

## 6. 프로젝트 디렉토리 구조 예시

```bash
solana-token-analyzer/
├── main.py                     # 실행 진입점 (CLI 또는 FastAPI)
├── config.py                   # 설정 파일 (API 키 등)
├── requirements.txt
├── app/
│   ├── fetchers/
│   │   ├── birdeye.py          # Birdeye Signature 수집 로직
│   │   └── helius.py           # Helius 트랜잭션 분석 로직
│   ├── analyzers/
│   │   └── buyer_classifier.py # 지갑별 매수량 집계 및 분류
│   ├── utils/
│   │   └── time.py             # 시간 처리 유틸
│   └── models/
│       └── types.py            # Pydantic 모델 및 데이터 구조 정의
└── data/
    └── snapshots.json          # 결과 저장 파일 (선택)
```

---

## 7. 향후 확장 방향
- 초기 매수 지갑의 매도 여부 트래킹
- 지갑별 첫 매수 후 홀드 기간 분석
- 알림 기능 (특정 구간 이상 매수 감지 시)
- 웹 대시보드 및 실시간 시각화 연동

---

**✅ 요약**: Birdeye → Signature 수집, Helius → 트랜잭션 상세 분석을 조합해, WSOL로 특정 SPL 토큰을 매수한 지갑들을 SOL 단위로 분류/집계하는 구조. 이 시스템을 통해 특정 토큰의 매수세 분포 및 주요 매수자 추적이 가능함.

