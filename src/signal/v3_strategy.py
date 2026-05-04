"""
v3 전략 — 5m Multi-TF RSI Mean Reversion + ATR Volatility Filter

철학: 큰 흐름(1h, 15m)은 추세 따라가되, 5m 단위 RSI 과매수/과매도 회복을
      변동성이 정상인 구간에서만 잡는다.

진입 (LONG, SHORT 거울):
  1. 1h EMA50 > EMA200 AND 격차 ≥ TREND_GAP_PCT
  2. 15m EMA50 > EMA200
  3. 5m ATR < ATR_50_avg × ATR_PEAK_MULT (변동성 폭증 차단)
  4. 5m RSI(14) 직전 봉 < RSI_OVERSOLD AND 현재 ≥ RSI_OVERSOLD (과매도 복귀)

리스크:
  - SL = 진입가 ± ATR × ATR_SL_MULT
  - TP = 진입가 ± ATR × ATR_TP_MULT  (R:R 결정)
  - position_qty = (capital × RISK_PCT) / SL_distance  (1회 손실 = 자본의 RISK_PCT)
  - 24h time stop, kill switch (일일 손실 30%)
"""

from dataclasses import dataclass


# 기본 파라미터 (grid search에서 일부 override됨)
DEFAULT_PARAMS = {
    "trend_gap_pct":   0.003,   # 1h EMA 격차 0.3% 이상 = 추세 명확
    "atr_peak_mult":   2.5,     # 현재 ATR < 평균 × 2.5 일 때만 진입
    "rsi_oversold":    30,      # 과매도 임계
    "rsi_overbought":  70,      # 과매수 임계
    "atr_sl_mult":     1.5,     # 손절 거리 = ATR × 1.5
    "atr_tp_mult":     2.25,    # 익절 거리 = ATR × 2.25 (R:R 1:1.5)
    "risk_pct":        0.01,    # 1회 거래 위험 = 자본의 1%
    "max_pos_pct":     1.00,    # 명목 포지션 cap = 자본의 100% (leverage 곱하면 300%)
    "leverage":        3,
    "time_stop_bars":  288,     # 5m × 288 = 24시간
    "daily_loss_kill": 0.30,    # 일일 누적 손실 30% 시 kill switch
    "side_filter":     None,    # None | "LONG_ONLY" | "SHORT_ONLY"
}


# ── 지표 계산 (numpy/list만 사용 — pandas 의존 X) ──────────────────
def ema(values: list, span: int) -> list:
    """지수이동평균. 첫 값은 시드."""
    if not values:
        return []
    k = 2 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: list, period: int = 14) -> list:
    """
    Wilder smoothing 방식 RSI. 길이가 closes와 동일.
    period+1번째부터 유효, 이전은 None.
    """
    if len(closes) < period + 1:
        return [None] * len(closes)

    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    # 초기 평균 (단순평균) — Wilder 표준
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    out = [None] * (period + 1)  # closes[0..period] 까지 None

    rs = avg_g / avg_l if avg_l > 0 else float("inf")
    out[period] = 100 - 100 / (1 + rs) if rs != float("inf") else 100.0

    # period+1 이후 Wilder 평균
    k = 1 / period
    for i in range(period, len(deltas)):
        avg_g = avg_g * (1 - k) + gains[i] * k
        avg_l = avg_l * (1 - k) + losses[i] * k
        rs = avg_g / avg_l if avg_l > 0 else float("inf")
        out.append(100 - 100 / (1 + rs) if rs != float("inf") else 100.0)

    return out[:len(closes)]


def true_range(highs: list, lows: list, closes: list) -> list:
    """TR — len = len(closes), 첫 값은 (high - low)."""
    out = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        h, l, pc = highs[i], lows[i], closes[i-1]
        out.append(max(h - l, abs(h - pc), abs(l - pc)))
    return out


def atr(highs: list, lows: list, closes: list, period: int = 14) -> list:
    """ATR — Wilder 평활. 길이 len(closes), 첫 period개는 None."""
    tr = true_range(highs, lows, closes)
    if len(tr) < period:
        return [None] * len(closes)

    out = [None] * (period - 1)
    seed = sum(tr[:period]) / period
    out.append(seed)
    k = 1 / period
    for i in range(period, len(tr)):
        out.append(out[-1] * (1 - k) + tr[i] * k)
    return out[:len(closes)]


def sma(values: list, period: int) -> list:
    """단순이동평균. 길이 len(values), 처음 period-1개 None."""
    if len(values) < period:
        return [None] * len(values)
    out = [None] * (period - 1)
    s = sum(values[:period])
    out.append(s / period)
    for i in range(period, len(values)):
        s += values[i] - values[i - period]
        out.append(s / period)
    return out


# ── 시그널 생성 ────────────────────────────────────────────────────
@dataclass
class Signal:
    side: str       # "LONG" | "SHORT" | "NONE"
    reason: str
    entry: float    # 진입가 (현재 5m close)
    atr: float      # 5m ATR (entry sizing에 사용)


def generate_signal(
    klines_5m: list,
    klines_15m: list,
    klines_1h: list,
    params: dict | None = None,
) -> Signal:
    """
    klines_*: list of [open_time_ms, open, high, low, close, volume, ...] (Binance 표준)
              — 항상 closed candles만. 가장 마지막 봉 = 직전 닫힘.

    Returns Signal. side=NONE 이면 진입 안 함.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    if len(klines_5m) < 60 or len(klines_15m) < 210 or len(klines_1h) < 210:
        return Signal("NONE", "데이터 부족", 0.0, 0.0)

    # 1h 추세 ────────────────────────────────────────────
    c1h = [float(k[4]) for k in klines_1h]
    e50_1h  = ema(c1h, 50)
    e200_1h = ema(c1h, 200)
    gap_1h = abs(e50_1h[-1] - e200_1h[-1]) / e200_1h[-1]
    if gap_1h < p["trend_gap_pct"]:
        return Signal("NONE", f"1h 횡보 (격차 {gap_1h*100:.2f}%)", 0.0, 0.0)
    trend_1h_up   = e50_1h[-1] > e200_1h[-1]
    trend_1h_down = e50_1h[-1] < e200_1h[-1]

    # 15m 추세 ──────────────────────────────────────────
    c15 = [float(k[4]) for k in klines_15m]
    e50_15  = ema(c15, 50)
    e200_15 = ema(c15, 200)
    trend_15_up   = e50_15[-1] > e200_15[-1]
    trend_15_down = e50_15[-1] < e200_15[-1]

    # 추세 일치 체크
    if not ((trend_1h_up and trend_15_up) or (trend_1h_down and trend_15_down)):
        return Signal("NONE", "1h/15m 추세 불일치", 0.0, 0.0)

    # 5m 데이터 ─────────────────────────────────────────
    h5 = [float(k[2]) for k in klines_5m]
    l5 = [float(k[3]) for k in klines_5m]
    c5 = [float(k[4]) for k in klines_5m]

    # ATR 변동성 필터
    atr_5 = atr(h5, l5, c5, 14)
    if atr_5[-1] is None:
        return Signal("NONE", "ATR 미계산", 0.0, 0.0)
    atr_avg = sma([a if a is not None else 0 for a in atr_5], 50)
    if atr_avg[-1] is None or atr_avg[-1] == 0:
        return Signal("NONE", "ATR 평균 미계산", 0.0, 0.0)
    if atr_5[-1] >= atr_avg[-1] * p["atr_peak_mult"]:
        return Signal("NONE", f"ATR 폭증 ({atr_5[-1]:.1f} vs avg {atr_avg[-1]:.1f})", 0.0, 0.0)

    # RSI(14) 임계 복귀
    rsi_5 = rsi(c5, 14)
    if rsi_5[-1] is None or rsi_5[-2] is None:
        return Signal("NONE", "RSI 미계산", 0.0, 0.0)

    entry = c5[-1]
    cur_atr = atr_5[-1]

    side_filter = p.get("side_filter")

    if (side_filter != "SHORT_ONLY"
            and trend_1h_up and trend_15_up
            and rsi_5[-2] < p["rsi_oversold"]
            and rsi_5[-1] >= p["rsi_oversold"]):
        return Signal(
            "LONG",
            f"1h+15m UP | RSI {rsi_5[-2]:.1f}→{rsi_5[-1]:.1f} 과매도 복귀 | ATR {cur_atr:.1f}",
            entry, cur_atr,
        )

    if (side_filter != "LONG_ONLY"
            and trend_1h_down and trend_15_down
            and rsi_5[-2] > p["rsi_overbought"]
            and rsi_5[-1] <= p["rsi_overbought"]):
        return Signal(
            "SHORT",
            f"1h+15m DOWN | RSI {rsi_5[-2]:.1f}→{rsi_5[-1]:.1f} 과매수 복귀 | ATR {cur_atr:.1f}",
            entry, cur_atr,
        )

    return Signal("NONE", f"RSI {rsi_5[-1]:.1f} 진입조건 미달", 0.0, 0.0)


# ── 진입 시 SL/TP/사이즈 계산 ──────────────────────────────────────
@dataclass
class EntryPlan:
    side: str
    entry: float
    sl: float
    tp: float
    qty: float            # BTC 수량
    notional_usdt: float  # 명목 포지션 (entry × qty)
    risk_usdt: float      # 손절 시 잃을 금액 (자본의 risk_pct)
    sl_dist: float
    tp_dist: float


def plan_entry(signal: Signal, capital: float, params: dict | None = None) -> EntryPlan:
    """
    동적 포지션 사이징:
        risk_amount = capital × risk_pct
        qty         = risk_amount / sl_dist
        명목가가 max_pos_pct × 레버리지를 초과하면 cap.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    sl_dist = signal.atr * p["atr_sl_mult"]
    tp_dist = signal.atr * p["atr_tp_mult"]

    if signal.side == "LONG":
        sl = signal.entry - sl_dist
        tp = signal.entry + tp_dist
    else:
        sl = signal.entry + sl_dist
        tp = signal.entry - tp_dist

    risk_usdt = capital * p["risk_pct"]
    qty = risk_usdt / sl_dist if sl_dist > 0 else 0.0
    notional = qty * signal.entry

    # 명목 cap (자본 × max_pos × leverage)
    max_notional = capital * p["max_pos_pct"] * p["leverage"]
    if notional > max_notional:
        scale = max_notional / notional
        qty *= scale
        notional = qty * signal.entry
        risk_usdt = qty * sl_dist  # 실제 위험액 (cap 적용 후 작아짐)

    return EntryPlan(
        side=signal.side, entry=signal.entry,
        sl=sl, tp=tp, qty=qty, notional_usdt=notional,
        risk_usdt=risk_usdt, sl_dist=sl_dist, tp_dist=tp_dist,
    )
