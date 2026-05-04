"""
v3-C 최고 조합의 거래를 LONG / SHORT 별로 분리 분석.
가설: 하락장이라 SHORT만 살아남고 LONG이 다 까먹었을 수 있음.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.historical import fetch_klines, split_train_validation
from src.backtest.v3_engine import run_v3_backtest

IV_15M = 900_000
IV_1H  = 3_600_000
IV_4H  = 14_400_000


def side_stats(trades, side):
    side_trades = [t for t in trades if t.side == side]
    if not side_trades:
        return f"   {side}: 거래 없음"
    wins  = [t for t in side_trades if t.outcome == "TP"]
    losses= [t for t in side_trades if t.outcome == "SL"]
    times = [t for t in side_trades if t.outcome == "TIME"]
    total_pnl = sum(t.pnl for t in side_trades)
    total_pct = sum(t.pnl_pct for t in side_trades)
    avg_w = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
    avg_l = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
    wr = len(wins) / len(side_trades) * 100
    return (
        f"   {side:5}: {len(side_trades):3d}건 | "
        f"WR {wr:5.1f}% (W{len(wins)}/L{len(losses)}/T{len(times)}) | "
        f"sum_pnl ${total_pnl:+7.2f} ({total_pct:+6.2f}%) | "
        f"avg_W {avg_w:+.2f}% avg_L {avg_l:+.2f}%"
    )


def main():
    print("=" * 60)
    print("v3-C 최고 조합 — LONG vs SHORT 분리 분석")
    print("=" * 60)

    k15m = fetch_klines("BTCUSDT", "15m", 365, verbose=False)
    k1h  = fetch_klines("BTCUSDT", "1h",  365, verbose=False)
    k4h  = fetch_klines("BTCUSDT", "4h",  365, verbose=False)
    train_15, val_15 = split_train_validation(k15m, 8/12)
    train_1h, val_1h = split_train_validation(k1h,  8/12)
    train_4h, val_4h = split_train_validation(k4h,  8/12)

    # 최고 조합 (가장 적은 손실 본 것)
    best_params = {
        "rsi_oversold":   25,
        "rsi_overbought": 75,
        "atr_sl_mult":    1.5,
        "atr_tp_mult":    1.5 * 3.0,   # R:R 1:3
    }

    # 두번째로 좋은 것 (Expectancy 기준 다른 양상)
    second_params = {
        "rsi_oversold":   30,
        "rsi_overbought": 70,
        "atr_sl_mult":    2.0,
        "atr_tp_mult":    2.0 * 2.0,   # R:R 1:2
    }

    for label, params in [
        ("Top 1: RSI 25/75 SL1.5 RR3.0", best_params),
        ("Top 2: RSI 30/70 SL2.0 RR2.0", second_params),
    ]:
        print(f"\n=== {label} ===")
        for span_label, exec_, mid, high in [
            ("Train (8개월)", train_15, train_1h, train_4h),
            ("Validation (4개월)", val_15, val_1h, val_4h),
        ]:
            r = run_v3_backtest(
                exec_, mid, high, seed=100.0, params=params,
                iv_exec_ms=IV_15M, iv_mid_ms=IV_1H, iv_high_ms=IV_4H,
            )
            if "error" in r:
                print(f"  [{span_label}] {r['error']}")
                continue
            print(f"\n  [{span_label}] 총 {r['total_return_pct']:+.2f}% | WR {r['win_rate']*100:.1f}% | Trades {r['trade_count']}")
            print(side_stats(r["trades"], "LONG"))
            print(side_stats(r["trades"], "SHORT"))


if __name__ == "__main__":
    main()
