"""
TREND_GAP_PCT 강화로 mild range 거르기 시험.
8 구간 × 4 gap 값 = 32 backtest.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import datetime as dt
from src.data.historical_range import fetch_klines_range
from src.backtest.multi_engine import run_backtest
from src.signal.v3_strategy import generate_signal as mr_signal, plan_entry as mr_plan

IV_15M = 900_000
IV_1H  = 3_600_000
IV_4H  = 14_400_000

PERIODS = [
    ("BEAR",  "2022 H1 대폭락",     "2022-01-01", "2022-06-30"),
    ("BEAR",  "2024 여름 dip",      "2024-05-01", "2024-08-31"),
    ("BEAR",  "2025 검증",          "2025-10-06", "2026-05-04"),
    ("BULL",  "2020 코로나 회복",   "2020-04-01", "2020-10-01"),
    ("BULL",  "2023 포스트 FTX",   "2023-01-01", "2023-07-01"),
    ("BULL",  "2024 ETF 후반",     "2024-09-01", "2024-12-31"),
    ("RANGE", "2023 H2 횡보",      "2023-07-01", "2023-11-01"),
    ("RANGE", "2024 봄 횡보",      "2024-02-01", "2024-04-30"),
]

GAP_VALUES = [0.003, 0.005, 0.008, 0.012]


def main():
    print("=" * 90)
    print("TREND_GAP_PCT 튜닝 — Daily SMA(200) gate 적용 + SHORT-only")
    print("=" * 90)

    all_data = {}
    for regime, label, start, end in PERIODS:
        try:
            warm = (dt.datetime.strptime(start, "%Y-%m-%d") - dt.timedelta(days=260)).strftime("%Y-%m-%d")
            all_data[label] = (
                regime,
                fetch_klines_range("BTCUSDT", "15m", start, end, verbose=False),
                fetch_klines_range("BTCUSDT", "1h",  start, end, verbose=False),
                fetch_klines_range("BTCUSDT", "4h",  start, end, verbose=False),
                fetch_klines_range("BTCUSDT", "1d",  warm,  end, verbose=False),
            )
        except Exception as e:
            print(f"   {label}: 다운로드 실패 {e}")

    # 결과 매트릭스
    matrix = {}  # matrix[label][gap] = {ret, tc}
    for label, (regime, k15, k1h, k4h, k_d) in all_data.items():
        matrix[label] = {}
        first = k15[0][4] if k15 else 0
        last = k15[-1][4] if k15 else 0
        bh = (last/first - 1) * 100 if first else 0
        matrix[label]["__bh"] = bh
        matrix[label]["__regime"] = regime

        for gap in GAP_VALUES:
            params = {
                "rsi_oversold":   30,
                "rsi_overbought": 70,
                "atr_sl_mult":    2.0,
                "atr_tp_mult":    6.0,
                "side_filter":    "SHORT_ONLY",
                "trend_gap_pct":  gap,
            }
            r = run_backtest(
                k15, k1h, k4h, mr_signal, mr_plan,
                seed=100.0, params=params,
                iv_exec_ms=IV_15M, iv_mid_ms=IV_1H, iv_high_ms=IV_4H,
                klines_daily=k_d, regime_filter="BEAR_ONLY",
            )
            tc = r.get("trade_count", 0)
            ret = r.get("total_return_pct", 0) if tc > 0 else 0
            matrix[label][gap] = (ret, tc)

    # 출력
    print(f"\n  {'Regime':<7} {'구간':<22} {'B&H':>7}", end="")
    for g in GAP_VALUES:
        print(f"  GAP {g*100:.1f}%".ljust(13), end="")
    print()
    print("  " + "-" * 86)

    for label, vals in matrix.items():
        regime = vals["__regime"]
        bh = vals["__bh"]
        print(f"  {regime:<7} {label:<22} {bh:>+6.1f}%", end="")
        for g in GAP_VALUES:
            ret, tc = vals[g]
            if tc == 0:
                print(f"   skip(0)   ", end="")
            else:
                print(f"  {ret:>+6.2f}%/{tc:<3}", end="")
        print()

    # Regime 별 평균 (각 gap)
    print("\n  Regime 평균 수익률:")
    print(f"  {'Regime':<8}", end="")
    for g in GAP_VALUES:
        print(f"  GAP {g*100:.1f}%".ljust(13), end="")
    print()
    for target_regime in ["BEAR", "BULL", "RANGE"]:
        print(f"  {target_regime:<8}", end="")
        for g in GAP_VALUES:
            vals_for_g = [matrix[lbl][g][0] for lbl, v in matrix.items()
                          if v["__regime"] == target_regime and matrix[lbl][g][1] > 0]
            if vals_for_g:
                avg = sum(vals_for_g) / len(vals_for_g)
                print(f"  {avg:>+6.2f}% (n={len(vals_for_g)})", end="")
            else:
                print(f"   거래없음    ", end="")
        print()


if __name__ == "__main__":
    main()
