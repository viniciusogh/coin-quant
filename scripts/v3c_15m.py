"""
옵션 C — v3를 15m execution으로 재시도.

타임프레임 매핑:
  execution: 15m (직전 5m 자리)
  mid:        1h (직전 15m 자리)
  high:       4h (직전 1h 자리)
"""

import sys
from itertools import product
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.historical import fetch_klines, split_train_validation
from src.backtest.v3_engine import run_v3_backtest

IV_15M = 900_000
IV_1H  = 3_600_000
IV_4H  = 14_400_000


def main():
    print("=" * 60)
    print("v3-C — 15m execution + 1h/4h multi-TF")
    print("=" * 60)

    print("\n[1] 데이터 (캐시)")
    k15m = fetch_klines("BTCUSDT", "15m", 365, verbose=False)
    k1h  = fetch_klines("BTCUSDT", "1h",  365, verbose=False)
    k4h  = fetch_klines("BTCUSDT", "4h",  365, verbose=False)
    print(f"   15m: {len(k15m):,}  | 1h: {len(k1h):,}  | 4h: {len(k4h):,}")

    train_15, _ = split_train_validation(k15m, 8/12)
    train_1h, _ = split_train_validation(k1h,  8/12)
    train_4h, _ = split_train_validation(k4h,  8/12)
    print(f"   Train 15m: {len(train_15):,}  ({len(train_15)*15/60/24:.0f}일)")

    # ── BASELINE (default params, 15m exec) ──
    print("\n[2] Baseline (default params)")
    r = run_v3_backtest(
        train_15, train_1h, train_4h,
        seed=100.0,
        iv_exec_ms=IV_15M, iv_mid_ms=IV_1H, iv_high_ms=IV_4H,
    )
    if "error" in r:
        print(f"   {r}")
    else:
        print(f"   {r['total_return_pct']:+.2f}% | WR {r['win_rate']*100:.1f}% | "
              f"MDD {r['mdd_pct']:.2f}% | Trades {r['trade_count']} ({r['trades_per_day']:.2f}/일)")

    # ── GRID SEARCH ──
    print("\n[3] 27 조합 grid search")
    rsi_levels = [25, 30, 35]
    sl_mults = [1.0, 1.5, 2.0]
    rr_ratios = [1.5, 2.0, 3.0]

    results = []
    for n, (rsi_th, sl_m, rr) in enumerate(product(rsi_levels, sl_mults, rr_ratios), 1):
        params = {
            "rsi_oversold":   rsi_th,
            "rsi_overbought": 100 - rsi_th,
            "atr_sl_mult":    sl_m,
            "atr_tp_mult":    sl_m * rr,
        }
        r = run_v3_backtest(
            train_15, train_1h, train_4h, seed=100.0, params=params,
            iv_exec_ms=IV_15M, iv_mid_ms=IV_1H, iv_high_ms=IV_4H,
        )
        if "error" in r:
            ret, wr, mdd, ex, tc, tpd = 0, 0, 0, 0, 0, 0
        else:
            ret = r["total_return_pct"]
            wr  = r["win_rate"] * 100
            mdd = r["mdd_pct"]
            ex  = r["expectancy_pct"]
            tc  = r["trade_count"]
            tpd = r["trades_per_day"]
        results.append({
            "rsi": rsi_th, "sl": sl_m, "rr": rr,
            "ret": ret, "wr": wr, "mdd": mdd, "ex": ex, "tc": tc, "tpd": tpd,
        })
        print(f"   [{n:2d}/27] RSI {rsi_th}/{100-rsi_th}  SL {sl_m}  R:R 1:{rr}  "
              f"→ {ret:+7.2f}%  WR {wr:5.1f}%  MDD {mdd:6.2f}%  Tr {tc:4d}  Ex {ex:+.3f}%")

    print("\n" + "=" * 100)
    print("  TOP 5 — 총수익률")
    print("=" * 100)
    print(f"  {'rank':>4} {'RSI':>7} {'SL':>5} {'R:R':>6} {'Return':>9} "
          f"{'Win%':>7} {'MDD':>8} {'Trades':>7} {'Trades/d':>9} {'Exp%':>8}")
    print("  " + "-" * 96)
    for i, r in enumerate(sorted(results, key=lambda x: -x["ret"])[:5], 1):
        print(f"  {i:>4} {r['rsi']:>3}/{100-r['rsi']:<3} {r['sl']:>5.1f} 1:{r['rr']:<4.1f}  "
              f"{r['ret']:>+8.2f}% {r['wr']:>6.1f}% {r['mdd']:>+7.2f}% "
              f"{r['tc']:>6} {r['tpd']:>8.2f} {r['ex']:>+7.3f}")

    print("\n  TOP 5 — Expectancy")
    print("  " + "-" * 96)
    for i, r in enumerate(sorted(results, key=lambda x: -x["ex"])[:5], 1):
        print(f"  {i:>4} {r['rsi']:>3}/{100-r['rsi']:<3} {r['sl']:>5.1f} 1:{r['rr']:<4.1f}  "
              f"{r['ret']:>+8.2f}% {r['wr']:>6.1f}% {r['mdd']:>+7.2f}% "
              f"{r['tc']:>6} {r['tpd']:>8.2f} {r['ex']:>+7.3f}")


if __name__ == "__main__":
    main()
