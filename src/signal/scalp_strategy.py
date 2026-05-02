"""
15분봉 단타 전략
- 1h 추세 필터 (EMA50/200)
- 15m RSI(7) 반전 시그널
- SL: 1%, TP: 2% (R:R 1:2)
"""

import pandas as pd
import logging

logger = logging.getLogger(__name__)

RSI_PERIOD     = 7
RSI_OVERSOLD   = 25   # 더 극단적 과매도 (노이즈 감소)
RSI_OVERBOUGHT = 75   # 더 극단적 과매수
EMA_THRESHOLD  = 0.003


def add_scalp_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_50"]  = df["close"].ewm(span=50,  adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()

    # RSI(7)
    delta = df["close"].diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=RSI_PERIOD - 1, min_periods=RSI_PERIOD).mean()
    avg_loss = loss.ewm(com=RSI_PERIOD - 1, min_periods=RSI_PERIOD).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def generate_signal(df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> dict:
    df_15m = add_scalp_indicators(df_15m)
    df_1h_ind = df_1h.copy()
    df_1h_ind["ema_50"]  = df_1h_ind["close"].ewm(span=50,  adjust=False).mean()
    df_1h_ind["ema_200"] = df_1h_ind["close"].ewm(span=200, adjust=False).mean()

    if len(df_15m) < 30 or len(df_1h_ind) < 200:
        return {"signal": "NONE", "reason": "데이터 부족"}

    last_1h = df_1h_ind.iloc[-1]
    prev    = df_15m.iloc[-2]
    curr    = df_15m.iloc[-1]

    # 횡보 필터
    gap = abs(last_1h["ema_50"] - last_1h["ema_200"]) / last_1h["ema_200"]
    if gap < EMA_THRESHOLD:
        return {"signal": "NONE", "reason": "횡보 구간"}

    trend_up   = last_1h["ema_50"] > last_1h["ema_200"]
    trend_down = last_1h["ema_50"] < last_1h["ema_200"]

    if pd.isna(curr["rsi"]) or pd.isna(prev["rsi"]):
        return {"signal": "NONE", "reason": "지표 계산 중"}

    # 롱: 상승추세 + RSI 과매도 복귀
    if trend_up and prev["rsi"] < RSI_OVERSOLD and curr["rsi"] >= RSI_OVERSOLD:
        return {"signal": "LONG",  "reason": f"RSI {prev['rsi']:.1f}→{curr['rsi']:.1f} 과매도 복귀"}

    # 숏: 하락추세 + RSI 과매수 복귀
    if trend_down and prev["rsi"] > RSI_OVERBOUGHT and curr["rsi"] <= RSI_OVERBOUGHT:
        return {"signal": "SHORT", "reason": f"RSI {prev['rsi']:.1f}→{curr['rsi']:.1f} 과매수 복귀"}

    return {"signal": "NONE", "reason": "조건 미충족"}
