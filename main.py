"""
coin-quant: BTC/USDT 자동 매매 봇
전략: EMA21/55 크로스오버 + 볼륨 필터 (백테스트 +6.76% MDD -6.89%)
"""

import os
import time
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log"),
    ],
)
logger = logging.getLogger("main")

SYMBOL  = os.getenv("SYMBOL", "BTCUSDT")
LEVERAGE = int(os.getenv("LEVERAGE", 3))
SEED_USDT = float(os.getenv("SEED_USDT", 100))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
SL_PCT  = 0.02
TP_PCT  = 0.05

assert LEVERAGE <= 5, "레버리지 5x 초과 절대 금지 (AGENTS.md)"

from src.data.fetcher import DataFetcher
from src.signal.ema_strategy import generate_signal
from src.risk.manager import RiskManager
from src.monitor import telegram_bot as tg


CHECK_INTERVAL_MIN = 15  # 체크 간격 (분)


def wait_interval():
    """15분 대기"""
    logger.info(f"{CHECK_INTERVAL_MIN}분 후 다음 체크...")
    time.sleep(CHECK_INTERVAL_MIN * 60)


def run():
    logger.info("=" * 50)
    logger.info(f"coin-quant 시작 | {SYMBOL} | {LEVERAGE}x | ${SEED_USDT}")
    logger.info(f"모드: {'📋 페이퍼 트레이딩' if DRY_RUN else '💰 실거래'}")
    logger.info("=" * 50)

    if not DRY_RUN:
        confirm = input("⚠️  실거래 모드입니다. 'CONFIRM' 입력: ")
        if confirm != "CONFIRM":
            return

    fetcher       = DataFetcher(symbol=SYMBOL)
    risk          = RiskManager(seed=SEED_USDT)
    position      = None   # {signal, entry, sl, tp, qty}
    last_signal_candle = None  # 중복 시그널 방지용 (마지막 1h 캔들 시간)

    tg.notify_start(SYMBOL, LEVERAGE, SEED_USDT, DRY_RUN)

    while True:
        try:
            risk.reset_daily()

            # Kill Switch 체크
            if risk.check_kill_switch():
                tg.notify_kill_switch(risk.daily_pnl, risk.capital)
                logger.warning("Kill Switch — 오늘 거래 중단")
                wait_next_candle()
                continue

            # 데이터 수집
            df_1h = fetcher.get_ohlcv(interval="1h", limit=300)
            df_4h = fetcher.get_ohlcv(interval="4h", limit=300)

            # 포지션 모니터링 (페이퍼)
            if position and DRY_RUN:
                price = fetcher.get_price()
                sig, sl, tp, qty, entry = (
                    position[k] for k in ("signal","sl","tp","qty","entry")
                )
                hit_sl = (sig=="LONG" and price<=sl) or (sig=="SHORT" and price>=sl)
                hit_tp = (sig=="LONG" and price>=tp) or (sig=="SHORT" and price<=tp)

                if hit_sl or hit_tp:
                    outcome = "TP" if hit_tp else "SL"
                    exit_p  = tp if hit_tp else sl
                    pnl     = qty*(exit_p-entry) if sig=="LONG" else qty*(entry-exit_p)
                    net_pnl = pnl - (qty*entry + qty*exit_p)*0.0004
                    risk.record_trade(net_pnl)
                    tg.notify_close(outcome, net_pnl, risk.capital)
                    logger.info(f"청산: {outcome} PnL={net_pnl:+.2f}")
                    position = None

            # 신규 시그널 (같은 1h 캔들에서 중복 진입 방지)
            current_candle = df_1h.index[-1]
            if position is None and current_candle != last_signal_candle:
                result = generate_signal(df_1h, df_4h)
                signal = result["signal"]
                logger.info(f"시그널: {signal} — {result['reason']}")

                if signal in ("LONG", "SHORT"):
                    price = fetcher.get_price()
                    qty   = risk.position_size(price)
                    sl, tp = risk.sl_tp_price(signal, price, SL_PCT, TP_PCT)
                    tg.notify_signal(signal, price, sl, tp, dry_run=DRY_RUN)
                    last_signal_candle = current_candle  # 이 캔들은 처리 완료

                    if DRY_RUN:
                        position = dict(signal=signal, entry=price,
                                        sl=sl, tp=tp, qty=qty)
                    else:
                        pass  # Phase 4: 실제 주문

            # 일일 결산 (자정)
            now = datetime.now(timezone.utc)
            if now.hour == 0 and now.minute < 2:
                tg.notify_daily_summary(
                    str(risk.today), risk.daily_pnl,
                    risk.capital, risk.daily_trades
                )

        except KeyboardInterrupt:
            logger.info("봇 종료 (Ctrl+C)")
            tg.send("🛑 봇이 수동 종료되었습니다.")
            break
        except Exception as e:
            logger.error(f"오류: {e}", exc_info=True)
            tg.notify_error(str(e))

        wait_interval()


if __name__ == "__main__":
    run()
