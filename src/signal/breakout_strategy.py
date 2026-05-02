"""
브레이크아웃 추세 추종 전략

시그널:
  LONG  : 4h 상승추세 + 1h 종가가 20봉 최고가 돌파 + 볼륨 확인
  SHORT : 4h 하락추세 + 1h 종가가 20봉 최저가 하향돌파 + 볼륨 확인

장점: EMA 크로스오버 대비 후행성 적음 — 실제 가격 움직임 기반
"""

import pandas as pd
import logging

logger = logging.getLogger(__name__)

LOOKBACK       = 20    # 고점/저점 계산 기간 (봉)
VOLUME_MULT    = 1.3   # 볼륨 배율 (평균의 1.3배 이상)
EMA_THRESHOLD  = 0.005 # 횡보 판단 기준


def add_breakout_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # 추세 판단용 EMA
    df["ema_50"]  = df["close"].ewm(span=50,  adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
    # 브레이크아웃 기준: 직전 20봉 고점/저점 (현재 봉 제외)
    df["high_20"] = df["high"].shift(1).rolling(LOOKBACK).max()
    df["low_20"]  = df["low"].shift(1).rolling(LOOKBACK).min()
    # 볼륨 MA
    df["volume_ma"] = df["volume"].rolling(20).mean()
    # ATR
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"]  - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"] = tr.ewm(com=13, min_periods=14).mean()
    return df


def generate_signal(df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> dict:
    df_1h = add_breakout_indicators(df_1h)
    df_4h_ind = df_4h.copy()
    df_4h_ind["ema_50"]  = df_4h_ind["close"].ewm(span=50,  adjust=False).mean()
    df_4h_ind["ema_200"] = df_4h_ind["close"].ewm(span=200, adjust=False).mean()

    if len(df_1h) < LOOKBACK + 5 or len(df_4h_ind) < 200:
        return {"signal": "NONE", "reason": "데이터 부족"}

    last_4h = df_4h_ind.iloc[-1]
    curr    = df_1h.iloc[-1]

    # 횡보 필터
    ema_gap = abs(last_4h["ema_50"] - last_4h["ema_200"]) / last_4h["ema_200"]
    if ema_gap < EMA_THRESHOLD:
        return {"signal": "NONE", "reason": "횡보 구간"}

    trend_up   = last_4h["ema_50"] > last_4h["ema_200"]
    trend_down = last_4h["ema_50"] < last_4h["ema_200"]

    if pd.isna(curr["high_20"]) or pd.isna(curr["volume_ma"]):
        return {"signal": "NONE", "reason": "지표 계산 중"}

    vol_ok = curr["volume"] >= curr["volume_ma"] * VOLUME_MULT

    # 상향 브레이크아웃
    if trend_up and curr["close"] > curr["high_20"] and vol_ok:
        reason = (f"LONG | 종가 {curr['close']:.0f} > 20봉고점 {curr['high_20']:.0f} "
                  f"| 볼륨 확인")
        return {"signal": "LONG", "reason": reason}

    # 하향 브레이크아웃
    if trend_down and curr["close"] < curr["low_20"] and vol_ok:
        reason = (f"SHORT | 종가 {curr['close']:.0f} < 20봉저점 {curr['low_20']:.0f} "
                  f"| 볼륨 확인")
        return {"signal": "SHORT", "reason": reason}

    return {"signal": "NONE", "reason": "조건 미충족"}
