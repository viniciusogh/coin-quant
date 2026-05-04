"""
공개(인증 X) Binance Futures 캔들 fetch + 디스크 캐시.
v3 백테스트 전용 — pandas 의존 없음, list of [open_time_ms, o,h,l,c,v] 반환.
"""

import json
import os
import time
import requests
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)
BASE_URL = "https://fapi.binance.com"


def _interval_to_ms(interval: str) -> int:
    unit = interval[-1]
    n = int(interval[:-1])
    return n * {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]


def fetch_klines(
    symbol: str,
    interval: str,
    days: int,
    end_ts: int | None = None,
    use_cache: bool = True,
    verbose: bool = True,
) -> list:
    """
    Binance Futures 공개 klines, 페이지네이션으로 days 일치 수집.

    Returns: list of [open_time_ms, open, high, low, close, volume] (open만 string→float)
             closed candles만 (가장 마지막이 진행 중일 수 있어 잘라냄)
    """
    if end_ts is None:
        # 현재 시각, ms
        end_ts = int(time.time() * 1000)
    # 인터벌 정확히 자르기 (마지막 closed candle까지)
    iv_ms = _interval_to_ms(interval)
    end_ts = (end_ts // iv_ms) * iv_ms  # 인터벌 경계로 내림

    start_ts = end_ts - days * 86_400_000

    cache_file = CACHE_DIR / f"{symbol}_{interval}_{days}d_{end_ts}.json"
    if use_cache and cache_file.exists():
        if verbose:
            print(f"  [cache] {cache_file.name}")
        return json.loads(cache_file.read_text())

    all_candles: list[list] = []
    cur = start_ts
    pages = 0
    while cur < end_ts:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cur,
            "endTime": end_ts,
            "limit": 1500,
        }
        r = requests.get(f"{BASE_URL}/fapi/v1/klines", params=params, timeout=15)
        r.raise_for_status()
        raw = r.json()
        if not raw:
            break
        # raw 항목: [open_time, o, h, l, c, v, close_time, quote_v, trades, ...]
        for k in raw:
            all_candles.append([
                int(k[0]), float(k[1]), float(k[2]), float(k[3]),
                float(k[4]), float(k[5]),
            ])
        pages += 1
        if verbose and pages % 5 == 0:
            print(f"  ...{len(all_candles)}봉 ({interval})")
        cur = raw[-1][0] + iv_ms
        if len(raw) < 1500:
            break
        time.sleep(0.15)  # rate limit 여유

    # 중복 제거 (같은 open_time)
    seen: set[int] = set()
    deduped: list[list] = []
    for k in all_candles:
        if k[0] in seen:
            continue
        seen.add(k[0])
        deduped.append(k)
    deduped.sort(key=lambda k: k[0])

    if verbose:
        first = deduped[0][0] if deduped else 0
        last = deduped[-1][0] if deduped else 0
        print(f"  → {symbol} {interval}: {len(deduped)}봉 "
              f"({_ts(first)} ~ {_ts(last)})")

    if use_cache:
        cache_file.write_text(json.dumps(deduped))
    return deduped


def _ts(ms: int) -> str:
    if not ms:
        return "?"
    import datetime as dt
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def split_train_validation(klines: list, train_ratio: float = 8/12) -> tuple[list, list]:
    """klines를 시간 순서대로 train_ratio 비율로 분할."""
    n = len(klines)
    cut = int(n * train_ratio)
    return klines[:cut], klines[cut:]


if __name__ == "__main__":
    print("BTC 1년치 5m/15m/1h 다운로드 + 캐시")
    for interval in ("1h", "15m", "5m"):
        kls = fetch_klines("BTCUSDT", interval, days=365)
        print(f"   {interval}: {len(kls)}봉")
