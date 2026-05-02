"""
EMA 크로스오버 추세 추종 전략
docs/STRATEGY.md v2

시그널:
  LONG  : 4h EMA50 > EMA200 (상승추세) + 1h EMA21 > EMA55 상향돌파
  SHORT : 4h EMA50 < EMA200 (하락추세) + 1h EMA21 < EMA55 하향돌파
  NONE  : 횡보 구간 or 조건 미충족
"""

import logging
import pandas as pd

logger = logging.getLogger(__name__)

EMA_RANGE_THRESHOLD = 0.005  # 4h EMA 간격 0.5% 미만 → 횡보


def add_ema_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_21"]    = df["close"].ewm(span=21,  adjust=False).mean()
    df["ema_55"]    = df["close"].ewm(span=55,  adjust=False).mean()
    df["ema_50"]    = df["close"].ewm(span=50,  adjust=False).mean()
    df["ema_200"]   = df["close"].ewm(span=200, adjust=False).mean()
    df["volume_ma"] = df["volume"].rolling(20).mean()
    # ATR
    hl  = df["high"] - df["low"]
    hc  = (df["high"] - df["close"].shift(1)).abs()
    lc  = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"] = tr.ewm(com=13, min_periods=14).mean()
    return df


def generate_signal(df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> dict:
    df_1h = add_ema_indicators(df_1h)
    df_4h = add_ema_indicators(df_4h)

    if len(df_1h) < 3 or len(df_4h) < 200:
        return {"signal": "NONE", "reason": "데이터 부족"}

    last_4h = df_4h.iloc[-1]
    prev_1h = df_1h.iloc[-2]
    curr_1h = df_1h.iloc[-1]

    # 횡보 필터
    ema_gap = abs(last_4h["ema_50"] - last_4h["ema_200"]) / last_4h["ema_200"]
    if ema_gap < EMA_RANGE_THRESHOLD:
        return {"signal": "NONE", "reason": "횡보 구간 — 진입 금지"}

    trend_up   = last_4h["ema_50"] > last_4h["ema_200"]
    trend_down = last_4h["ema_50"] < last_4h["ema_200"]

    # 골든 크로스: EMA21이 EMA55를 상향돌파
    golden_cross = prev_1h["ema_21"] <= prev_1h["ema_55"] and curr_1h["ema_21"] > curr_1h["ema_55"]
    # 데스 크로스: EMA21이 EMA55를 하향돌파
    death_cross  = prev_1h["ema_21"] >= prev_1h["ema_55"] and curr_1h["ema_21"] < curr_1h["ema_55"]

    # 볼륨 확인 필터: 현재 볼륨이 20봉 평균의 1.5배 이상
    vol_ok = (not pd.isna(curr_1h.get("volume_ma", float("nan")))
              and curr_1h["volume"] >= curr_1h["volume_ma"] * 1.5)

    if trend_up and golden_cross and vol_ok:
        reason = (f"LONG | 4h EMA50({last_4h['ema_50']:.0f}) > EMA200({last_4h['ema_200']:.0f}) "
                  f"| EMA21 상향돌파 | 볼륨 {curr_1h['volume']:.0f} > MA*1.5")
        return {"signal": "LONG", "reason": reason}

    if trend_down and death_cross and vol_ok:
        reason = (f"SHORT | 4h EMA50({last_4h['ema_50']:.0f}) < EMA200({last_4h['ema_200']:.0f}) "
                  f"| EMA21 하향돌파 | 볼륨 {curr_1h['volume']:.0f} > MA*1.5")
        return {"signal": "SHORT", "reason": reason}

    return {"signal": "NONE", "reason": "조건 미충족"}
