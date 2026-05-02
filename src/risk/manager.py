"""
리스크 관리 모듈
- Kill Switch: 일일 손실 -30% 도달 시 거래 중단
- 포지션 사이징: 시드의 20% 이내
"""

import os
import logging
from datetime import date
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", 0.30))
MAX_POSITION_PCT     = float(os.getenv("MAX_POSITION_PCT", 0.20))
LEVERAGE             = int(os.getenv("LEVERAGE", 3))
TAKER_FEE            = 0.0004


class RiskManager:
    def __init__(self, seed: float):
        self.seed          = seed
        self.capital       = seed
        self.daily_start   = seed
        self.today         = date.today()
        self.daily_trades  = 0
        self.kill_switch   = False  # True 시 오늘 거래 중단

    def reset_daily(self):
        """자정에 일일 통계 초기화"""
        today = date.today()
        if today != self.today:
            logger.info(f"일일 초기화: {self.today} → {today}")
            self.today        = today
            self.daily_start  = self.capital
            self.daily_trades = 0
            self.kill_switch  = False

    @property
    def daily_pnl(self) -> float:
        return self.capital - self.daily_start

    @property
    def daily_pnl_pct(self) -> float:
        return self.daily_pnl / self.daily_start if self.daily_start else 0

    def check_kill_switch(self) -> bool:
        """일일 손실 한도 초과 시 Kill Switch 발동"""
        self.reset_daily()
        if self.kill_switch:
            return True
        if self.daily_pnl_pct <= -DAILY_LOSS_LIMIT_PCT:
            self.kill_switch = True
            logger.warning(
                f"Kill Switch 발동! 일일 손실 {self.daily_pnl_pct*100:.1f}% "
                f"(한도: -{DAILY_LOSS_LIMIT_PCT*100:.0f}%)"
            )
            return True
        return False

    def position_size(self, entry_price: float) -> float:
        """
        진입 수량 계산 (BTC)
        = (가용자본 × MAX_POSITION_PCT × LEVERAGE) / 진입가
        """
        usdt_size = self.capital * MAX_POSITION_PCT * LEVERAGE
        qty = usdt_size / entry_price
        logger.info(
            f"포지션 사이징: ${self.capital:.2f} × {MAX_POSITION_PCT*100:.0f}% "
            f"× {LEVERAGE}x / ${entry_price:,.0f} = {qty:.6f} BTC"
        )
        return qty

    def sl_tp_price(self, signal: str, entry: float,
                    sl_pct: float = 0.02, tp_pct: float = 0.05):
        """손절/익절 가격 계산"""
        if signal == "LONG":
            return entry * (1 - sl_pct), entry * (1 + tp_pct)
        else:
            return entry * (1 + sl_pct), entry * (1 - tp_pct)

    def record_trade(self, pnl: float):
        """거래 결과 반영"""
        self.capital      += pnl
        self.daily_trades += 1
        logger.info(
            f"거래 반영: PnL={pnl:+.2f} | 자본={self.capital:.2f} "
            f"| 일일={self.daily_pnl:+.2f} ({self.daily_pnl_pct*100:+.1f}%)"
        )
