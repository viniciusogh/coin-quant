"""
Breakout LONG 전략 — "롱 초입"에서 검증할 모멘텀 paradigm.

진입 (LONG):
  1. 1h+4h 추세 = UP (1h EMA50 > EMA200, 4h EMA50 > EMA200)
  2. 현재 15m close > 최근 N봉 고점 (돌파)
  3. 거래량 > 평균 × VOL_MULT (진정성)
  4. 직전 봉이 high 안 깨고 있었음 (= 진짜 신규 돌파)

청산:
  SL = ATR × atr_sl_mult (진입 아래)
  TP = ATR × atr_tp_mult (진입 위)
  Time stop = 24h
"""

from dataclasses import dataclass


DEFAULT_BREAKOUT_PARAMS = {
    "lookback_high":   50,    # 최근 N봉 고점
    "vol_mult":        1.5,   # 거래량 배수
    "atr_sl_mult":     1.5,
    "atr_tp_mult":     3.0,   # R:R 1:2
    "risk_pct":        0.01,
    "max_pos_pct":     1.00,
    "leverage":        3,
    "time_stop_bars":  96,    # 15m × 96 = 24h
    "daily_loss_kill": 0.30,
}


def ema(values, span):
    if not values: return []
    k = 2 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def true_range(highs, lows, closes):
    out = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        h, l, pc = highs[i], lows[i], closes[i-1]
        out.append(max(h - l, abs(h - pc), abs(l - pc)))
    return out


def atr(highs, lows, closes, period=14):
    tr = true_range(highs, lows, closes)
    if len(tr) < period: return [None] * len(closes)
    out = [None] * (period - 1)
    seed = sum(tr[:period]) / period
    out.append(seed)
    k = 1 / period
    for i in range(period, len(tr)):
        out.append(out[-1] * (1 - k) + tr[i] * k)
    return out[:len(closes)]


def sma(values, period):
    if len(values) < period: return [None] * len(values)
    out = [None] * (period - 1)
    s = sum(values[:period])
    out.append(s / period)
    for i in range(period, len(values)):
        s += values[i] - values[i - period]
        out.append(s / period)
    return out


@dataclass
class Signal:
    side: str
    reason: str
    entry: float
    atr: float


def generate_breakout_signal(
    klines_15m: list,
    klines_1h: list,
    klines_4h: list,
    params: dict | None = None,
) -> Signal:
    p = {**DEFAULT_BREAKOUT_PARAMS, **(params or {})}

    if len(klines_15m) < max(p["lookback_high"] + 50, 60):
        return Signal("NONE", "데이터 부족", 0.0, 0.0)
    if len(klines_1h) < 210 or len(klines_4h) < 210:
        return Signal("NONE", "데이터 부족", 0.0, 0.0)

    # 1h 추세
    c1h = [float(k[4]) for k in klines_1h]
    e50_1h, e200_1h = ema(c1h, 50), ema(c1h, 200)
    trend_1h_up = e50_1h[-1] > e200_1h[-1]

    # 4h 추세
    c4h = [float(k[4]) for k in klines_4h]
    e50_4h, e200_4h = ema(c4h, 50), ema(c4h, 200)
    trend_4h_up = e50_4h[-1] > e200_4h[-1]

    if not (trend_1h_up and trend_4h_up):
        return Signal("NONE", "추세 UP 아님", 0.0, 0.0)

    # 15m 데이터
    h15 = [float(k[2]) for k in klines_15m]
    l15 = [float(k[3]) for k in klines_15m]
    c15 = [float(k[4]) for k in klines_15m]
    v15 = [float(k[5]) for k in klines_15m]

    lookback = p["lookback_high"]
    # 직전 봉까지의 lookback 고점 (현재 봉 제외)
    recent_high = max(h15[-lookback-1:-1])

    # 현재 봉 close가 그 고점 돌파
    if c15[-1] <= recent_high:
        return Signal("NONE", f"돌파 안 함 ({c15[-1]:.0f} vs {recent_high:.0f})", 0.0, 0.0)

    # 직전 봉도 돌파했으면 = 신선한 돌파 아님 (이미 한참 전부터 진행)
    if c15[-2] > recent_high * 0.999:
        return Signal("NONE", "이미 돌파 중 (신선도 X)", 0.0, 0.0)

    # 거래량 필터
    v_avg = sum(v15[-lookback:-1]) / (lookback - 1) if lookback > 1 else 0
    if v_avg == 0 or v15[-1] < v_avg * p["vol_mult"]:
        return Signal("NONE", f"거래량 부족 ({v15[-1]:.0f} < {v_avg*p['vol_mult']:.0f})", 0.0, 0.0)

    # ATR (sizing 용)
    atr_15 = atr(h15, l15, c15, 14)
    if atr_15[-1] is None:
        return Signal("NONE", "ATR 미계산", 0.0, 0.0)

    return Signal(
        "LONG",
        f"1h+4h UP | breakout {recent_high:.0f}→{c15[-1]:.0f} (+{(c15[-1]/recent_high-1)*100:.2f}%) "
        f"| vol {v15[-1]/v_avg:.1f}x | ATR {atr_15[-1]:.0f}",
        c15[-1], atr_15[-1],
    )


@dataclass
class EntryPlan:
    side: str
    entry: float
    sl: float
    tp: float
    qty: float
    notional_usdt: float
    risk_usdt: float
    sl_dist: float
    tp_dist: float


def plan_breakout_entry(signal: Signal, capital: float, params: dict | None = None) -> EntryPlan:
    p = {**DEFAULT_BREAKOUT_PARAMS, **(params or {})}

    sl_dist = signal.atr * p["atr_sl_mult"]
    tp_dist = signal.atr * p["atr_tp_mult"]
    sl = signal.entry - sl_dist
    tp = signal.entry + tp_dist

    risk_usdt = capital * p["risk_pct"]
    qty = risk_usdt / sl_dist if sl_dist > 0 else 0.0
    notional = qty * signal.entry

    max_notional = capital * p["max_pos_pct"] * p["leverage"]
    if notional > max_notional:
        scale = max_notional / notional
        qty *= scale
        notional = qty * signal.entry
        risk_usdt = qty * sl_dist

    return EntryPlan(
        side="LONG", entry=signal.entry, sl=sl, tp=tp, qty=qty,
        notional_usdt=notional, risk_usdt=risk_usdt,
        sl_dist=sl_dist, tp_dist=tp_dist,
    )
