"""
Telegram 알림 모듈
- 시그널 발생, 체결, 손절/익절, 에러, 일일 P&L 알림
"""

import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
API_URL   = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def send(message: str) -> bool:
    """텔레그램 메시지 발송 (실패 시 로그만 남기고 계속 진행)"""
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("Telegram 설정 없음 — 알림 스킵")
        return False
    try:
        resp = requests.post(API_URL, json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=5)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram 전송 실패: {e}")
        return False


def notify_signal(signal: str, price: float, sl: float, tp: float, dry_run: bool = True):
    mode = "📋 페이퍼" if dry_run else "💰 실거래"
    icon = "🟢" if signal == "LONG" else "🔴"
    msg = (
        f"{icon} <b>[{signal} 진입]</b> {mode}\n"
        f"BTC 진입가: <b>${price:,.2f}</b>\n"
        f"손절: ${sl:,.2f} | 익절: ${tp:,.2f}"
    )
    send(msg)


def notify_close(outcome: str, pnl: float, capital: float):
    icon = "✅" if outcome == "TP" else "❌"
    label = "익절" if outcome == "TP" else "손절"
    sign = "+" if pnl >= 0 else ""
    msg = (
        f"{icon} <b>[{label} 체결]</b>\n"
        f"손익: <b>{sign}${pnl:.2f}</b>\n"
        f"잔고: ${capital:,.2f}"
    )
    send(msg)


def notify_kill_switch(daily_pnl: float, capital: float):
    msg = (
        f"🚨 <b>[Kill Switch 발동]</b>\n"
        f"일일 손실: <b>${daily_pnl:.2f}</b>\n"
        f"모든 포지션 청산 — 오늘 거래 중단\n"
        f"잔고: ${capital:,.2f}"
    )
    send(msg)


def notify_daily_summary(date: str, daily_pnl: float, capital: float, trades: int):
    sign = "+" if daily_pnl >= 0 else ""
    icon = "📈" if daily_pnl >= 0 else "📉"
    msg = (
        f"{icon} <b>[일일 결산 {date}]</b>\n"
        f"일일 손익: <b>{sign}${daily_pnl:.2f}</b>\n"
        f"총 거래: {trades}건\n"
        f"잔고: ${capital:,.2f}"
    )
    send(msg)


def notify_error(error: str):
    msg = f"⚠️ <b>[오류 발생]</b>\n<code>{error[:300]}</code>"
    send(msg)


def notify_start(symbol: str, leverage: int, seed: float, dry_run: bool):
    mode = "페이퍼 트레이딩" if dry_run else "⚠️ 실거래"
    msg = (
        f"🚀 <b>[봇 시작]</b>\n"
        f"심볼: {symbol} | 레버리지: {leverage}x\n"
        f"시드: ${seed:,.2f} | 모드: {mode}"
    )
    send(msg)


if __name__ == "__main__":
    print("텔레그램 연결 테스트...")
    ok = send("✅ coin-quant 봇 연결 테스트 성공!")
    print("성공!" if ok else "실패 — .env 확인 필요")
