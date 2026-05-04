"""
v3 grid search — Training 8개월 데이터에서 27조합 비교.

축:
  rsi_threshold: 25 (= 25/75) | 30 (= 30/70) | 35 (= 35/65)
  atr_sl_mult:   1.0 | 1.5 | 2.0
  rr_ratio:      1.5 | 2.0 | 3.0   (atr_tp_mult = atr_sl_mult × rr_ratio)
"""

import sys
from itertools import product
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.historical import fetch_klines, split_train_validation
from src.backtest.v3_engine import run_v3_backtest


def run_grid():
    print("=" * 60)
    print("v3 GRID SEARCH — Training 8개월")
    print("=" * 60)

    print("\n[1] 데이터 로드")
    k1h  = fetch_klines("BTCUSDT", "1h",  365, verbose=False)
    k15m = fetch_klines("BTCUSDT", "15m", 365, verbose=False)
    k5m  = fetch_klines("BTCUSDT", "5m",  365, verbose=False)
    train_5m,  _ = split_train_validation(k5m,  8/12)
    train_15m, _ = split_train_validation(k15m, 8/12)
    train_1h,  _ = split_train_validation(k1h,  8/12)
    print(f"   Train 5m: {len(train_5m):,}봉 ({len(train_5m)*5/60/24:.0f}일)")

    rsi_levels = [25, 30, 35]
    atr_sl_mults = [1.0, 1.5, 2.0]
    rr_ratios = [1.5, 2.0, 3.0]

    combos = list(product(rsi_levels, atr_sl_mults, rr_ratios))
    print(f"\n[2] 27 조합 백테스트 시작 (예상 ~3분)\n")

    results = []
    for n, (rsi_th, sl_m, rr) in enumerate(combos, 1):
        params = {
            "rsi_oversold":   rsi_th,
            "rsi_overbought": 100 - rsi_th,
            "atr_sl_mult":    sl_m,
            "atr_tp_mult":    sl_m * rr,
        }
        r = run_v3_backtest(train_5m, train_15m, train_1h, seed=100.0, params=params)
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
            "ret": ret, "wr": wr, "mdd": mdd,
            "ex": ex, "tc": tc, "tpd": tpd,
        })
        print(f"   [{n:2d}/27] RSI {rsi_th}/{100-rsi_th}  ATR_SL {sl_m}  R:R 1:{rr}  "
              f"→ {ret:+7.2f}%  WR {wr:5.1f}%  MDD {mdd:6.2f}%  Trades {tc:4d}  Ex {ex:+.3f}%")

    print("\n" + "=" * 100)
    print("  TOP 5 — 총수익률 기준")
    print("=" * 100)
    print(f"  {'rank':>4} {'RSI':>7} {'SL':>5} {'R:R':>6} {'Return':>9} "
          f"{'Win%':>7} {'MDD':>8} {'Trades':>7} {'Trades/d':>9} {'Exp%':>8}")
    print("  " + "-" * 96)
    for i, r in enumerate(sorted(results, key=lambda x: -x["ret"])[:5], 1):
        print(f"  {i:>4} {r['rsi']:>3}/{100-r['rsi']:<3} {r['sl']:>5.1f} 1:{r['rr']:<4.1f}  "
              f"{r['ret']:>+8.2f}% {r['wr']:>6.1f}% {r['mdd']:>+7.2f}% "
              f"{r['tc']:>6} {r['tpd']:>8.2f} {r['ex']:>+7.3f}")

    print("\n" + "=" * 100)
    print("  TOP 5 — Expectancy 기준 (단위 R 가치, 거래마다 평균)")
    print("=" * 100)
    print(f"  {'rank':>4} {'RSI':>7} {'SL':>5} {'R:R':>6} {'Return':>9} "
          f"{'Win%':>7} {'MDD':>8} {'Trades':>7} {'Trades/d':>9} {'Exp%':>8}")
    print("  " + "-" * 96)
    for i, r in enumerate(sorted(results, key=lambda x: -x["ex"])[:5], 1):
        print(f"  {i:>4} {r['rsi']:>3}/{100-r['rsi']:<3} {r['sl']:>5.1f} 1:{r['rr']:<4.1f}  "
              f"{r['ret']:>+8.2f}% {r['wr']:>6.1f}% {r['mdd']:>+7.2f}% "
              f"{r['tc']:>6} {r['tpd']:>8.2f} {r['ex']:>+7.3f}")

    print("\n" + "=" * 100)
    print("  TOP 5 — 승률 기준")
    print("=" * 100)
    print(f"  {'rank':>4} {'RSI':>7} {'SL':>5} {'R:R':>6} {'Return':>9} "
          f"{'Win%':>7} {'MDD':>8} {'Trades':>7} {'Trades/d':>9} {'Exp%':>8}")
    print("  " + "-" * 96)
    for i, r in enumerate(sorted(results, key=lambda x: -x["wr"])[:5], 1):
        print(f"  {i:>4} {r['rsi']:>3}/{100-r['rsi']:<3} {r['sl']:>5.1f} 1:{r['rr']:<4.1f}  "
              f"{r['ret']:>+8.2f}% {r['wr']:>6.1f}% {r['mdd']:>+7.2f}% "
              f"{r['tc']:>6} {r['tpd']:>8.2f} {r['ex']:>+7.3f}")

    return results


if __name__ == "__main__":
    run_grid()
