"""
백테스트 엔진
- 1h 캔들 기준으로 시그널 재현
- 진입 → 손절(-2%) / 익절(+4%) 시뮬레이션
- 결과: 수익률, MDD, 승률, 거래 수
"""

import logging
import pandas as pd
import numpy as np
from src.signal.ema_strategy import add_ema_indicators
from src.signal.breakout_strategy import add_breakout_indicators

logger = logging.getLogger(__name__)

# 리스크 파라미터 (STRATEGY.md)
STOP_LOSS_PCT   = 0.02   # 손절 2%
TAKE_PROFIT_PCT = 0.04   # 익절 4%
LEVERAGE        = 3
POSITION_PCT    = 0.20   # 시드의 20%
TAKER_FEE       = 0.0004 # Binance Futures taker 수수료 0.04%


def run_backtest(
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    seed: float = 1000.0,
    sl_pct: float = 0.02,
    tp_pct: float = 0.05,
    max_consecutive_losses: int = 2,
    cooldown_bars: int = 24,
    strategy: str = "ema",        # "ema" | "ema_vol" | "breakout"
    use_atr_sl: bool = False,     # True = ATR 기반 동적 손절
    atr_sl_mult: float = 1.5,     # ATR 손절 배율
    atr_tp_mult: float = 3.0,     # ATR 익절 배율
) -> dict:
    """
    백테스트 실행

    Args:
        df_1h: 1h OHLCV DataFrame
        df_4h: 4h OHLCV DataFrame
        seed: 초기 자본 (USDT)

    Returns:
        결과 딕셔너리
    """
    if strategy == "breakout":
        df_1h = add_breakout_indicators(df_1h)
        df_4h_ind = df_4h.copy()
        df_4h_ind["ema_50"]  = df_4h_ind["close"].ewm(span=50,  adjust=False).mean()
        df_4h_ind["ema_200"] = df_4h_ind["close"].ewm(span=200, adjust=False).mean()
    else:
        df_1h = add_ema_indicators(df_1h)
        df_4h_ind = add_ema_indicators(df_4h)

    capital = seed
    trades = []
    equity_curve = [capital]
    consecutive_losses = 0
    cooldown_until = -1  # 쿨다운 종료 인덱스

    # 4h 인덱스를 1h에 맞게 resample (forward fill)
    df_4h_reindexed = df_4h_ind.reindex(df_1h.index, method="ffill")

    min_bars = max(200, 3)  # EMA200 워밍업

    for i in range(min_bars, len(df_1h)):
        prev = df_1h.iloc[i - 1]
        curr = df_1h.iloc[i]
        ctx_4h = df_4h_reindexed.iloc[i]

        chk_col = "high_20" if strategy == "breakout" else "ema_21"
        if pd.isna(curr.get(chk_col, float("nan"))) or pd.isna(ctx_4h["ema_50"]):
            equity_curve.append(capital)
            continue

        # 쿨다운 중이면 스킵
        if i <= cooldown_until:
            equity_curve.append(capital)
            continue

        # 횡보 필터
        ema_gap = abs(ctx_4h["ema_50"] - ctx_4h["ema_200"]) / ctx_4h["ema_200"]
        if ema_gap < 0.005:
            equity_curve.append(capital)
            continue

        trend_up   = ctx_4h["ema_50"] > ctx_4h["ema_200"]
        trend_down = ctx_4h["ema_50"] < ctx_4h["ema_200"]

        signal = None
        if strategy == "breakout":
            if pd.isna(curr.get("volume_ma", float("nan"))):
                equity_curve.append(capital)
                continue
            vol_ok = curr["volume"] >= curr["volume_ma"] * 1.3
            if trend_up and curr["close"] > curr["high_20"] and vol_ok:
                signal = "LONG"
            elif trend_down and curr["close"] < curr["low_20"] and vol_ok:
                signal = "SHORT"
        else:
            golden_cross = prev["ema_21"] <= prev["ema_55"] and curr["ema_21"] > curr["ema_55"]
            death_cross  = prev["ema_21"] >= prev["ema_55"] and curr["ema_21"] < curr["ema_55"]
            vol_ok = True
            if strategy == "ema_vol":
                vol_ok = (not pd.isna(curr.get("volume_ma", float("nan")))
                          and curr["volume"] >= curr["volume_ma"] * 1.5)
            if trend_up and golden_cross and vol_ok:
                signal = "LONG"
            elif trend_down and death_cross and vol_ok:
                signal = "SHORT"

        if signal is None:
            equity_curve.append(capital)
            continue

        # 진입
        entry_price = curr["close"]
        position_usdt = capital * POSITION_PCT
        qty = (position_usdt * LEVERAGE) / entry_price

        # 손절 / 익절 계산 (ATR 또는 고정 %)
        atr_val = curr.get("atr", float("nan")) if hasattr(curr, "get") else float("nan")
        if use_atr_sl and not pd.isna(atr_val):
            sl_dist = atr_val * atr_sl_mult
            tp_dist = atr_val * atr_tp_mult
        else:
            sl_dist = entry_price * sl_pct
            tp_dist = entry_price * tp_pct

        if signal == "LONG":
            sl_price = entry_price - sl_dist
            tp_price = entry_price + tp_dist
        else:
            sl_price = entry_price + sl_dist
            tp_price = entry_price - tp_dist

        # 이후 캔들에서 손절/익절 체크
        outcome = None
        exit_price = None
        for j in range(i + 1, min(i + 100, len(df_1h))):
            future = df_1h.iloc[j]
            if signal == "LONG":
                if future["low"] <= sl_price:
                    outcome = "SL"
                    exit_price = sl_price
                    break
                if future["high"] >= tp_price:
                    outcome = "TP"
                    exit_price = tp_price
                    break
            else:
                if future["high"] >= sl_price:
                    outcome = "SL"
                    exit_price = sl_price
                    break
                if future["low"] <= tp_price:
                    outcome = "TP"
                    exit_price = tp_price
                    break

        if outcome is None:
            equity_curve.append(capital)
            continue

        # P&L 계산 (수수료 포함)
        if signal == "LONG":
            pnl = qty * (exit_price - entry_price)
        else:
            pnl = qty * (entry_price - exit_price)

        fee = (qty * entry_price + qty * exit_price) * TAKER_FEE
        net_pnl = pnl - fee
        capital += net_pnl

        # 연속 손절 카운트
        if outcome == "SL":
            consecutive_losses += 1
            if consecutive_losses >= max_consecutive_losses:
                cooldown_until = i + cooldown_bars
                consecutive_losses = 0
                logger.debug(f"쿨다운 발동: {cooldown_bars}봉 진입 금지")
        else:
            consecutive_losses = 0

        trades.append({
            "time": df_1h.index[i],
            "signal": signal,
            "entry": entry_price,
            "exit": exit_price,
            "outcome": outcome,
            "pnl": net_pnl,
            "capital": capital,
        })
        equity_curve.append(capital)

    # 결과 통계
    if not trades:
        return {"error": "거래 없음 — 시그널 발생 없음"}

    df_trades = pd.DataFrame(trades)
    wins = df_trades[df_trades["outcome"] == "TP"]
    losses = df_trades[df_trades["outcome"] == "SL"]

    equity = pd.Series(equity_curve)
    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    mdd = drawdown.min()

    result = {
        "초기자본":     f"${seed:,.0f}",
        "최종자본":     f"${capital:,.2f}",
        "총수익률":     f"{(capital - seed) / seed * 100:.2f}%",
        "총거래수":     len(df_trades),
        "승률":         f"{len(wins) / len(df_trades) * 100:.1f}%",
        "익절수":       len(wins),
        "손절수":       len(losses),
        "평균수익(TP)": f"${wins['pnl'].mean():.2f}" if len(wins) else "N/A",
        "평균손실(SL)": f"${losses['pnl'].mean():.2f}" if len(losses) else "N/A",
        "MDD":          f"{mdd * 100:.2f}%",
        "trades":       df_trades,
    }
    return result


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/Users/vinicius/Desktop/coin-quant")
    logging.basicConfig(level=logging.WARNING)  # INFO 로그 줄이기

    from dotenv import load_dotenv
    load_dotenv()
    from src.data.fetcher import DataFetcher

    fetcher = DataFetcher()
    print("BTC 1년치 데이터 수집 중... (약 30초 소요)")
    df_1h = fetcher.get_ohlcv_history(interval="1h", days=365)
    df_4h = fetcher.get_ohlcv_history(interval="4h", days=365)
    print(f"1h: {len(df_1h)}개 | 4h: {len(df_4h)}개 ({df_1h.index[0].date()} ~ {df_1h.index[-1].date()})\n")

    def print_result(label, result):
        if "error" in result:
            print(f"[{label}] {result['error']}")
            return
        t = result.pop("trades")
        print(f"{'='*45}")
        print(f"  {label}")
        print(f"{'='*45}")
        for k, v in result.items():
            print(f"  {k:<16}: {v}")
        print(f"{'='*45}\n")

    configs = [
        ("EMA 크로스 (SL2% TP5% CD24h)",
         dict(strategy="ema",      sl_pct=0.02, tp_pct=0.05, cooldown_bars=24, max_consecutive_losses=2)),
        ("EMA+볼륨 필터 (SL2% TP5% CD24h)",
         dict(strategy="ema_vol",  sl_pct=0.02, tp_pct=0.05, cooldown_bars=24, max_consecutive_losses=2)),
        ("EMA+볼륨+ATR손절 (ATR1.5x TP3x)",
         dict(strategy="ema_vol",  use_atr_sl=True, atr_sl_mult=1.5, atr_tp_mult=3.0, cooldown_bars=24, max_consecutive_losses=2)),
        ("브레이크아웃 (SL2% TP5% CD24h)",
         dict(strategy="breakout", sl_pct=0.02, tp_pct=0.05, cooldown_bars=24, max_consecutive_losses=2)),
        ("브레이크아웃+ATR손절 (ATR1.5x TP3x)",
         dict(strategy="breakout", use_atr_sl=True, atr_sl_mult=1.5, atr_tp_mult=3.0, cooldown_bars=24, max_consecutive_losses=2)),
    ]

    summary = []
    for label, kwargs in configs:
        r = run_backtest(df_1h, df_4h, seed=1000.0, **kwargs)
        if "error" in r:
            summary.append((label, "N/A", "N/A", "N/A", 0))
            continue
        r.pop("trades")
        pnl = float(r["총수익률"].replace("%",""))
        mdd = float(r["MDD"].replace("%",""))
        wr  = float(r["승률"].replace("%",""))
        tot = int(r["총거래수"])
        summary.append((label, pnl, mdd, wr, tot))

    print(f"\n{'='*70}")
    print(f"  {'전략':<38} {'수익률':>7} {'MDD':>7} {'승률':>6} {'거래':>5}")
    print(f"{'='*70}")
    for label, pnl, mdd, wr, tot in summary:
        if pnl == "N/A":
            print(f"  {label:<38} {'N/A':>7}")
        else:
            print(f"  {label:<38} {pnl:>6.2f}% {mdd:>6.2f}% {wr:>5.1f}% {tot:>5}")
    print(f"{'='*70}")

    # 최응 전략 상세 출력
    best = max(summary, key=lambda x: x[1] if x[1] != "N/A" else -999)
    print(f"\n★ 최우수 전략: {best[0]} → {best[1]:.2f}%")
    best_cfg = next(cfg for lbl, cfg in configs if lbl == best[0])
    r_best = run_backtest(df_1h, df_4h, seed=1000.0, **best_cfg)
    print_result(f"최우수: {best[0]}", r_best)
