"""
특정 시간 구간 데이터 다운로드 + 캐시. (historical.py의 days 기반과 별도)
"""

import json
import time
import requests
from pathlib import Path
import datetime as dt

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)
BASE_URL = "https://fapi.binance.com"


def _interval_to_ms(interval: str) -> int:
    unit = interval[-1]
    n = int(interval[:-1])
    return n * {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]


def fetch_klines_range(
    symbol: str,
    interval: str,
    start_date: str,   # "YYYY-MM-DD"
    end_date: str,     # "YYYY-MM-DD"
    use_cache: bool = True,
    verbose: bool = True,
) -> list:
    """주어진 날짜 구간의 klines (인증 X)."""
    start_ts = int(dt.datetime.strptime(start_date, "%Y-%m-%d")
                   .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    end_ts = int(dt.datetime.strptime(end_date, "%Y-%m-%d")
                 .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

    iv_ms = _interval_to_ms(interval)
    end_ts = (end_ts // iv_ms) * iv_ms

    cache_file = CACHE_DIR / f"{symbol}_{interval}_{start_date}_{end_date}.json"
    if use_cache and cache_file.exists():
        if verbose:
            print(f"  [cache] {cache_file.name}")
        return json.loads(cache_file.read_text())

    all_candles: list[list] = []
    cur = start_ts
    pages = 0
    while cur < end_ts:
        params = {
            "symbol": symbol, "interval": interval,
            "startTime": cur, "endTime": end_ts, "limit": 1500,
        }
        r = requests.get(f"{BASE_URL}/fapi/v1/klines", params=params, timeout=15)
        r.raise_for_status()
        raw = r.json()
        if not raw:
            break
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
        time.sleep(0.15)

    seen: set[int] = set()
    deduped: list[list] = []
    for k in all_candles:
        if k[0] in seen:
            continue
        seen.add(k[0])
        deduped.append(k)
    deduped.sort(key=lambda k: k[0])

    if verbose:
        if deduped:
            first_d = dt.datetime.fromtimestamp(deduped[0][0]/1000, dt.timezone.utc).strftime("%Y-%m-%d")
            last_d  = dt.datetime.fromtimestamp(deduped[-1][0]/1000, dt.timezone.utc).strftime("%Y-%m-%d")
            print(f"  → {symbol} {interval}: {len(deduped)}봉 ({first_d} ~ {last_d})")

    if use_cache:
        cache_file.write_text(json.dumps(deduped))
    return deduped
