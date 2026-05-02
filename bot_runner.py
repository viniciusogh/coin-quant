"""
GitHub Actions용 봇 런너 v2
전략: 15m RSI(7) 단타 | 1h 추세 필터 | SL 1% / TP 3%
알림: 시그널/체결 시 + 2시간마다 현황 + 일일 결산
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
SL_PCT           = 0.01    # 손절 1%
TP_PCT           = 0.03    # 익절 3% (R:R 1:3)
MAX_POS_PCT      = 0.20
DAILY_LOSS_LIMIT = 0.30
STATE_FILE       = os.path.expanduser("~/.coin-quant/position.json")
STATUS_INTERVAL  = 8       # 2시간마다 현황 (15분 × 8)
MIN_RUN_GAP_SEC  = 600     # 큐 폭주 방지: 마지막 실행 후 10분 이내면 skip

os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

# ── 텔레그램 ─────────────────────────────────────────────
def tg(msg: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5
        )
    except Exception as e:
        print(f"Telegram 오류: {e}")

# ── 상태 파일 ─────────────────────────────────────────────
def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {
            "position": None, "capital": SEED_USDT,
            "daily_start": SEED_USDT, "today": "",
            "daily_trades": 0, "daily_pnl": 0.0,
            "kill_switch": False, "total_trades": 0,
            "wins": 0, "losses": 0, "last_signal_candle": None
        }

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ── Binance REST ──────────────────────────────────────────
def binance_get(path: str, params: dict = None, signed: bool = False) -> list | dict:
    base = "https://fapi.binance.com"
    p = params or {}
    if signed:
        p["timestamp"] = int(time.time() * 1000)
        query = "&".join(f"{k}={v}" for k, v in p.items())
        sig = hmac.new(BINANCE_SECRET_KEY.encode(), query.encode(), hashlib.sha256).hexdigest()
        query += f"&signature={sig}"
        url = f"{base}{path}?{query}"
    else:
        url = f"{base}{path}"
        if p:
            url += "?" + "&".join(f"{k}={v}" for k, v in p.items())
    r = requests.get(url, headers={"X-MBX-APIKEY": BINANCE_API_KEY}, timeout=10)
    r.raise_for_status()
    return r.json()

def get_klines(interval: str, limit: int) -> list:
    return binance_get("/fapi/v1/klines", {"symbol": SYMBOL, "interval": interval, "limit": limit})

def get_price() -> float:
    d = binance_get("/fapi/v1/ticker/price", {"symbol": SYMBOL})
    return float(d["price"])

# ── 지표 계산 ─────────────────────────────────────────────────────
def ema(values: list, span: int) -> list:
    k = 2 / (span + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result

def calc_rsi(closes: list, period: int = 7) -> list:
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    k = 1 / period
    avg_g, avg_l = gains[0], losses[0]
    rsi = []
    for g, l in zip(gains[1:], losses[1:]):
        avg_g = avg_g * (1-k) + g * k
        avg_l = avg_l * (1-k) + l * k
        rs = avg_g / avg_l if avg_l else 100
        rsi.append(100 - 100 / (1 + rs))
    return [None] * (period + 1) + rsi

# ── 시그널 생성 (15m RSI + 1h 추세) ─────────────────────────────
def generate_signal(klines_15m: list, klines_1h: list):
    c15m = [float(k[4]) for k in klines_15m]
    t15m = [k[0] for k in klines_15m]
    c1h  = [float(k[4]) for k in klines_1h]

    if len(c15m) < 50 or len(c1h) < 200:
        return "NONE", "데이터 부족", t15m[-1]

    # 1h 추세 판단
    e50  = ema(c1h, 50)
    e200 = ema(c1h, 200)
    gap  = abs(e50[-1] - e200[-1]) / e200[-1]
    if gap < 0.003:
        return "NONE", "횡보 구간", t15m[-1]
    trend_up   = e50[-1] > e200[-1]
    trend_down = e50[-1] < e200[-1]
    trend_label = "상승" if trend_up else "하락"

    # 15m RSI(7)
    rsi = calc_rsi(c15m, 7)
    if rsi[-1] is None or rsi[-2] is None:
        return "NONE", "RSI 계산 중", t15m[-1]

    if trend_up   and rsi[-2] < 25 and rsi[-1] >= 25:
        return "LONG",  f"RSI {rsi[-2]:.1f}→{rsi[-1]:.1f} 과매도 복귀 | 1h {trend_label}추세", t15m[-1]
    if trend_down and rsi[-2] > 75 and rsi[-1] <= 75:
        return "SHORT", f"RSI {rsi[-2]:.1f}→{rsi[-1]:.1f} 과매수 복귀 | 1h {trend_label}추세", t15m[-1]

    return "NONE", f"RSI {rsi[-1]:.1f} | 1h {trend_label}추세", t15m[-1]

# ── 메인 ─────────────────────────────────────────────────
def main():
    now = datetime.now(timezone.utc)
    today_str = str(date.today())
    kst_str   = f"{now.hour+9:02d}:{now.minute:02d} KST"  # UTC+9
    run_count = state_run_count()
    print(f"[{now.strftime('%Y-%m-%d %H:%M')} UTC] #{run_count} 실행")

    state = load_state()

    # 일일 초기화
    if state["today"] != today_str:
        prev_capital = state["capital"]
        prev_trades  = state.get("total_trades", 0)
        prev_wins    = state.get("wins", 0)
        state.update({"today": today_str, "daily_start": prev_capital,
                      "daily_trades": 0, "daily_pnl": 0.0, "kill_switch": False})
        wr = prev_wins / prev_trades * 100 if prev_trades else 0
        tg(
            f"📅 <b>새 날 시작</b> ({today_str})\n"
            f"━━━━━━━━━━━━━━━\n"
            f"어제까지 잔고: <b>${prev_capital:.2f}</b>\n"
            f"총 누적 거래: {prev_trades}건 | 승률 {wr:.1f}%"
        )

    # Kill Switch
    if state["kill_switch"]:
        save_state(state)
        return  # 알림 없이 조용히 스킵

    # 데이터 수집
    klines_15m = get_klines("15m", 300)
    klines_1h  = get_klines("1h",  300)
    price      = get_price()
    total      = state["total_trades"]
    wr         = state["wins"] / total * 100 if total else 0
    pnl_sign   = "+" if state["daily_pnl"] >= 0 else ""
    cap_pct    = (state["capital"] - SEED_USDT) / SEED_USDT * 100

    # 포지션 모니터링
    pos = state["position"]
    if pos:
        sl, tp, sig = pos["sl"], pos["tp"], pos["signal"]
        entry, qty  = pos["entry"], pos["qty"]
        entry_time  = pos.get("entry_time", "")
        unrealized  = qty*(price-entry) if sig=="LONG" else qty*(entry-price)
        unr_pct     = (price/entry - 1) * 100 if sig=="LONG" else (entry/price - 1) * 100
        sl_dist_pct = abs(price - sl) / price * 100
        tp_dist_pct = abs(tp - price) / price * 100

        hit_sl = (sig=="LONG" and price<=sl) or (sig=="SHORT" and price>=sl)
        hit_tp = (sig=="LONG" and price>=tp) or (sig=="SHORT" and price<=tp)

        if hit_sl or hit_tp:
            outcome = "TP" if hit_tp else "SL"
            exit_p  = tp if hit_tp else sl
            pnl     = qty*(exit_p-entry) if sig=="LONG" else qty*(entry-exit_p)
            fee     = (qty*entry + qty*exit_p) * 0.0004
            net_pnl = pnl - fee
            hold_bars = (run_count - pos.get("entry_run", run_count))
            hold_str  = f"{hold_bars * 15}분"

            state["capital"]      += net_pnl
            state["daily_pnl"]    += net_pnl
            state["daily_trades"] += 1
            state["total_trades"] += 1
            state["position"]      = None
            state["last_signal_candle"] = None
            if outcome == "TP": state["wins"]   += 1
            else:               state["losses"] += 1

            new_total = state["total_trades"]
            new_wr    = state["wins"] / new_total * 100
            cap_chg   = (state["capital"] - SEED_USDT) / SEED_USDT * 100

            icon  = "✅" if outcome == "TP" else "❌"
            label = "익절 성공" if outcome == "TP" else "손절"
            tg(
                f"{icon} <b>[{label}]</b> {sig} | 📋 페이퍼\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"진입: ${entry:,.2f}  →  청산: ${exit_p:,.2f}\n"
                f"수익: <b>{'+'if net_pnl>=0 else''}${net_pnl:.2f}</b>  (수수료 포함)\n"
                f"보유 시간: {hold_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"잔고: <b>${state['capital']:.2f}</b>  ({'+' if cap_chg>=0 else ''}{cap_chg:.2f}% from ${SEED_USDT:.0f})\n"
                f"오늘: {pnl_sign}${state['daily_pnl']:.2f}  |  누적 {new_total}건 승률 {new_wr:.1f}%"
            )

            # Kill Switch
            loss_pct = state["daily_pnl"] / state["daily_start"]
            if loss_pct <= -DAILY_LOSS_LIMIT:
                state["kill_switch"] = True
                tg(
                    f"🚨 <b>Kill Switch 발동!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"일일 손실: <b>{loss_pct*100:.1f}%</b> (한도 -{DAILY_LOSS_LIMIT*100:.0f}%)\n"
                    f"모든 거래 중단 → 자정 후 자동 재개\n"
                    f"잔고: ${state['capital']:.2f}"
                )

    # 신규 시그널
    if not state["position"]:
        signal, reason, candle_time = generate_signal(klines_15m, klines_1h)
        candle_str = str(candle_time)

        if signal in ("LONG", "SHORT") and candle_str != state["last_signal_candle"]:
            qty = (state["capital"] * MAX_POS_PCT * LEVERAGE) / price
            sl  = price*(1-SL_PCT) if signal=="LONG" else price*(1+SL_PCT)
            tp  = price*(1+TP_PCT) if signal=="LONG" else price*(1-TP_PCT)
            pos_usdt    = state["capital"] * MAX_POS_PCT
            max_loss    = pos_usdt * LEVERAGE * SL_PCT
            max_profit  = pos_usdt * LEVERAGE * TP_PCT

            state["position"] = {
                "signal": signal, "entry": price,
                "sl": sl, "tp": tp, "qty": qty,
                "entry_time": kst_str, "entry_run": run_count
            }
            state["last_signal_candle"] = candle_str

            icon = "🟢" if signal == "LONG" else "🔴"
            tg(
                f"{icon} <b>[{signal} 진입]</b> 📋 페이퍼 | {kst_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"BTC 진입가: <b>${price:,.2f}</b>\n"
                f"손절가: ${sl:,.2f}  ({'-' if signal=='LONG' else '+'}{SL_PCT*100:.0f}%)\n"
                f"익절가: ${tp:,.2f}  ({'+' if signal=='LONG' else '-'}{TP_PCT*100:.0f}%)\n"
                f"시그널: {reason}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"포지션: ${pos_usdt:.1f} × {LEVERAGE}x\n"
                f"최대손실: -${max_loss:.2f}  |  최대수익: +${max_profit:.2f}\n"
                f"잔고: ${state['capital']:.2f}  |  누적 {total}건 승률 {wr:.1f}%"
            )

        # 2시간마다 현황 알림 (시그널 없을 때)
        elif run_count % STATUS_INTERVAL == 0:
            pos_status = "없음"
            if state["position"]:
                p = state["position"]
                unr = qty*(price-p["entry"]) if p["signal"]=="LONG" else qty*(p["entry"]-price)
                pos_status = f"{p['signal']} @ ${p['entry']:,.0f} (미실현 {'+'if unr>=0 else''}${unr:.2f})"
            tg(
                f"📊 <b>정기 현황</b> | {kst_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"BTC: <b>${price:,.2f}</b>\n"
                f"시그널: {signal} ({reason})\n"
                f"포지션: {pos_status}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"잔고: ${state['capital']:.2f}  ({'+' if cap_pct>=0 else ''}{cap_pct:.2f}%)\n"
                f"오늘: {pnl_sign}${state['daily_pnl']:.2f}  ({state['daily_trades']}건)\n"
                f"누적: {total}건 | 승률 {wr:.1f}%"
            )

        # 일일 결산 (자정 KST = UTC 15:00)
        if now.hour == 15 and now.minute < 16:
            tg(
                f"🌙 <b>일일 결산</b> ({today_str})\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"오늘 손익: <b>{pnl_sign}${state['daily_pnl']:.2f}</b>  ({state['daily_trades']}건)\n"
                f"현재 잔고: <b>${state['capital']:.2f}</b>\n"
                f"시작 잔고: ${SEED_USDT:.2f}  →  누적 {'+' if cap_pct>=0 else ''}{cap_pct:.2f}%\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"전체 {total}건 | 승률 {wr:.1f}% | W{state['wins']}/L{state['losses']}"
            )

    state["run_count"] = run_count
    save_state(state)
    print(f"완료 (#{run_count})")


def state_run_count():
    """실행 횟수 추적 (position.json의 run_count 필드)"""
    try:
        with open(STATE_FILE) as f:
            return json.load(f).get("run_count", 0) + 1
    except:
        return 1


if __name__ == "__main__":
    main()
