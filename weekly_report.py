"""
주간 봇 점검 — Oracle 인스턴스에서 매주 자동 실행.

cron: 0 0 * * 0   (= 매주 일요일 00:00 UTC = 09:00 KST)

체크:
  1. ~/.coin-quant/position.json 의 상태
  2. ~/.coin-quant/bot.log 최근 7일 라인 + 에러 grep
  3. crontab -l 살아있는지
  4. 거래 빈도 vs 백테스트 기대치 비교
  5. 텔레그램으로 요약 발송
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests

STATE_FILE = Path.home() / ".coin-quant" / "position.json"
LOG_FILE   = Path.home() / ".coin-quant" / "bot.log"
ENV_FILE   = Path.home() / ".coin-quant" / "env"

# Telegram 토큰 — env 파일에서 읽기
def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env

env = load_env(ENV_FILE)
BOT_TOKEN = env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID   = env.get("TELEGRAM_CHAT_ID")   or os.environ.get("TELEGRAM_CHAT_ID")
SEED_USDT = float(env.get("SEED_USDT", os.environ.get("SEED_USDT", "100")))

NOW = datetime.now(timezone.utc)
WEEK_AGO = NOW - timedelta(days=7)


def tg(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram 토큰 없음")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"Telegram 오류: {e}")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def parse_log_week():
    """최근 7일 봇 로그에서 라인 수 + 에러 + 거래 카운트."""
    if not LOG_FILE.exists():
        return {"lines": 0, "errors": 0, "trades": 0, "exists": False}

    total_lines = 0
    error_lines = 0
    trade_lines = 0

    # 로그 라인 형식: "[2026-05-04 03:01 UTC] #16 실행" + "완료 (#16) regime=BEAR pos=NO"
    # 진입/청산은 stdout에 별로 안 찍히고 텔레그램으로 감 — 봇 로그에서 직접 trade 추출은 어려움
    # 대신 state 변화를 봐야 함. 여기선 단순 카운트만.
    log_re = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}) UTC\]")
    err_re = re.compile(r"(error|exception|traceback|fatal)", re.IGNORECASE)

    with LOG_FILE.open("r", errors="ignore") as f:
        for line in f:
            m = log_re.match(line)
            if m:
                try:
                    ts = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M")
                    ts = ts.replace(tzinfo=timezone.utc)
                    if ts >= WEEK_AGO:
                        total_lines += 1
                except ValueError:
                    pass
            if err_re.search(line):
                error_lines += 1

    return {"lines": total_lines, "errors": error_lines, "exists": True}


def crontab_alive() -> bool:
    """crontab 에 run_bot.sh 라인 존재?"""
    try:
        out = subprocess.check_output(["crontab", "-l"], text=True)
        return "run_bot.sh" in out
    except subprocess.CalledProcessError:
        return False


def main():
    state = load_state()
    log_info = parse_log_week()
    cron_ok = crontab_alive()

    capital = state.get("capital", 0)
    total_trades = state.get("total_trades", 0)
    wins = state.get("wins", 0)
    losses = state.get("losses", 0)
    regime = state.get("current_regime", "UNKNOWN")
    cap_chg = (capital - SEED_USDT) / SEED_USDT * 100 if SEED_USDT else 0
    wr = wins / total_trades * 100 if total_trades else 0

    # 1주일 거래 수 추정 — state는 누적만 저장. 별도 weekly 추적이 없으면 이전 점검 보고서와 비교 필요.
    # 단순 처리: 지난 점검 시 total_trades 를 별도 파일에 저장 → 이번 차이 = 이번 주 거래
    weekly_state_file = Path.home() / ".coin-quant" / "weekly_snapshot.json"
    weekly_trades = 0
    weekly_pnl = 0.0
    weekly_capital_start = capital
    if weekly_state_file.exists():
        try:
            prev = json.loads(weekly_state_file.read_text())
            weekly_trades = total_trades - prev.get("total_trades", total_trades)
            weekly_pnl = capital - prev.get("capital", capital)
            weekly_capital_start = prev.get("capital", capital)
        except Exception:
            pass

    # 다음 회차용 스냅샷 저장
    weekly_state_file.write_text(json.dumps({
        "ts": NOW.isoformat(),
        "capital": capital,
        "total_trades": total_trades,
    }, indent=2))

    # 거래 빈도 비교
    actual_tpd = weekly_trades / 7.0
    expected_tpd = 0.25

    # 메시지
    today_str = NOW.strftime("%Y-%m-%d (%a)")
    cron_icon = "✅ 살아있음" if cron_ok else "❌ <b>죽음 — 즉시 확인 필요!</b>"
    err_str = "없음" if log_info["errors"] == 0 else f"<b>{log_info['errors']}건</b> ⚠"
    if not log_info["exists"]:
        log_block = "로그 파일 없음 (봇 첫 실행 대기 중일 수 있음)"
    else:
        log_block = f"실행 라인: {log_info['lines']}건 | 에러: {err_str}"

    weekly_pnl_pct = weekly_pnl / weekly_capital_start * 100 if weekly_capital_start else 0

    next_check = (NOW + timedelta(days=7)).strftime("%Y-%m-%d")

    tg(
        f"🤖 <b>봇 1주 점검</b>  {today_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>자본 현황</b>\n"
        f"  잔고: <b>${capital:.2f}</b>\n"
        f"  시드 대비: {'+' if cap_chg>=0 else ''}{cap_chg:.2f}%\n"
        f"  이번 주 손익: {'+' if weekly_pnl>=0 else ''}${weekly_pnl:.2f} "
        f"({'+' if weekly_pnl_pct>=0 else ''}{weekly_pnl_pct:.2f}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>거래</b>\n"
        f"  이번 주: {weekly_trades}건 ({actual_tpd:.2f}/일)\n"
        f"  누적:   {total_trades}건 (W{wins}/L{losses})\n"
        f"  승률:   {wr:.1f}%\n"
        f"  백테스트 기대치: {expected_tpd}/일 → 실제 {actual_tpd:.2f}/일\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>상태</b>\n"
        f"  Regime: <b>{regime}</b>\n"
        f"  cron: {cron_icon}\n"
        f"  로그: {log_block}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"다음 점검: {next_check}"
    )

    print(f"[{NOW}] 점검 완료 — capital ${capital:.2f}, regime {regime}, weekly {weekly_trades}거래")


if __name__ == "__main__":
    main()
