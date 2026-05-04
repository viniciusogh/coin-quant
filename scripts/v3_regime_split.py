"""
옵션 1 — 1년 데이터를 BULL/BEAR로 자동 분할 후 각자 검증.

방법:
  1. 1h 데이터에서 가장 높은 close (peak) 찾음
  2. peak 이전 = BULL phase (LONG-only 백테스트)
  3. peak 이후 = BEAR phase (SHORT-only 백테스트)
  4. 각자 27 조합 그리드 서치
"""

import sys
from itertools import product
from pathlib import Path
import datetime as dt
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.historical import fetch_klines
from src.backtest.v3_engine import run_v3_backtest

IV_15M = 900_000
IV_1H  = 3_600_000
IV_4H  = 14_400_000


def slice_klines_by_time(klines, start_ms, end_ms):
    return [k for k in klines if start_ms <= k[0] < end_ms]


def fmt_date(ms):
    return dt.datetime.fromtimestamp(ms/1000, dt.timezone.utc).strftime("%Y-%m-%d")


def main():
    print("=" * 60)
    print("옵션 1 — 1년 데이터 BULL/BEAR 분할 검증")
    print("=" * 60)

    print("\n[1] 데이터 (캐시)")
    k15m = fetch_klines("BTCUSDT", "15m", 365, verbose=False)
    k1h  = fetch_klines("BTCUSDT", "1h",  365, verbose=False)
    k4h  = fetch_klines("BTCUSDT", "4h",  365, verbose=False)

    # peak 찾기 (1h close 기준)
    peak_idx = max(range(len(k1h)), key=lambda i: k1h[i][4])
    peak_ms = k1h[peak_idx][0]
    peak_price = k1h[peak_idx][4]

    print(f"\n[2] Peak 위치 탐색")
    print(f"   시작: {fmt_date(k1h[0][0])}  close ${k1h[0][4]:,.0f}")
    print(f"   peak: {fmt_date(peak_ms)}  close ${peak_price:,.0f}  (인덱스 {peak_idx}/{len(k1h)})")
    print(f"   끝:   {fmt_date(k1h[-1][0])}  close ${k1h[-1][4]:,.0f}")

    bull_days = (peak_ms - k1h[0][0]) / 86_400_000
    bear_days = (k1h[-1][0] - peak_ms) / 86_400_000
    print(f"\n   BULL phase: {bull_days:.0f}일 ({k1h[0][4]:.0f} → {peak_price:.0f}, "
          f"{(peak_price/k1h[0][4]-1)*100:+.1f}%)")
    print(f"   BEAR phase: {bear_days:.0f}일 ({peak_price:.0f} → {k1h[-1][4]:.0f}, "
          f"{(k1h[-1][4]/peak_price-1)*100:+.1f}%)")

    # 분할
    bull_15 = slice_klines_by_time(k15m, k15m[0][0], peak_ms)
    bull_1h = slice_klines_by_time(k1h,  k1h[0][0],  peak_ms)
    bull_4h = slice_klines_by_time(k4h,  k4h[0][0],  peak_ms)
    bear_15 = slice_klines_by_time(k15m, peak_ms, k15m[-1][0] + IV_15M)
    bear_1h = slice_klines_by_time(k1h,  peak_ms, k1h[-1][0] + IV_1H)
    bear_4h = slice_klines_by_time(k4h,  peak_ms, k4h[-1][0] + IV_4H)

    print(f"\n   BULL 봉 수: 15m {len(bull_15)} | 1h {len(bull_1h)} | 4h {len(bull_4h)}")
    print(f"   BEAR 봉 수: 15m {len(bear_15)} | 1h {len(bear_1h)} | 4h {len(bear_4h)}")

    # 그리드
    rsi_levels = [25, 30, 35]
    sl_mults = [1.0, 1.5, 2.0]
    rr_ratios = [1.5, 2.0, 3.0]

    def grid(label, e15, m1h, h4h, side_filter):
        print(f"\n[3] {label} — 27 조합")
        results = []
        for n, (rsi_th, sl_m, rr) in enumerate(product(rsi_levels, sl_mults, rr_ratios), 1):
            params = {
                "rsi_oversold":   rsi_th,
                "rsi_overbought": 100 - rsi_th,
                "atr_sl_mult":    sl_m,
                "atr_tp_mult":    sl_m * rr,
                "side_filter":    side_filter,
            }
            r = run_v3_backtest(
                e15, m1h, h4h, seed=100.0, params=params,
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

        print(f"\n   ━━━ {label} TOP 5 (수익률) ━━━")
        for i, r in enumerate(sorted(results, key=lambda x: -x["ret"])[:5], 1):
            print(f"   {i}.  RSI {r['rsi']:>3}/{100-r['rsi']:<3} SL {r['sl']:.1f} R:R 1:{r['rr']:.1f}  "
                  f"→ {r['ret']:+7.2f}% | WR {r['wr']:5.1f}% | MDD {r['mdd']:+.2f}% | Tr {r['tc']}")
        return results

    bull_results = grid("BULL phase (LONG-only)", bull_15, bull_1h, bull_4h, "LONG_ONLY")
    bear_results = grid("BEAR phase (SHORT-only)", bear_15, bear_1h, bear_4h, "SHORT_ONLY")

    # 최종 요약
    print("\n" + "=" * 60)
    print("  최종 비교")
    print("=" * 60)
    best_bull = max(bull_results, key=lambda x: x["ret"])
    best_bear = max(bear_results, key=lambda x: x["ret"])
    print(f"\n  BULL phase ({bull_days:.0f}일, BTC {(peak_price/k1h[0][4]-1)*100:+.1f}%):")
    print(f"    LONG-only best: {best_bull['ret']:+.2f}% | WR {best_bull['wr']:.1f}% | "
          f"RSI {best_bull['rsi']}/{100-best_bull['rsi']} SL {best_bull['sl']} RR 1:{best_bull['rr']}")
    print(f"\n  BEAR phase ({bear_days:.0f}일, BTC {(k1h[-1][4]/peak_price-1)*100:+.1f}%):")
    print(f"    SHORT-only best: {best_bear['ret']:+.2f}% | WR {best_bear['wr']:.1f}% | "
          f"RSI {best_bear['rsi']}/{100-best_bear['rsi']} SL {best_bear['sl']} RR 1:{best_bear['rr']}")


if __name__ == "__main__":
    main()
