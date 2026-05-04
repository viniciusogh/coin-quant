"""
coin-quant bot v4 — SHORT-only Mean Reversion + Daily SMA(200) Regime Gate.

전략 (백테스트 검증):
  Daily 가격 < SMA(200) → BEAR regime → 봇 가동
  Daily 가격 ≥ SMA(200) → BULL regime → 봇 휴식 (현물 보유 가정)

진입 (SHORT only):
  1. 1h+4h 추세 = DOWN (1h EMA50<EMA200, gap≥0.3%; 4h EMA50<EMA200)
  2. 15m ATR < 50봉 평균 × 2.5 (변동성 안전)
  3. 15m RSI(14) 직전 봉 > 70 → 현재 ≤ 70 (과매수 복귀)

리스크:
  SL = 진입가 + ATR × 2.0
  TP = 진입가 - ATR × 6.0  (R:R 1:3)
  포지션 사이즈 = (자본 × 1%) / SL_distance   (= 손절 시 자본 1% 손실)
  명목 cap = 자본 × 100% × leverage(3) = 자본의 300%
  Time stop: 24h (96 × 15m bars)
  Daily kill switch: 일일 손실 30%

백테스트 (2025-10 ~ 2026-05 BEAR phase 209일):
  +15.92%, WR 28.3%, MDD -8.9%, 60 trades (= 약 1회/3.5일)
"""

import os
import json
import time
import hmac
import hashlib
import requests
from datetime import datetime, timezone, date

# ── 환경변수 ──────────────────────────────────────────────
BINANCE_API_KEY    = os.environ["BINANCE_API_KEY"]
BINANCE_SECRET_KEY = os.environ["BINANCE_SECRET_KEY"]
TELEGRAM_TOKEN     = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
SYMBOL     = os.environ.get("SYMBOL", "BTCUSDT")
SEED_USDT  = float(os.environ.get("SEED_USDT", 100))
LEVERAGE   = int(os.environ.get("LEVERAGE", 3))

# 전략 파라미터 (백테스트 best)
TREND_GAP_PCT    = 0.003   # 1h EMA 격차 0.3% 이상
ATR_PEAK_MULT    = 2.5     # ATR 폭증 차단
RSI_PERIOD       = 14
RSI_OVERBOUGHT   = 70
ATR_SL_MULT      = 2.0
ATR_TP_MULT      = 6.0     # R:R 1:3
RISK_PCT         = 0.01    # 자본 1% 위험 한도
MAX_POS_PCT      = 1.00    # 명목 cap (× leverage = 300%)
DAILY_LOSS_LIMIT = 0.30
TIME_STOP_BARS   = 96      # 15m × 96 = 24h
STATUS_INTERVAL  = 8       # 2시간마다 (15분 × 8)
SMA_DAILY_PERIOD = 200
REGIME_BUFFER    = 0.02    # ±2% 내는 NEUTRAL

STATE_FILE = os.path.expanduser("~/.coin-quant/position.json")
os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)


# ── 텔레그램 ─────────────────────────────────────────────
def tg(msg: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception as e:
        print(f"Telegram 오류: {e}")


# ── State ────────────────────────────────────────────────
def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {
            "position": None, "capital": SEED_USDT,
            "daily_start": SEED_USDT, "today": "",
            "daily_trades": 0, "daily_pnl": 0.0,
            "kill_switch": False, "total_trades": 0,
            "wins": 0, "losses": 0,
            "last_signal_candle": None, "run_count": 0,
            "current_regime": "UNKNOWN",
        }

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Binance REST (공개 엔드포인트만 사용 — 인증 불필요) ─
def get_klines(interval: str, limit: int) -> list:
    r = requests.get(
        "https://fapi.binance.com/fapi/v1/klines",
        params={"symbol": SYMBOL, "interval": interval, "limit": limit},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()

def get_price() -> float:
    r = requests.get(
        "https://fapi.binance.com/fapi/v1/ticker/price",
        params={"symbol": SYMBOL}, timeout=10,
    )
    r.raise_for_status()
    return float(r.json()["price"])


# ── 지표 ─────────────────────────────────────────────────
def ema(values: list, span: int) -> list:
    if not values: return []
    k = 2 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

def sma(values: list, period: int) -> list:
    if len(values) < period: return [None] * len(values)
    out = [None] * (period - 1)
    s = sum(values[:period])
    out.append(s / period)
    for i in range(period, len(values)):
        s += values[i] - values[i - period]
        out.append(s / period)
    return out

def true_range(highs: list, lows: list, closes: list) -> list:
    out = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        h, l, pc = highs[i], lows[i], closes[i-1]
        out.append(max(h - l, abs(h - pc), abs(l - pc)))
    return out

def atr(highs: list, lows: list, closes: list, period: int = 14) -> list:
    tr = true_range(highs, lows, closes)
    if len(tr) < period: return [None] * len(closes)
    out = [None] * (period - 1)
    seed = sum(tr[:period]) / period
    out.append(seed)
    k = 1 / period
    for i in range(period, len(tr)):
        out.append(out[-1] * (1 - k) + tr[i] * k)
    return out[:len(closes)]

def rsi(closes: list, period: int = 14) -> list:
    if len(closes) < period + 1:
        return [None] * len(closes)
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    rs = avg_g / avg_l if avg_l > 0 else float("inf")
    out = [None] * (period + 1)
    out[period] = 100 - 100/(1+rs) if rs != float("inf") else 100.0
    k = 1 / period
    for i in range(period, len(deltas)):
        avg_g = avg_g * (1-k) + gains[i] * k
        avg_l = avg_l * (1-k) + losses[i] * k
        rs = avg_g / avg_l if avg_l > 0 else float("inf")
        out.append(100 - 100/(1+rs) if rs != float("inf") else 100.0)
    return out[:len(closes)]


# ── Daily Regime Gate ────────────────────────────────────
def get_regime(klines_daily: list) -> tuple[str, float, float]:
    """일봉 가격 vs SMA(200)로 regime 판정."""
    closes = [float(k[4]) for k in klines_daily]
    if len(closes) < SMA_DAILY_PERIOD:
        return "UNKNOWN", 0.0, 0.0
    sma200 = sma(closes, SMA_DAILY_PERIOD)
    cur = closes[-1]
    cur_sma = sma200[-1]
    if cur_sma is None:
        return "UNKNOWN", cur, 0.0
    deviation = (cur - cur_sma) / cur_sma
    if deviation > REGIME_BUFFER:
        return "BULL", cur, cur_sma
    if deviation < -REGIME_BUFFER:
        return "BEAR", cur, cur_sma
    return "NEUTRAL", cur, cur_sma


# ── Signal (SHORT-only) ──────────────────────────────────
def generate_short_signal(k15m: list, k1h: list, k4h: list):
    """closed candle 기준. 마지막 봉은 진행 중일 수 있어 [-2]까지 안전.
    여기선 단순화로 마지막 봉을 사용(15m 봉 닫힌 직후 cron)."""
    if len(k15m) < 60 or len(k1h) < 210 or len(k4h) < 210:
        return "NONE", "데이터 부족", None, None

    # 1h 추세
    c1h = [float(k[4]) for k in k1h]
    e50_1h, e200_1h = ema(c1h, 50), ema(c1h, 200)
    gap_1h = abs(e50_1h[-1] - e200_1h[-1]) / e200_1h[-1]
    if gap_1h < TREND_GAP_PCT:
        return "NONE", f"1h 횡보 (격차 {gap_1h*100:.2f}%)", None, None
    trend_1h_down = e50_1h[-1] < e200_1h[-1]

    # 4h 추세
    c4h = [float(k[4]) for k in k4h]
    e50_4h, e200_4h = ema(c4h, 50), ema(c4h, 200)
    trend_4h_down = e50_4h[-1] < e200_4h[-1]

    if not (trend_1h_down and trend_4h_down):
        return "NONE", "1h/4h DOWN 추세 아님", None, None

    # 15m 데이터
    h15 = [float(k[2]) for k in k15m]
    l15 = [float(k[3]) for k in k15m]
    c15 = [float(k[4]) for k in k15m]

    atr_15 = atr(h15, l15, c15, 14)
    if atr_15[-1] is None:
        return "NONE", "ATR 미계산", None, None
    atr_avg = sma([a if a is not None else 0 for a in atr_15], 50)
    if atr_avg[-1] is None or atr_avg[-1] == 0:
        return "NONE", "ATR 평균 미계산", None, None
    if atr_15[-1] >= atr_avg[-1] * ATR_PEAK_MULT:
        return "NONE", f"ATR 폭증 ({atr_15[-1]:.0f} vs avg {atr_avg[-1]:.0f})", None, None

    rsi_15 = rsi(c15, RSI_PERIOD)
    if rsi_15[-1] is None or rsi_15[-2] is None:
        return "NONE", "RSI 미계산", None, None

    if rsi_15[-2] > RSI_OVERBOUGHT and rsi_15[-1] <= RSI_OVERBOUGHT:
        reason = (f"1h+4h DOWN | RSI {rsi_15[-2]:.1f}→{rsi_15[-1]:.1f} 과매수 복귀 "
                  f"| ATR {atr_15[-1]:.0f}")
        return "SHORT", reason, c15[-1], atr_15[-1]

    return "NONE", f"RSI {rsi_15[-1]:.1f} 진입조건 미달", None, None


# ── 메인 ─────────────────────────────────────────────────
def main():
    now = datetime.now(timezone.utc)
    today_str = str(date.today())
    kst_str = f"{(now.hour + 9) % 24:02d}:{now.minute:02d} KST"

    state = load_state()
    state["run_count"] = state.get("run_count", 0) + 1
    run_count = state["run_count"]
    print(f"[{now.strftime('%Y-%m-%d %H:%M')} UTC] #{run_count} 실행")

    # 일일 초기화
    if state["today"] != today_str:
        prev_capital = state["capital"]
        prev_trades  = state.get("total_trades", 0)
        prev_wins    = state.get("wins", 0)
        state.update({
            "today": today_str, "daily_start": prev_capital,
            "daily_trades": 0, "daily_pnl": 0.0, "kill_switch": False,
        })
        wr = prev_wins / prev_trades * 100 if prev_trades else 0
        if state["today"]:  # 첫 실행 X
            tg(
                f"📅 <b>새 날 시작</b> ({today_str})\n"
                f"━━━━━━━━━━━━━━━\n"
                f"어제까지 잔고: <b>${prev_capital:.2f}</b>\n"
                f"누적 거래: {prev_trades}건 | 승률 {wr:.1f}%"
            )

    if state["kill_switch"]:
        save_state(state)
        return

    # ── Daily Regime ──────────
    k_daily = get_klines("1d", 250)
    regime, cur_price, sma_d = get_regime(k_daily)
    prev_regime = state.get("current_regime", "UNKNOWN")
    state["current_regime"] = regime

    # regime 변경 알림
    if prev_regime != "UNKNOWN" and prev_regime != regime:
        tg(
            f"🔄 <b>Regime 변경</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{prev_regime} → <b>{regime}</b>\n"
            f"BTC ${cur_price:,.0f} | Daily SMA(200) ${sma_d:,.0f}\n"
            f"{'봇 가동 ▶️' if regime == 'BEAR' else '봇 휴식 ⏸'}"
        )

    if regime != "BEAR":
        save_state(state)
        # 2시간마다 휴식 알림
        if run_count % STATUS_INTERVAL == 0:
            tg(
                f"⏸ <b>봇 휴식 중</b> ({regime}) | {kst_str}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"BTC ${cur_price:,.0f} | SMA(200) ${sma_d:,.0f}\n"
                f"BEAR regime 진입 시 자동 가동\n"
                f"잔고: ${state['capital']:.2f}"
            )
        return

    # ── BEAR 모드 — 봇 가동 ──
    k15m = get_klines("15m", 300)
    k1h  = get_klines("1h",  300)
    k4h  = get_klines("4h",  300)
    price = get_price()

    total = state["total_trades"]
    wr_pct = state["wins"] / total * 100 if total else 0

    # 포지션 모니터링
    pos = state.get("position")
    if pos:
        sl, tp = pos["sl"], pos["tp"]
        entry, qty = pos["entry"], pos["qty"]
        side = pos["signal"]

        hit_sl = (side == "SHORT" and price >= sl)
        hit_tp = (side == "SHORT" and price <= tp)
        held_runs = run_count - pos.get("entry_run", run_count)
        time_out  = held_runs >= TIME_STOP_BARS

        if hit_sl or hit_tp or time_out:
            outcome = "TP" if hit_tp else ("TIME" if time_out else "SL")
            exit_p = tp if hit_tp else (price if time_out else sl)
            pnl = qty * (entry - exit_p)
            fee = (qty * entry + qty * exit_p) * 0.0004
            net_pnl = pnl - fee

            state["capital"] += net_pnl
            state["daily_pnl"] += net_pnl
            state["daily_trades"] += 1
            state["total_trades"] += 1
            state["position"] = None
            state["last_signal_candle"] = None
            if outcome == "TP": state["wins"] += 1
            elif outcome == "SL": state["losses"] += 1

            cap_chg = (state["capital"] - SEED_USDT) / SEED_USDT * 100
            new_total = state["total_trades"]
            new_wr = state["wins"] / new_total * 100
            icon = {"TP": "✅", "SL": "❌", "TIME": "⏱"}[outcome]
            label = {"TP": "익절", "SL": "손절", "TIME": "시간 청산"}[outcome]
            hold_str = f"{held_runs * 15}분"

            tg(
                f"{icon} <b>[{label}]</b> SHORT | 📋 페이퍼\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"진입: ${entry:,.2f} → 청산: ${exit_p:,.2f}\n"
                f"수익: <b>{'+' if net_pnl>=0 else ''}${net_pnl:.2f}</b>\n"
                f"보유: {hold_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"잔고: <b>${state['capital']:.2f}</b> ({'+' if cap_chg>=0 else ''}{cap_chg:.2f}%)\n"
                f"오늘: {'+' if state['daily_pnl']>=0 else ''}${state['daily_pnl']:.2f} | "
                f"누적 {new_total}건 승률 {new_wr:.1f}%"
            )

            # Kill switch
            if state["daily_pnl"] / state["daily_start"] <= -DAILY_LOSS_LIMIT:
                state["kill_switch"] = True
                tg(
                    f"🚨 <b>Kill Switch 발동</b>\n"
                    f"일일 손실 한도 {DAILY_LOSS_LIMIT*100:.0f}% 도달\n"
                    f"잔고: ${state['capital']:.2f} | 자정 후 자동 재개"
                )

    # 신규 시그널
    if not state["position"]:
        signal, reason, entry_price, atr_val = generate_short_signal(k15m, k1h, k4h)
        candle_str = str(k15m[-1][0])

        if signal == "SHORT" and candle_str != state.get("last_signal_candle"):
            sl_dist = atr_val * ATR_SL_MULT
            tp_dist = atr_val * ATR_TP_MULT
            sl = entry_price + sl_dist
            tp = entry_price - tp_dist
            risk_amt = state["capital"] * RISK_PCT
            qty = risk_amt / sl_dist if sl_dist > 0 else 0
            notional = qty * entry_price
            max_notional = state["capital"] * MAX_POS_PCT * LEVERAGE
            if notional > max_notional:
                scale = max_notional / notional
                qty *= scale
                notional = qty * entry_price
                risk_amt = qty * sl_dist

            state["position"] = {
                "signal": "SHORT", "entry": entry_price,
                "sl": sl, "tp": tp, "qty": qty,
                "entry_time": kst_str, "entry_run": run_count,
                "atr": atr_val,
            }
            state["last_signal_candle"] = candle_str

            tg(
                f"🔴 <b>[SHORT 진입]</b> 📋 페이퍼 | {kst_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"BTC: <b>${entry_price:,.2f}</b>\n"
                f"손절: ${sl:,.2f} (+{sl_dist/entry_price*100:.2f}%)\n"
                f"익절: ${tp:,.2f} (-{tp_dist/entry_price*100:.2f}%)\n"
                f"시그널: {reason}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"포지션: ${notional:.2f} ({qty:.5f} BTC)\n"
                f"위험: ${risk_amt:.2f} ({risk_amt/state['capital']*100:.2f}% 자본)\n"
                f"잔고: ${state['capital']:.2f} | 누적 {total}건 승률 {wr_pct:.1f}%"
            )

        # 정기 현황 (BEAR 모드, 신호 없을 때)
        elif run_count % STATUS_INTERVAL == 0:
            pos_status = "없음"
            if state.get("position"):
                p = state["position"]
                unr = p["qty"] * (p["entry"] - price)
                pos_status = f"SHORT @ ${p['entry']:,.0f} (미실현 {'+' if unr>=0 else ''}${unr:.2f})"
            cap_pct = (state["capital"] - SEED_USDT) / SEED_USDT * 100
            tg(
                f"📊 <b>정기 현황</b> (BEAR 가동) | {kst_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"BTC: <b>${price:,.2f}</b> | SMA(200) ${sma_d:,.0f}\n"
                f"시그널: {signal} ({reason})\n"
                f"포지션: {pos_status}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"잔고: ${state['capital']:.2f} ({'+' if cap_pct>=0 else ''}{cap_pct:.2f}%)\n"
                f"오늘: {'+' if state['daily_pnl']>=0 else ''}${state['daily_pnl']:.2f} ({state['daily_trades']}건) | "
                f"누적 {total}건 승률 {wr_pct:.1f}%"
            )

        # 일일 결산 (자정 KST = UTC 15:00)
        if now.hour == 15 and now.minute < 16:
            cap_pct = (state["capital"] - SEED_USDT) / SEED_USDT * 100
            tg(
                f"🌙 <b>일일 결산</b> ({today_str})\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"오늘 손익: <b>{'+' if state['daily_pnl']>=0 else ''}${state['daily_pnl']:.2f}</b> "
                f"({state['daily_trades']}건)\n"
                f"잔고: <b>${state['capital']:.2f}</b> "
                f"({'+' if cap_pct>=0 else ''}{cap_pct:.2f}% from ${SEED_USDT:.0f})\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"누적 {total}건 | 승률 {wr_pct:.1f}% | W{state['wins']}/L{state['losses']}"
            )

    save_state(state)
    print(f"완료 (#{run_count}) regime={regime} pos={'YES' if state.get('position') else 'NO'}")


if __name__ == "__main__":
    main()
