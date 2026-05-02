# Binance Futures API 레퍼런스

> **규칙**: 새 엔드포인트 사용 전 이 문서 먼저 확인. 없으면 raw 응답 확인 후 여기에 추가하고 사용.

## 기본 정보

| 항목 | 값 |
|---|---|
| Base URL (실거래) | `https://fapi.binance.com` |
| Base URL (테스트넷) | `https://testnet.binancefuture.com` |
| WebSocket (실거래) | `wss://fstream.binance.com` |
| WebSocket (테스트넷) | `wss://stream.binancefuture.com` |
| 시장 | USDT-M Perpetual Futures |
| 기본 심볼 | `BTCUSDT` |

---

## REST API 엔드포인트

### 시세 / 데이터

#### OHLCV (캔들 데이터)
- **Endpoint**: `GET /fapi/v1/klines`
- **인증**: 불필요
- **파라미터**:
  - `symbol`: `BTCUSDT`
  - `interval`: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`
  - `limit`: 최대 1500 (기본 500)
  - `startTime`, `endTime`: 밀리초 타임스탬프 (선택)
- **응답 필드** (배열):
  ```
  [0]  open_time
  [1]  open
  [2]  high
  [3]  low
  [4]  close
  [5]  volume
  [6]  close_time
  [7]  quote_asset_volume
  [8]  number_of_trades
  [9]  taker_buy_base_volume
  [10] taker_buy_quote_volume
  [11] ignore
  ```

#### 현재가
- **Endpoint**: `GET /fapi/v1/ticker/price`
- **파라미터**: `symbol`
- **응답 키**: `price` (str)

#### 24h 통계
- **Endpoint**: `GET /fapi/v1/ticker/24hr`
- **파라미터**: `symbol`
- **주요 응답 키**: `lastPrice`, `priceChangePercent`, `volume`, `highPrice`, `lowPrice`

---

### 계정 / 포지션 (인증 필요)

#### 계정 잔고
- **Endpoint**: `GET /fapi/v2/account`
- **인증**: HMAC SHA256 서명
- **주요 응답 키**:
  - `totalWalletBalance`: 총 잔고 (USDT)
  - `totalUnrealizedProfit`: 미실현 손익
  - `availableBalance`: 가용 잔고
  - `assets[].asset`: 자산명
  - `assets[].walletBalance`: 자산 잔고

#### 포지션 조회
- **Endpoint**: `GET /fapi/v2/positionRisk`
- **파라미터**: `symbol` (선택)
- **주요 응답 키**:
  - `symbol`: 심볼
  - `positionAmt`: 포지션 수량 (양수=롱, 음수=숏)
  - `entryPrice`: 진입가
  - `unrealizedProfit`: 미실현 손익
  - `leverage`: 현재 레버리지
  - `liquidationPrice`: 청산가

#### 레버리지 설정
- **Endpoint**: `POST /fapi/v1/leverage`
- **파라미터**: `symbol`, `leverage` (1~125)
- **주의**: 최대 5 (AGENTS.md 절대 제약)

---

### 주문 (인증 필요)

#### 주문 생성
- **Endpoint**: `POST /fapi/v1/order`
- **주요 파라미터**:
  - `symbol`: `BTCUSDT`
  - `side`: `BUY` / `SELL`
  - `type`: `MARKET` / `LIMIT` / `STOP_MARKET` / `TAKE_PROFIT_MARKET`
  - `quantity`: 수량 (BTC 단위)
  - `price`: 지정가 (LIMIT만)
  - `stopPrice`: 트리거가 (STOP_MARKET, TAKE_PROFIT_MARKET)
  - `reduceOnly`: `true` (포지션 청산용)
  - `timeInForce`: `GTC` (Good Till Cancel)

#### OCO 대신 Hedge 방식 (손절 + 익절 동시)
Binance Futures는 전통적 OCO 미지원. 대신:
1. 진입 주문 체결 후
2. `STOP_MARKET` (손절) + `TAKE_PROFIT_MARKET` (익절) 동시 발주
3. 어느 쪽이든 체결 시 나머지는 `reduceOnly=true`이므로 자동 무효화

#### 주문 취소
- **Endpoint**: `DELETE /fapi/v1/order`
- **파라미터**: `symbol`, `orderId`

#### 미체결 주문 전체 취소
- **Endpoint**: `DELETE /fapi/v1/allOpenOrders`
- **파라미터**: `symbol`

#### 포지션 전체 청산 (시장가)
```python
# positionAmt > 0 (롱) → SELL MARKET reduceOnly
# positionAmt < 0 (숏) → BUY MARKET reduceOnly
```

---

## WebSocket 스트림

### 실시간 가격 (Ticker)
- **URL**: `wss://fstream.binance.com/ws/btcusdt@ticker`
- **주요 응답 키**: `c` (현재가), `p` (가격변동), `v` (거래량)

### 캔들 스트림 (실시간 OHLCV)
- **URL**: `wss://fstream.binance.com/ws/btcusdt@kline_1m`
- **응답 키**: `k.o`, `k.h`, `k.l`, `k.c`, `k.v`, `k.x` (캔들 완성 여부)

### 유저 데이터 스트림 (체결 알림)
1. `POST /fapi/v1/listenKey` → `listenKey` 발급
2. `wss://fstream.binance.com/ws/{listenKey}` 연결
3. 30분마다 `PUT /fapi/v1/listenKey` (keepalive)
- **이벤트 타입**: `ORDER_TRADE_UPDATE` (체결), `ACCOUNT_UPDATE` (잔고변동)
- **ORDER_TRADE_UPDATE 주요 키**:
  - `o.s`: 심볼
  - `o.S`: 사이드 (`BUY`/`SELL`)
  - `o.X`: 주문 상태 (`FILLED`, `CANCELED` 등)
  - `o.ap`: 평균 체결가
  - `o.rp`: 실현 손익

---

## python-binance 클라이언트 초기화

```python
from binance.client import Client
from binance.enums import *
import os

client = Client(
    api_key=os.getenv("BINANCE_API_KEY"),
    api_secret=os.getenv("BINANCE_SECRET_KEY"),
)
# 선물 거래는 futures_* 메서드 사용
# 예: client.futures_klines(), client.futures_create_order()
```

---

## Rate Limit

| 타입 | 한도 |
|---|---|
| 요청 무게 | 2400 / 분 |
| 주문 | 1200 / 분 |
| OHLCV 1회 요청 무게 | 가중치 1~10 (limit 크기에 따라) |

> 자동화 시 요청 사이에 `time.sleep(0.1)` 권장

---

## 에러 코드 (주요)

| 코드 | 의미 |
|---|---|
| -1121 | Invalid symbol |
| -2019 | Insufficient margin |
| -4061 | Order's position side does not match user's setting |
| -1111 | Precision is over the maximum defined for this asset |

> 새 에러 코드 만나면 여기에 추가
