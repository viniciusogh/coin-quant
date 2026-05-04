"""
v3 baseline 백테스트: 1년치 데이터 → 8:4 분할 → training 8개월에 default 파라미터로 실행.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.historical import fetch_klines, split_train_validation
from src.backtest.v3_engine import run_v3_backtest


def fmt_result(label: str, r: dict) -> str:
    if "error" in r:
        return f"  [{label}] {r.get('error')}\n"
    return (
        f"  ┌─── {label} ───\n"
        f"  │ 기간 (일):       {r.get('span_days', 0):.0f}\n"
        f"  │ 초기→최종:       ${r['seed']:.2f} → ${r['final_capital']:.2f}\n"
        f"  │ 총수익률:        {r['total_return_pct']:+.2f}%\n"
        f"  │ 총거래:          {r['trade_count']}건  (하루 {r['trades_per_day']:.2f}회)\n"
        f"  │ 승률:            {r['win_rate']*100:.1f}%  "
        f"(W:{r['win_count']}, L:{r['loss_count']}, T:{r['time_count']})\n"
        f"  │ 평균 익절:       {r['avg_win_pct']:+.2f}% / 거래\n"
        f"  │ 평균 손절:       {r['avg_loss_pct']:+.2f}% / 거래\n"
        f"  │ Expectancy:      {r['expectancy_pct']:+.3f}% / 거래\n"
        f"  │ MDD:             {r['mdd_pct']:.2f}%\n"
        f"  └────────────\n"
    )


def main():
    print("=" * 60)
    print("v3 baseline 백테스트")
    print("=" * 60)

    # 1년치 데이터 — 캐시 사용
    print("\n[1] 데이터 다운로드 (캐시 사용)")
    k1h  = fetch_klines("BTCUSDT", "1h",  365)
    k15m = fetch_klines("BTCUSDT", "15m", 365)
    k5m  = fetch_klines("BTCUSDT", "5m",  365)

    # 8:4 분할 (시간순)
    print("\n[2] Train(8개월) / Validation(4개월) 분할")
    train_5m,  val_5m  = split_train_validation(k5m,  8/12)
    train_15m, val_15m = split_train_validation(k15m, 8/12)
    train_1h,  val_1h  = split_train_validation(k1h,  8/12)

    print(f"   Train  5m: {len(train_5m):,}봉  | Val 5m: {len(val_5m):,}봉")
    print(f"   Train 15m: {len(train_15m):,}봉  | Val 15m: {len(val_15m):,}봉")
    print(f"   Train 1h:  {len(train_1h):,}봉   | Val 1h:  {len(val_1h):,}봉")

    # baseline = default params
    print("\n[3] Baseline 백테스트 (default params)")
    print("    필터: 1h+15m 추세 + ATR 폭증차단(2.5x)")
    print("    진입: RSI(14) 30/70 임계 복귀")
    print("    청산: ATR×1.5 SL / ATR×2.25 TP (R:R 1:1.5)")
    print("    사이즈: 자본 1% 위험 한도")
    print()

    print("--- TRAINING ---")
    r_train = run_v3_backtest(train_5m, train_15m, train_1h, seed=100.0)
    print(fmt_result("Train (8개월)", r_train))

    print("--- VALIDATION (참고용, 튜닝 사용 X) ---")
    r_val = run_v3_backtest(val_5m, val_15m, val_1h, seed=100.0)
    print(fmt_result("Validation (4개월)", r_val))


if __name__ == "__main__":
    main()
