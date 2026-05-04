"""
"롱 초입" 역사적 구간 3개에서 LONG paradigm 두 가지 비교.

구간:
  1. 2020-04-01 ~ 2020-10-01 (코로나 회복)
  2. 2023-01-01 ~ 2023-07-01 (포스트 FTX)
  3. 2024-09-01 ~ 2024-12-31 (ETF 후반)

전략 비교:
  A. Mean Reversion LONG (RSI 30 dip 매수, v3_strategy LONG_ONLY)
  B. Breakout LONG (50봉 돌파 + 거래량, v3_breakout)
"""

import sys
from itertools import product
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.historical_range import fetch_klines_range
from src.backtest.multi_engine import run_backtest
from src.signal.v3_strategy import generate_signal as mr_signal, plan_entry as mr_plan
from src.signal.v3_breakout import generate_breakout_signal as bo_signal, plan_breakout_entry as bo_plan

IV_15M = 900_000
IV_1H  = 3_600_000
IV_4H  = 14_400_000


PERIODS = [
    ("2020 코로나 회복",  "2020-04-01", "2020-10-01"),
    ("2023 포스트 FTX",   "2023-01-01", "2023-07-01"),
    ("2024 ETF 후반",     "2024-09-01", "2024-12-31"),
]


def load_period(start, end):
    print(f"   다운로드: {start} ~ {end}")
    k15 = fetch_klines_range("BTCUSDT", "15m", start, end, verbose=False)
    k1h = fetch_klines_range("BTCUSDT", "1h",  start, end, verbose=False)
    k4h = fetch_klines_range("BTCUSDT", "4h",  start, end, verbose=False)
    print(f"   → 15m {len(k15)} | 1h {len(k1h)} | 4h {len(k4h)}")
    return k15, k1h, k4h


def grid_mr_long(k15, k1h, k4h):
    """Mean Reversion LONG-only 27 조합."""
    rsi_levels = [25, 30, 35]
    sl_mults = [1.0, 1.5, 2.0]
    rr_ratios = [1.5, 2.0, 3.0]

    results = []
    for rsi_th, sl_m, rr in product(rsi_levels, sl_mults, rr_ratios):
        params = {
            "rsi_oversold":   rsi_th,
            "rsi_overbought": 100 - rsi_th,
            "atr_sl_mult":    sl_m,
            "atr_tp_mult":    sl_m * rr,
            "side_filter":    "LONG_ONLY",
        }
        r = run_backtest(
            k15, k1h, k4h, mr_signal, mr_plan,
            seed=100.0, params=params,
            iv_exec_ms=IV_15M, iv_mid_ms=IV_1H, iv_high_ms=IV_4H,
        )
        if "error" in r and r.get("trade_count", 0) == 0:
            ret, wr, mdd, ex, tc = 0, 0, 0, 0, 0
        else:
            ret, wr, mdd = r["total_return_pct"], r["win_rate"]*100, r["mdd_pct"]
            ex, tc = r["expectancy_pct"], r["trade_count"]
        results.append(dict(label=f"RSI {rsi_th}/{100-rsi_th} SL{sl_m} RR1:{rr}",
                            ret=ret, wr=wr, mdd=mdd, ex=ex, tc=tc))
    return results


def grid_breakout_long(k15, k1h, k4h):
    """Breakout LONG 27 조합."""
    lookbacks = [30, 50, 80]
    vol_mults = [1.0, 1.5, 2.0]
    rr_ratios = [1.5, 2.0, 3.0]

    results = []
    for lb, vm, rr in product(lookbacks, vol_mults, rr_ratios):
        params = {
            "lookback_high": lb,
            "vol_mult":      vm,
            "atr_sl_mult":   1.5,
            "atr_tp_mult":   1.5 * rr,
        }
        r = run_backtest(
            k15, k1h, k4h, bo_signal, bo_plan,
            seed=100.0, params=params,
            iv_exec_ms=IV_15M, iv_mid_ms=IV_1H, iv_high_ms=IV_4H,
        )
        if "error" in r and r.get("trade_count", 0) == 0:
            ret, wr, mdd, ex, tc = 0, 0, 0, 0, 0
        else:
            ret, wr, mdd = r["total_return_pct"], r["win_rate"]*100, r["mdd_pct"]
            ex, tc = r["expectancy_pct"], r["trade_count"]
        results.append(dict(label=f"Lookback {lb} Vol×{vm} RR1:{rr}",
                            ret=ret, wr=wr, mdd=mdd, ex=ex, tc=tc))
    return results


def fmt_top(results, label, n=3):
    sorted_r = sorted(results, key=lambda x: -x["ret"])
    out = [f"     {label}:"]
    for i, r in enumerate(sorted_r[:n], 1):
        out.append(f"      {i}. {r['label']:35s} → {r['ret']:+7.2f}% | WR {r['wr']:5.1f}% | "
                   f"MDD {r['mdd']:+6.2f}% | Tr {r['tc']}")
    return "\n".join(out)


def main():
    print("=" * 70)
    print("'롱 초입' 역사 구간 3개 — LONG paradigm 비교")
    print("=" * 70)

    all_periods_data = {}
    print("\n[1] 데이터 다운로드")
    for label, start, end in PERIODS:
        print(f"\n  {label}")
        all_periods_data[label] = (start, end, *load_period(start, end))

    print("\n\n" + "=" * 70)
    print("[2] 각 구간 grid search (paradigm A: MR LONG, paradigm B: Breakout LONG)")
    print("=" * 70)

    summary = {}
    for label, (start, end, k15, k1h, k4h) in all_periods_data.items():
        first_close = k15[0][4] if k15 else 0
        last_close = k15[-1][4] if k15 else 0
        bh_pct = (last_close/first_close - 1) * 100 if first_close else 0
        print(f"\n━━━ {label} ({start}~{end}) BTC {first_close:.0f}→{last_close:.0f} = {bh_pct:+.1f}% ━━━")

        mr_results = grid_mr_long(k15, k1h, k4h)
        bo_results = grid_breakout_long(k15, k1h, k4h)
        print(fmt_top(mr_results, "Paradigm A — Mean Reversion LONG"))
        print(fmt_top(bo_results, "Paradigm B — Breakout LONG"))

        summary[label] = {
            "bh_pct": bh_pct,
            "mr_best": max(mr_results, key=lambda x: x["ret"]),
            "bo_best": max(bo_results, key=lambda x: x["ret"]),
        }

    print("\n\n" + "=" * 70)
    print("[3] 최종 비교표")
    print("=" * 70)
    print(f"\n  {'구간':<22} {'B&H':>8} {'MR best':>15} {'Breakout best':>20}")
    print("  " + "-" * 67)
    for label, s in summary.items():
        mr = s["mr_best"]
        bo = s["bo_best"]
        print(f"  {label:<22} {s['bh_pct']:>+7.1f}%  "
              f"{mr['ret']:>+7.2f}% (Tr {mr['tc']:>3})  "
              f"{bo['ret']:>+7.2f}% (Tr {bo['tc']:>3})")
    print()


if __name__ == "__main__":
    main()
