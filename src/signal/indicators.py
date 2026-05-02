"""
기술적 지표 계산 모듈
"""

import pandas as pd
import numpy as np


def add_rsi(df: pd.DataFrame, period: int = 14, col: str = "close") -> pd.DataFrame:
    """RSI 계산 후 'rsi' 컬럼 추가"""
    delta = df[col].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    df = df.copy()
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def add_ema(df: pd.DataFrame, period: int, col: str = "close") -> pd.DataFrame:
    """EMA 계산 후 'ema_{period}' 컬럼 추가"""
    df = df.copy()
    df[f"ema_{period}"] = df[col].ewm(span=period, adjust=False).mean()
    return df


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """ATR(Average True Range) 계산"""
    df = df.copy()
    high_low   = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close  = (df["low"]  - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.ewm(com=period - 1, min_periods=period).mean()
    return df


def add_volume_ma(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """볼륨 이동평균 계산"""
    df = df.copy()
    df["volume_ma"] = df["volume"].rolling(period).mean()
    return df


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """전략에 필요한 모든 지표 한 번에 추가"""
    df = add_rsi(df, period=14)
    df = add_ema(df, period=50)
    df = add_ema(df, period=200)
    df = add_atr(df, period=14)
    df = add_volume_ma(df, period=20)
    return df
