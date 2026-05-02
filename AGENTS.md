# 프로젝트 컨텍스트 & 규칙 — coin-quant

## 프로젝트 개요

비트코인(및 향후 ETH 등 메이저 크립토) **자동 매매 시스템** 구축. 24/7 실시간 데이터 수집 → 시그널 생성 → 리스크 관리 → 거래소 실거래 → 모니터링·로그.

**선행 결정 사항** (이전 대화에서 합의):
- 거래소: Binance Futures (USDT-M Perpetual) — 백업으로 OKX/Bybit
- 자산: BTC/USDT 단일 자산으로 시작, 검증 후 ETH 추가
- 레버리지: 5x 상한 (절대 10x 이상 X)
- 시드 시작: ₩50K~100K 권장 (₩10K 는 수수료 비중 너무 큼)
- 운영 단계: Phase 1 데이터 → Phase 2 페이퍼 트레이딩 → Phase 3 리스크 관리 → Phase 4 작은 시드 실거래 → Phase 5 확대

## 자매 프로젝트 (참고만, 코드 의존 X)

`/Users/vinicius/Desktop/퀀트스코어/` — KOSPI/KOSDAQ/US 멀티팩터 + Quality 모델

**재사용 가능한 패턴** (참고용 — 직접 import 하지 말고 필요 시 코드 베껴오기):
- `quality.py`, `수급.py` : KIS API 연동·캐시·Notion 업로드 패턴
- `us_quant.py` : EMA 평탄화·순위 변동·yfinance 처리
- `docs/KIS_API_REFERENCE.md` : 단일 출처 API 문서 패턴 — Binance 도 동일하게 `docs/BINANCE_API.md` 만들 것
- `_cz`, `_cz_by_sector`, `smooth_with_ema`, `fmt_pct`, `fmt_rank_change` 헬퍼들 — 필요하면 가져오기

**가져오지 말 것**:
- 일별 cron + Notion 업로드 모델 — 실시간 봇과 완전히 다름
- 멀티팩터 점수 산식 — 주식용. 코인은 다른 시그널 (RSI, MA, 변동성, 펀딩비 등)
- 시총 필터링 등 주식 특화 로직

## Phase 1 시작점 — 우선순위

1. **Binance API 키 발급** (testnet 먼저)
2. **`docs/BINANCE_API.md` 작성** — 사용할 엔드포인트 정리 (가격 WebSocket, OHLCV REST, 주문, 포지션, 레버리지)
3. **데이터 수집 모듈** — REST 로 OHLCV 과거 데이터 + WebSocket 실시간 가격
4. **백테스트 환경** — 간단한 RSI 전략으로 시작
5. **Telegram 봇** 셋업 — 알림용 (시그널·체결·오류)

## 기술 스택 (제안, 변경 가능)

- 언어: **Python 3.11+** (퀀트스코어와 동일 — 학습 비용 X)
- Binance: `python-binance` 라이브러리
- WebSocket: `websockets` 또는 라이브러리 내장
- 백테스트: `backtrader` 또는 `vectorbt` (간단하면 직접 numpy)
- DB: SQLite (시작) → PostgreSQL (확장 시)
- 호스팅: 본인 Mac (개발) → VPS (실거래 시 24/7)
- 알림: Telegram Bot API
- 시크릿: `.env` 파일 + `python-dotenv`

## 핵심 제약사항 (반드시 지킬 것)

### 안전
- **실거래 전 페이퍼 트레이딩 1개월 이상 의무**
- **레버리지 5x 상한** — 어떤 경우에도 초과 X
- **일일 손실 한도 (Kill Switch)**: 시드의 30% 손실 시 봇 자동 정지
- **시크릿은 .env 만**, 코드에 절대 하드코딩 X (퀀트스코어 NOTION_API_KEY 같은 실수 반복 금지)
- **API 키는 출금 권한 X**, 거래 권한만

### 거래
- **모든 진입에 손절·익절 동시 입력 (OCO)** — 시장가 단독 진입 금지
- **포지션 사이즈는 시드의 일정 비율** 로만 (예: 한 진입당 시드의 20% 이내)
- **레인지장(횡보) 진입 금지** — 추세 시작 시점만

### 디버그·운영
- **모든 결정·체결 SQLite 로그** — "왜 이 진입했나" 추적 가능
- **Telegram 알림** — 체결/오류/일일 P&L 자동 발송
- **백테스트 결과와 실거래 결과 비교 대시보드**

## 프로젝트 구조 (제안)

```
coin-quant/
├── AGENTS.md             # 이 파일
├── docs/
│   ├── BINANCE_API.md    # Binance API 단일 출처
│   └── STRATEGY.md       # 시그널 로직 문서
├── src/
│   ├── data/             # 데이터 수집 (REST, WebSocket)
│   ├── signal/           # 시그널 생성 (지표 + 룰)
│   ├── risk/             # 포지션 사이징, 손절/익절
│   ├── execution/        # 주문 발송, 체결 추적
│   ├── monitor/          # Telegram 알림, 로그
│   └── backtest/         # 백테스트 엔진
├── tests/                # 단위 테스트
├── .env.example          # API 키 템플릿
├── requirements.txt
└── main.py               # 진입점
```

## 절대 안 할 것

- 알트코인 단타 (펌프앤덤프, MEV 봇, 유동성 부족) — BTC/ETH 만
- 검증 안 된 전략 실거래
- 페이퍼 트레이딩 건너뛰기
- 일일 손실 한도 무시
- "한 번만 더" 추가 베팅 (도박꾼 망하는 패턴)
- 자고 일어나서 봇 강제 종료 후 수동 매매 (시스템 신뢰 X)

## 진행 현황 (2026-05-02 기준)

### 완료된 작업
- **Binance 계정**: 가입 + KYC Verified + Futures 계정 활성화
- **API 키**: 발급 완료 (Enable Futures ✅, IP 제한 210.100.191.127 ✅)
- **프로젝트 구조**: AGENTS.md 기준 전체 폴더 구조 생성 완료
- **라이브러리**: venv 환경 + requirements.txt 설치 완료
- **API 연결**: `src/data/fetcher.py` — BTC 현재가/OHLCV/잔고 조회 정상 동작 확인
- **시그널 모듈**: `src/signal/indicators.py` (RSI, EMA), `src/signal/rsi_strategy.py` (전략 로직)
- **백테스트 엔진**: `src/backtest/engine.py` 구현 및 실행 완료

### 백테스트 결과 (1차, 2026-02-28 ~ 2026-05-02, 1h x1500봉)
| 항목 | 값 |
|---|---|
| 총수익률 | -11.97% |
| 승률 | 7.7% (13건 중 1건 익절) |
| MDD | -11.97% |
| 주요 원인 | 4월 초 급등장에서 숏 시그널 연속 4번 손절 |

### 다음 할 일 (우선순위)
1. **페이퍼 트레이딩 1개월** — GitHub Actions로 자동 실행 중, 결과 축적
2. **전략 추가 검증** — 더 긴 기간 데이터로 재백테스트
3. **주문 실행 모듈** (`src/execution/`) — 실제 주문 발송 (Phase 4)
4. **Oracle Cloud VPS** — 카카오페이 가상카드 발급 후 24/7 안정적 운영

### 현재 파일 구조
```
coin-quant/
├── AGENTS.md
├── .env                       # API 키 (gitignore됨)
├── .env.example
├── .gitignore
├── requirements.txt
├── main.py                    ✅ 로컬 실행용
├── bot_runner.py              ✅ GitHub Actions용
├── position.json              ✅ 페이퍼 트레이딩 상태
├── .github/workflows/bot.yml  ✅ 15분마다 자동 실행
├── docs/
│   ├── BINANCE_API.md
│   └── STRATEGY.md
├── src/
│   ├── data/fetcher.py        ✅ 완료
│   ├── signal/
│   │   ├── indicators.py      ✅ 완료
│   │   ├── ema_strategy.py    ✅ 완료 (현재 사용)
│   │   ├── breakout_strategy.py ✅ 완료
│   │   └── rsi_strategy.py    ✅ 완료
│   ├── backtest/engine.py     ✅ 완료
│   ├── risk/manager.py        ✅ 완료
│   ├── monitor/telegram_bot.py ✅ 완료
│   └── execution/             🔲 미구현 (Phase 4)
└── tests/
```

### GitHub
- Repo: https://github.com/viniciusogh/coin-quant
- Secrets 등록 완료 (BINANCE_API_KEY, BINANCE_SECRET_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
- Actions: 15분마다 자동 실행 → 텔레그램 알림

### venv 활성화
```bash
cd /Users/vinicius/Desktop/coin-quant
source venv/bin/activate
```

## 새 대화 시작 시

이 폴더(`/Users/vinicius/Desktop/coin-quant/`) 에서 Claude 를 새로 시작하면 이 AGENTS.md 가 자동 로드됩니다. 먼저 이 문서 + 자매 프로젝트 (`/Users/vinicius/Desktop/퀀트스코어/AGENTS.md`) 둘 다 읽으면 양쪽 맥락 파악 가능.

**첫 대화 추천 시작점**:
> "AGENTS.md 봤어. 백테스트 결과 승률 7.7%라 전략 개선이 필요해. 파라미터 조정이랑 더 긴 기간 데이터 수집부터 시작하자."
