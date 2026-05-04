"""
v3 백테스트 엔진 — 멀티 타임프레임 + ATR 동적 사이즈.

각 5분봉 close 시점에:
1. 그 시각까지 닫힌 1h/15m/5m 캔들 슬라이스 만듦
2. v3_strategy.generate_signal() 호출
3. 시그널 있으면 plan_entry()로 SL/TP/qty 계산
4. 다음 5m 봉부터 SL/TP/time-stop 체크
5. 청산 → 자본 업데이트 → equity_curve 기록

수수료: Binance Futures taker 0.04% (왕복 0.08%)
슬리피지: 0.02% (보수적)
"""

import bisect
from dataclasses import dataclass

from src.signal.v3_strategy import (
    DEFAULT_PARAMS, generate_signal, plan_entry,
)


TAKER_FEE   = 0.0004   # 0.04% 한 방향
SLIPPAGE    = 0.0002   # 0.02% (시장가 진입 가정)


@dataclass
class TradeRecord:
    entry_time_ms: int
    exit_time_ms: int
    side: str
    entry: float
    exit: float
    qty: float
    pnl: float
    pnl_pct: float       # 자본 대비 % 변화
    outcome: str         # "TP" | "SL" | "TIME"
    capital_after: float


def _build_close_index(klines: list) -> list[int]:
    """캔들의 close_time_ms 리스트. close_time = open_time + interval - 1.
    여기선 단순화로 다음 캔들 open_time을 close 시각으로 씀."""
    if len(klines) < 2:
        return [k[0] for k in klines]
    iv = klines[1][0] - klines[0][0]
    return [k[0] + iv for k in klines]


def _last_closed_index(close_times: list[int], target_ms: int) -> int:
    """target_ms 시각에 닫혀 있는 가장 최근 캔들 인덱스 (없으면 -1)."""
    # close_times[i] <= target_ms 만족하는 마지막 i
    idx = bisect.bisect_right(close_times, target_ms) - 1
    return idx


def run_v3_backtest(
    klines_5m: list,
    klines_15m: list,
    klines_1h: list,
    seed: float = 100.0,
    params: dict | None = None,
    verbose: bool = False,
    iv_exec_ms: int = 300_000,     # default 5m execution
    iv_mid_ms: int = 900_000,      # default 15m
    iv_high_ms: int = 3_600_000,   # default 1h
) -> dict:
    """
    Multi-TF backtest. 변수명 5m/15m/1h은 historical 이름이지만 실제론 임의 TF 가능.
        klines_5m  = 진입 타이밍 (가장 빠른 봉)
        klines_15m = 중간 TF
        klines_1h  = 큰 흐름 TF

    iv_*_ms 인자로 다른 TF 조합도 가능 (e.g. 15m/1h/4h).
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    iv_5m = iv_exec_ms
    iv_15 = iv_mid_ms
    iv_1h = iv_high_ms

    # time_stop_bars: 24시간 = 86_400_000 / iv_5m
    if "time_stop_bars" not in (params or {}):
        p["time_stop_bars"] = 86_400_000 // iv_5m  # 자동 24h

    close5  = [k[0] + iv_5m for k in klines_5m]
    close15 = [k[0] + iv_15 for k in klines_15m]
    close1h = [k[0] + iv_1h for k in klines_1h]

    # 워밍업: 5m 200봉, 15m 210봉, 1h 210봉 필요
    # 5m 인덱스 i 일 때 그 시점 close_time = close5[i]
    # 그 시각에 5m: i+1봉 (0..i), 15m: 적어도 210, 1h: 210 필요
    # 시작 인덱스: 5m 200 이후 + 15m/1h 동기 가능한 시점
    min_5m  = 200
    # 5m 첫 시점이 15m/1h 워밍업 후인지 보장 — close5[i] >= close15[209] AND >= close1h[209]
    if len(close15) < 210 or len(close1h) < 210:
        return {"error": "데이터 부족"}
    warmup_close = max(close15[209], close1h[209])
    start_5m = max(min_5m, bisect.bisect_left(close5, warmup_close))

    capital = seed
    in_position = False
    pos: dict | None = None  # {side, entry, sl, tp, qty, entry_idx, entry_time}
    trades: list[TradeRecord] = []
    equity_curve: list[float] = []

    # 일일 손실 트래킹
    day_start_capital = capital
    cur_day_start_ms = (klines_5m[start_5m][0] // 86_400_000) * 86_400_000
    kill_until_ms = -1

    for i in range(start_5m, len(klines_5m) - 1):
        bar_open  = klines_5m[i][1]
        bar_high  = klines_5m[i][2]
        bar_low   = klines_5m[i][3]
        bar_close = klines_5m[i][4]
        bar_open_time = klines_5m[i][0]
        bar_close_time = bar_open_time + iv_5m

        # 일일 경계
        bar_day_start = (bar_open_time // 86_400_000) * 86_400_000
        if bar_day_start != cur_day_start_ms:
            cur_day_start_ms = bar_day_start
            day_start_capital = capital

        # ── 포지션 보유 중: 청산 체크 ──
        if in_position:
            sl, tp, side, entry, qty, entry_idx = (
                pos["sl"], pos["tp"], pos["side"], pos["entry"], pos["qty"], pos["entry_idx"]
            )
            outcome = None
            exit_price = None

            if side == "LONG":
                if bar_low <= sl:
                    outcome = "SL"; exit_price = sl
                elif bar_high >= tp:
                    outcome = "TP"; exit_price = tp
            else:
                if bar_high >= sl:
                    outcome = "SL"; exit_price = sl
                elif bar_low <= tp:
                    outcome = "TP"; exit_price = tp

            # time stop
            if outcome is None and (i - entry_idx) >= p["time_stop_bars"]:
                outcome = "TIME"; exit_price = bar_close

            if outcome is not None:
                # 슬리피지 (불리 방향)
                slip = exit_price * SLIPPAGE
                if (side == "LONG" and outcome == "SL") or (side == "SHORT" and outcome == "SL"):
                    # SL hit — 시장가 슬리피지로 약간 더 나쁜 가격
                    exit_eff = exit_price - slip if side == "LONG" else exit_price + slip
                else:
                    exit_eff = exit_price  # TP는 limit 가정 (또는 close)

                if side == "LONG":
                    pnl = qty * (exit_eff - entry)
                else:
                    pnl = qty * (entry - exit_eff)

                fee = (qty * entry + qty * exit_eff) * TAKER_FEE
                net_pnl = pnl - fee
                capital += net_pnl
                pnl_pct = net_pnl / pos["capital_at_entry"] * 100

                trades.append(TradeRecord(
                    entry_time_ms=pos["entry_time"], exit_time_ms=bar_close_time,
                    side=side, entry=entry, exit=exit_eff, qty=qty,
                    pnl=net_pnl, pnl_pct=pnl_pct,
                    outcome=outcome, capital_after=capital,
                ))

                in_position = False
                pos = None

                # kill switch
                day_pnl = (capital - day_start_capital) / day_start_capital
                if day_pnl <= -p["daily_loss_kill"]:
                    kill_until_ms = bar_day_start + 86_400_000
                    if verbose:
                        print(f"[kill switch] {bar_open_time}: 일일손실 {day_pnl*100:.1f}%")

        # ── 진입 후보 ──
        if not in_position and bar_close_time > kill_until_ms:
            # 멀티 TF 슬라이스 (현재 시각 기준 closed candles만)
            i15 = _last_closed_index(close15, bar_close_time)
            i1h = _last_closed_index(close1h, bar_close_time)
            if i15 < 209 or i1h < 209:
                equity_curve.append(capital)
                continue

            # generate_signal 은 닫힌 캔들 array 받음
            # 5m: 0..i (i 포함, close5[i] = bar_close_time)
            sig = generate_signal(
                klines_5m[max(0, i-199):i+1],
                klines_15m[max(0, i15-209):i15+1],
                klines_1h[max(0, i1h-209):i1h+1],
                params=p,
            )

            if sig.side != "NONE":
                # 진입가 = 다음 5m open으로 가정 (closed candle 기준 신호 → 다음봉 시초가)
                next_open = klines_5m[i+1][1]
                # 슬리피지 적용
                slip = next_open * SLIPPAGE
                effective_entry = next_open + slip if sig.side == "LONG" else next_open - slip

                # SL/TP는 ATR 기반 — generate_signal이 entry 기준으로 계산한 거 재계산
                # signal.entry는 bar_close였지만 실제 진입은 next_open. ATR 기반이라
                # SL/TP 거리는 동일, 가격만 next_open 기준으로 평행이동
                from src.signal.v3_strategy import EntryPlan
                # plan_entry는 signal.entry 사용 → next_open으로 갈음
                sig_for_plan = type(sig)(sig.side, sig.reason, effective_entry, sig.atr)
                plan = plan_entry(sig_for_plan, capital, params=p)

                if plan.qty > 0:
                    in_position = True
                    pos = {
                        "side": plan.side, "entry": plan.entry,
                        "sl": plan.sl, "tp": plan.tp, "qty": plan.qty,
                        "entry_idx": i, "entry_time": bar_close_time,
                        "capital_at_entry": capital,
                    }

        equity_curve.append(capital)

    # ── 결과 집계 ──
    if not trades:
        return {
            "error": "거래 없음",
            "seed": seed, "final_capital": capital,
            "total_return_pct": 0.0,
            "trades": [], "trades_per_day": 0.0,
            "win_count": 0, "loss_count": 0, "time_count": 0,
            "win_rate": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "mdd_pct": 0.0, "expectancy_pct": 0.0,
        }

    wins   = [t for t in trades if t.outcome == "TP"]
    losses = [t for t in trades if t.outcome == "SL"]
    times  = [t for t in trades if t.outcome == "TIME"]

    win_pct_list  = [t.pnl_pct for t in wins]
    loss_pct_list = [t.pnl_pct for t in losses]
    time_pct_list = [t.pnl_pct for t in times]

    avg_win  = sum(win_pct_list) / len(win_pct_list) if win_pct_list else 0.0
    avg_loss = sum(loss_pct_list) / len(loss_pct_list) if loss_pct_list else 0.0

    # MDD
    peak = seed
    mdd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (v - peak) / peak * 100
        if dd < mdd:
            mdd = dd

    span_ms = klines_5m[-1][0] - klines_5m[start_5m][0]
    span_days = max(span_ms / 86_400_000, 1)
    trades_per_day = len(trades) / span_days

    expectancy_pct = (
        len(wins) * avg_win + len(losses) * avg_loss + sum(time_pct_list)
    ) / len(trades)

    return {
        "seed": seed,
        "final_capital": capital,
        "total_return_pct": (capital - seed) / seed * 100,
        "trades": trades,
        "trade_count": len(trades),
        "trades_per_day": trades_per_day,
        "win_count": len(wins),
        "loss_count": len(losses),
        "time_count": len(times),
        "win_rate": len(wins) / len(trades),
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "mdd_pct": mdd,
        "expectancy_pct": expectancy_pct,
        "span_days": span_days,
    }
