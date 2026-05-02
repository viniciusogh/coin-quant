"""
RSI + EMA 추세 전략
docs/STRATEGY.md 기준

시그널:
  LONG  : 4h EMA50 > EMA200 + 1h RSI 30 하향돌파 후 상향복귀
  SHORT : 4h EMA50 < EMA200 + 1h RSI 70 상향돌파 후 하향복귀
  NONE  : 횡보 구간 or 조건 미충족
"""

import logging
import pandas as pd
from src.signal.indicators import add_all_indicators

logger = logging.getLogger(__name__)

# 전략 파라미터
RSI_OVERSOLD   = 30
RSI_OVERBOUGHT = 70
RSI_LONG_ENTRY = 35   # 과매도 진입 기준
RSI_SHORT_ENTRY= 65   # 과매수 진입 기준
EMA_RANGE_THRESHOLD = 0.005  # 0.5% 미만 → 횡보 판단


def is_ranging(df_4h: pd.DataFrame) -> bool:
    """EMA50/EMA200 간격 0.5% 미만이면 횡보"""
    last = df_4h.iloc[-1]
    gap = abs(last["ema_50"] - last["ema_200"]) / last["ema_200"]
    return gap < EMA_RANGE_THRESHOLD


def generate_signal(df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> dict:
    """
    시그널 생성
    Returns:
        {"signal": "LONG" | "SHORT" | "NONE", "reason": str}
    """
    # 지표 계산
    df_1h = add_all_indicators(df_1h)
    df_4h = add_all_indicators(df_4h)

    if len(df_1h) < 3 or len(df_4h) < 200:
        return {"signal": "NONE", "reason": "데이터 부족"}

    # 횡보 필터
    if is_ranging(df_4h):
        return {"signal": "NONE", "reason": "횡보 구간 — 진입 금지"}

    last_4h  = df_4h.iloc[-1]
    prev_1h  = df_1h.iloc[-2]
    curr_1h  = df_1h.iloc[-1]

    trend_up   = last_4h["ema_50"] > last_4h["ema_200"]
    trend_down = last_4h["ema_50"] < last_4h["ema_200"]

    # 롱 시그널: 상승 추세 + RSI 과매도 → 복귀
    if (trend_up
            and curr_1h["rsi"] < RSI_LONG_ENTRY
            and prev_1h["rsi"] < RSI_OVERSOLD
            and curr_1h["rsi"] > RSI_OVERSOLD):
        reason = (f"LONG | 4h EMA50({last_4h['ema_50']:.0f}) > EMA200({last_4h['ema_200']:.0f}) "
                  f"| 1h RSI {prev_1h['rsi']:.1f}→{curr_1h['rsi']:.1f} 상향돌파")
        logger.info(reason)
        return {"signal": "LONG", "reason": reason}

    # 숏 시그널: 하락 추세 + RSI 과매수 → 복귀
    if (trend_down
            and curr_1h["rsi"] > RSI_SHORT_ENTRY
            and prev_1h["rsi"] > RSI_OVERBOUGHT
            and curr_1h["rsi"] < RSI_OVERBOUGHT):
        reason = (f"SHORT | 4h EMA50({last_4h['ema_50']:.0f}) < EMA200({last_4h['ema_200']:.0f}) "
                  f"| 1h RSI {prev_1h['rsi']:.1f}→{curr_1h['rsi']:.1f} 하향돌파")
        logger.info(reason)
        return {"signal": "SHORT", "reason": reason}

    return {"signal": "NONE", "reason": "조건 미충족"}
