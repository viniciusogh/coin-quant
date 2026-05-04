"""
SHORT-only v3 봇 (RSI 30/70 SL 2.0 RR 1:3) — 8개 역사 구간 robustness 검증.

원칙: 파라미터 고정. 구간별 grid search X (= overfitting 방지).

구간 분류:
  BEAR  : 명확한 하락장 — 봇이 active 작동, 양수 기대
  BULL  : 강세장 — 봇이 신호 거의 안 받아야 (1h+4h DOWN 추세 일치 거의 X)
  RANGE : 횡보 — 봇이 가끔 신호 받아도 변동성 작아 BE 근처 기대
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.historical_range import fetch_klines_range
from src.backtest.multi_engine import run_backtest
from src.signal.v3_strategy import generate_signal as mr_signal, plan_entry as mr_plan

IV_15M = 900_000
IV_1H  = 3_600_000
IV_4H  = 14_400_000

# 백테스트로 도출된 SHORT-only best 파라미터
FIXED_PARAMS = {
    "rsi_oversold":   30,
    "rsi_overbought": 70,
    "atr_sl_mult":    2.0,
    "atr_tp_mult":    6.0,   # R:R 1:3
    "side_filter":    "SHORT_ONLY",
}

PERIODS = [
    # (regime, label, start, end)
    ("BEAR",  "2022 H1 대폭락",     "2022-01-01", "2022-06-30"),
    ("BEAR",  "2024 여름 dip",      "2024-05-01", "2024-08-31"),
    ("BEAR",  "2025 우리 검증 (참고)", "2025-10-06", "2026-05-04"),
    ("BULL",  "2020 코로나 회복",   "2020-04-01", "2020-10-01"),
    ("BULL",  "2023 포스트 FTX",   "2023-01-01", "2023-07-01"),
    ("BULL",  "2024 ETF 후반",     "2024-09-01", "2024-12-31"),
    ("RANGE", "2023 H2 횡보",      "2023-07-01", "2023-11-01"),
    ("RANGE", "2024 봄 횡보",      "2024-02-01", "2024-04-30"),
]


def main():
    print("=" * 78)
    print("SHORT-only v3 봇 robustness — Daily SMA(200) regime gate 적용")
    print(f"고정 파라미터: RSI 30/70  SL ATR×2.0  TP ATR×6.0 (R:R 1:3)  SHORT-only")
    print(f"Gate: Daily 가격 < SMA(200) - 2% 일 때만 거래 (BEAR_ONLY)")
    print("=" * 78)

    rows = []
    for regime, label, start, end in PERIODS:
        print(f"\n━━━ [{regime}] {label} ({start} ~ {end}) ━━━")
        try:
            k15 = fetch_klines_range("BTCUSDT", "15m", start, end, verbose=False)
            k1h = fetch_klines_range("BTCUSDT", "1h",  start, end, verbose=False)
            k4h = fetch_klines_range("BTCUSDT", "4h",  start, end, verbose=False)
            # Daily — 250일 워밍업 위해 더 일찍 시작
            import datetime as dt
            warm_start = (dt.datetime.strptime(start, "%Y-%m-%d") - dt.timedelta(days=260)).strftime("%Y-%m-%d")
            k_d = fetch_klines_range("BTCUSDT", "1d", warm_start, end, verbose=False)
        except Exception as e:
            print(f"   다운로드 실패: {e}")
            continue

        if not k15:
            print(f"   데이터 없음")
            continue

        first_close = k15[0][4]
        last_close = k15[-1][4]
        bh_pct = (last_close/first_close - 1) * 100
        print(f"   BTC: ${first_close:,.0f} → ${last_close:,.0f} ({bh_pct:+.1f}%)")
        print(f"   봉 수: 15m {len(k15)}, 1h {len(k1h)}, 4h {len(k4h)}, 1d {len(k_d)}")

        r = run_backtest(
            k15, k1h, k4h, mr_signal, mr_plan,
            seed=100.0, params=FIXED_PARAMS,
            iv_exec_ms=IV_15M, iv_mid_ms=IV_1H, iv_high_ms=IV_4H,
            klines_daily=k_d, regime_filter="BEAR_ONLY",
        )
        if r.get("trade_count", 0) == 0 or "error" in r:
            tc = r.get("trade_count", 0)
            print(f"   [결과] 거래 0건 — 신호 발생 X (1h+4h DOWN 추세 일치 안 됨)")
            rows.append((regime, label, bh_pct, 0, 0, 0, 0, 0))
            continue

        ret = r["total_return_pct"]
        wr  = r["win_rate"] * 100
        mdd = r["mdd_pct"]
        tc  = r["trade_count"]
        tpd = r["trades_per_day"]
        ex  = r["expectancy_pct"]

        # 알파 vs B&H (SHORT 입장에서 B&H 음수가 우리 친구)
        alpha = ret - bh_pct  # 봇이 B&H 대비 얼마나 더?
        print(f"   [결과] {ret:+7.2f}%  WR {wr:5.1f}%  MDD {mdd:+6.2f}%  Trades {tc}  ({tpd:.2f}/일)")
        print(f"          B&H 대비 알파: {alpha:+.2f}%p")
        rows.append((regime, label, bh_pct, ret, wr, mdd, tc, alpha))

    # 최종 요약표
    print("\n\n" + "=" * 90)
    print("  최종 요약 (고정 파라미터)")
    print("=" * 90)
    print(f"  {'Regime':<7} {'구간':<22} {'B&H':>8} {'봇':>8} {'WR':>6} {'MDD':>7} {'Tr':>4} {'알파':>8}")
    print("  " + "-" * 86)
    for regime, label, bh, ret, wr, mdd, tc, alpha in rows:
        if tc == 0:
            print(f"  {regime:<7} {label:<22} {bh:>+7.1f}%  {'-':>7}  {'-':>5}  {'-':>6}  {'0':>3}  {'-':>7}")
        else:
            print(f"  {regime:<7} {label:<22} {bh:>+7.1f}% {ret:>+7.2f}%  {wr:>5.1f}% {mdd:>+6.2f}% {tc:>4d} {alpha:>+7.2f}%p")

    # Regime별 평균
    by_regime = {"BEAR": [], "BULL": [], "RANGE": []}
    for regime, _, _, ret, _, _, tc, _ in rows:
        if tc > 0:
            by_regime[regime].append(ret)
    print("\n  Regime별 평균 수익률:")
    for r, vals in by_regime.items():
        if vals:
            avg = sum(vals) / len(vals)
            print(f"    {r:<6}: {avg:+.2f}% (n={len(vals)})")
        else:
            print(f"    {r:<6}: 거래 없음")


if __name__ == "__main__":
    main()
