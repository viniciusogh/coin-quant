"""
GitHub Actions용 봇 런너
- 15분마다 실행됨
- position.json에서 상태 읽고 → 체크 → 저장
- 텔레그램으로 결과 전송
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
SL_PCT     = 0.02
TP_PCT     = 0.05
MAX_POS_PCT = 0.20
DAILY_LOSS_LIMIT = 0.30
STATE_FILE = "position.json"

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

# ── 지표 계산 ─────────────────────────────────────────────
def ema(values: list, span: int) -> list:
    k = 2 / (span + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result

def volume_ma(volumes: list, period: int = 20) -> list:
    result = [None] * (period - 1)
    for i in range(period - 1, len(volumes)):
        result.append(sum(volumes[i - period + 1:i + 1]) / period)
    return result

# ── 시그널 생성 ───────────────────────────────────────────
def generate_signal(klines_1h: list, klines_4h: list) -> str:
    def parse(klines):
        closes  = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        times   = [k[0] for k in klines]
        return closes, volumes, times

    c1h, v1h, t1h = parse(klines_1h)
    c4h, _, _     = parse(klines_4h)

    if len(c1h) < 60 or len(c4h) < 200:
        return "NONE", "데이터 부족", t1h[-1]

    # 4h 추세
    e50_4h  = ema(c4h, 50)
    e200_4h = ema(c4h, 200)
    last_4h = e50_4h[-1]
    l200_4h = e200_4h[-1]
    gap = abs(last_4h - l200_4h) / l200_4h
    if gap < 0.005:
        return "NONE", "횡보 구간", t1h[-1]

    trend_up   = last_4h > l200_4h
    trend_down = last_4h < l200_4h

    # 1h EMA21/55
    e21 = ema(c1h, 21)
    e55 = ema(c1h, 55)
    vma = volume_ma(v1h, 20)

    prev_cross = e21[-2] <= e55[-2]
    curr_cross = e21[-1] > e55[-1]
    golden = prev_cross and curr_cross

    prev_death = e21[-2] >= e55[-2]
    curr_death = e21[-1] < e55[-1]
    death = prev_death and curr_death

    vol_ok = vma[-1] is not None and v1h[-1] >= vma[-1] * 1.5

    if trend_up and golden and vol_ok:
        return "LONG", f"EMA21 상향돌파 | 볼륨 확인", t1h[-1]
    if trend_down and death and vol_ok:
        return "SHORT", f"EMA21 하향돌파 | 볼륨 확인", t1h[-1]

    return "NONE", "조건 미충족", t1h[-1]

# ── 메인 ─────────────────────────────────────────────────
def main():
    now = datetime.now(timezone.utc)
    today_str = str(date.today())
    print(f"[{now.strftime('%Y-%m-%d %H:%M')} UTC] 실행 시작")

    state = load_state()

    # 일일 초기화
    if state["today"] != today_str:
        state["today"]        = today_str
        state["daily_start"]  = state["capital"]
        state["daily_trades"] = 0
        state["daily_pnl"]    = 0.0
        state["kill_switch"]  = False
        tg(f"📅 <b>일일 초기화</b>\n잔고: ${state['capital']:.2f}")

    # Kill Switch
    if state["kill_switch"]:
        tg(f"🚫 Kill Switch 활성 — 오늘 거래 중단\n잔고: ${state['capital']:.2f}")
        save_state(state)
        return

    # 데이터 수집
    klines_1h = get_klines("1h", 300)
    klines_4h = get_klines("4h", 300)
    price = get_price()

    # 포지션 모니터링
    pos = state["position"]
    if pos:
        sl, tp, sig = pos["sl"], pos["tp"], pos["signal"]
        entry = pos["entry"]
        qty   = pos["qty"]

        hit_sl = (sig == "LONG" and price <= sl) or (sig == "SHORT" and price >= sl)
        hit_tp = (sig == "LONG" and price >= tp) or (sig == "SHORT" and price <= tp)

        if hit_sl or hit_tp:
            outcome = "TP" if hit_tp else "SL"
            exit_p  = tp if hit_tp else sl
            pnl     = qty * (exit_p - entry) if sig == "LONG" else qty * (entry - exit_p)
            fee     = (qty * entry + qty * exit_p) * 0.0004
            net_pnl = pnl - fee

            state["capital"]      += net_pnl
            state["daily_pnl"]    += net_pnl
            state["daily_trades"] += 1
            state["total_trades"] += 1
            state["position"]      = None
            state["last_signal_candle"] = None

            if outcome == "TP":
                state["wins"] += 1
            else:
                state["losses"] += 1

            icon = "✅" if outcome == "TP" else "❌"
            tg(
                f"{icon} <b>[{outcome}]</b> {sig}\n"
                f"진입: ${entry:,.2f} → 청산: ${exit_p:,.2f}\n"
                f"손익: <b>{'+'if net_pnl>=0 else''}${net_pnl:.2f}</b>\n"
                f"잔고: ${state['capital']:.2f} | 일일: {'+'if state['daily_pnl']>=0 else''}${state['daily_pnl']:.2f}"
            )

            # Kill Switch 체크
            daily_loss_pct = state["daily_pnl"] / state["daily_start"]
            if daily_loss_pct <= -DAILY_LOSS_LIMIT:
                state["kill_switch"] = True
                tg(f"🚨 <b>Kill Switch 발동!</b>\n일일 손실 {daily_loss_pct*100:.1f}%\n오늘 거래 중단")

        else:
            # 포지션 유지 중 — 현황 알림
            unrealized = qty * (price - entry) if sig == "LONG" else qty * (entry - price)
            tg(
                f"📊 <b>포지션 유지 중</b> | BTC ${price:,.2f}\n"
                f"{sig} @ ${entry:,.2f}\n"
                f"미실현: {'+'if unrealized>=0 else''}${unrealized:.2f}\n"
                f"손절: ${sl:,.2f} | 익절: ${tp:,.2f}"
            )

    # 신규 시그널
    if not state["position"]:
        signal, reason, candle_time = generate_signal(klines_1h, klines_4h)
        candle_str = str(candle_time)
        total = state["total_trades"]
        wr = state["wins"] / total * 100 if total else 0

        if signal in ("LONG", "SHORT") and candle_str != state["last_signal_candle"]:
            qty = (state["capital"] * MAX_POS_PCT * LEVERAGE) / price
            sl  = price * (1 - SL_PCT) if signal == "LONG" else price * (1 + SL_PCT)
            tp  = price * (1 + TP_PCT) if signal == "LONG" else price * (1 - TP_PCT)

            state["position"] = {
                "signal": signal, "entry": price,
                "sl": sl, "tp": tp, "qty": qty
            }
            state["last_signal_candle"] = candle_str

            icon = "🟢" if signal == "LONG" else "🔴"
            tg(
                f"{icon} <b>[{signal} 진입]</b> 📋 페이퍼\n"
                f"BTC: <b>${price:,.2f}</b>\n"
                f"손절: ${sl:,.2f} | 익절: ${tp:,.2f}\n"
                f"누적 {total}건 | 승률 {wr:.1f}%"
            )
        else:
            # 시그널 없음 — 15분마다 현황 요약
            tg(
                f"⏱ <b>정기 현황</b> [{now.strftime('%H:%M')} UTC]\n"
                f"BTC: ${price:,.2f} | 시그널: {signal}\n"
                f"잔고: ${state['capital']:.2f} | 일일: {'+'if state['daily_pnl']>=0 else''}${state['daily_pnl']:.2f}\n"
                f"누적 {state['total_trades']}건 | 승률 {wr:.1f}%"
            )

    save_state(state)
    print("완료")


if __name__ == "__main__":
    main()
