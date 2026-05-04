"""
Strategy-agnostic 백테스트 엔진.
v3_engine을 일반화 — signal_fn / entry_planner 콜백을 받아 어떤 paradigm도 백테스트 가능.
"""

import bisect
from dataclasses import dataclass


TAKER_FEE = 0.0004
SLIPPAGE  = 0.0002


@dataclass
class TradeRecord:
    entry_time_ms: int
    exit_time_ms: int
    side: str
    entry: float
    exit: float
    qty: float
    pnl: float
    pnl_pct: float
    outcome: str
    capital_after: float


def _last_closed_index(close_times, target_ms):
    return bisect.bisect_right(close_times, target_ms) - 1


def run_backtest(
    klines_exec: list,
    klines_mid: list,
    klines_high: list,
    signal_fn,           # callable(exec_klines, mid_klines, high_klines, params) -> Signal
    entry_planner,       # callable(signal, capital, params) -> EntryPlan
    seed: float = 100.0,
    params: dict | None = None,
    iv_exec_ms: int = 900_000,    # default 15m
    iv_mid_ms: int = 3_600_000,   # default 1h
    iv_high_ms: int = 14_400_000, # default 4h
    min_exec_warmup: int = 200,
    min_mid_warmup: int = 210,
    min_high_warmup: int = 210,
    verbose: bool = False,
) -> dict:
    p = dict(params or {})
    if "time_stop_bars" not in p:
        p["time_stop_bars"] = 86_400_000 // iv_exec_ms

    close_exec = [k[0] + iv_exec_ms for k in klines_exec]
    close_mid  = [k[0] + iv_mid_ms  for k in klines_mid]
    close_high = [k[0] + iv_high_ms for k in klines_high]

    if len(close_mid) < min_mid_warmup or len(close_high) < min_high_warmup:
        return {"error": "데이터 부족"}
    warmup_close = max(close_mid[min_mid_warmup-1], close_high[min_high_warmup-1])
    start_exec = max(min_exec_warmup, bisect.bisect_left(close_exec, warmup_close))

    capital = seed
    in_position = False
    pos = None
    trades: list[TradeRecord] = []
    equity_curve = []

    day_start_capital = capital
    cur_day_start_ms = (klines_exec[start_exec][0] // 86_400_000) * 86_400_000
    kill_until_ms = -1
    daily_loss_kill = p.get("daily_loss_kill", 0.30)

    for i in range(start_exec, len(klines_exec) - 1):
        bar_high = klines_exec[i][2]
        bar_low  = klines_exec[i][3]
        bar_close = klines_exec[i][4]
        bar_open_time = klines_exec[i][0]
        bar_close_time = bar_open_time + iv_exec_ms

        bar_day_start = (bar_open_time // 86_400_000) * 86_400_000
        if bar_day_start != cur_day_start_ms:
            cur_day_start_ms = bar_day_start
            day_start_capital = capital

        # 포지션 청산 체크
        if in_position:
            sl, tp, side, entry, qty, entry_idx = (
                pos["sl"], pos["tp"], pos["side"], pos["entry"], pos["qty"], pos["entry_idx"]
            )
            outcome, exit_price = None, None
            if side == "LONG":
                if bar_low <= sl:
                    outcome, exit_price = "SL", sl
                elif bar_high >= tp:
                    outcome, exit_price = "TP", tp
            else:
                if bar_high >= sl:
                    outcome, exit_price = "SL", sl
                elif bar_low <= tp:
                    outcome, exit_price = "TP", tp

            if outcome is None and (i - entry_idx) >= p["time_stop_bars"]:
                outcome, exit_price = "TIME", bar_close

            if outcome is not None:
                slip = exit_price * SLIPPAGE
                if outcome == "SL":
                    exit_eff = exit_price - slip if side == "LONG" else exit_price + slip
                else:
                    exit_eff = exit_price

                pnl = qty * (exit_eff - entry) if side == "LONG" else qty * (entry - exit_eff)
                fee = (qty * entry + qty * exit_eff) * TAKER_FEE
                net_pnl = pnl - fee
                capital += net_pnl
                pnl_pct = net_pnl / pos["capital_at_entry"] * 100

                trades.append(TradeRecord(
                    entry_time_ms=pos["entry_time"], exit_time_ms=bar_close_time,
                    side=side, entry=entry, exit=exit_eff, qty=qty,
                    pnl=net_pnl, pnl_pct=pnl_pct, outcome=outcome, capital_after=capital,
                ))
                in_position, pos = False, None

                day_pnl = (capital - day_start_capital) / day_start_capital
                if day_pnl <= -daily_loss_kill:
                    kill_until_ms = bar_day_start + 86_400_000

        # 진입 체크
        if not in_position and bar_close_time > kill_until_ms:
            i_mid  = _last_closed_index(close_mid,  bar_close_time)
            i_high = _last_closed_index(close_high, bar_close_time)
            if i_mid < min_mid_warmup-1 or i_high < min_high_warmup-1:
                equity_curve.append(capital)
                continue

            sig = signal_fn(
                klines_exec[max(0, i-199):i+1],
                klines_mid[max(0, i_mid-209):i_mid+1],
                klines_high[max(0, i_high-209):i_high+1],
                p,
            )

            if sig.side != "NONE":
                next_open = klines_exec[i+1][1]
                slip = next_open * SLIPPAGE
                eff_entry = next_open + slip if sig.side == "LONG" else next_open - slip

                # signal entry를 next_open으로 갈음해서 plan
                sig_for_plan = type(sig)(sig.side, sig.reason, eff_entry, sig.atr)
                plan = entry_planner(sig_for_plan, capital, p)

                if plan.qty > 0:
                    in_position = True
                    pos = {
                        "side": plan.side, "entry": plan.entry,
                        "sl": plan.sl, "tp": plan.tp, "qty": plan.qty,
                        "entry_idx": i, "entry_time": bar_close_time,
                        "capital_at_entry": capital,
                    }

        equity_curve.append(capital)

    if not trades:
        return {
            "error": "거래 없음",
            "seed": seed, "final_capital": capital,
            "total_return_pct": 0.0, "trades": [],
            "trade_count": 0, "trades_per_day": 0.0,
            "win_count": 0, "loss_count": 0, "time_count": 0,
            "win_rate": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "mdd_pct": 0.0, "expectancy_pct": 0.0,
        }

    wins   = [t for t in trades if t.outcome == "TP"]
    losses = [t for t in trades if t.outcome == "SL"]
    times  = [t for t in trades if t.outcome == "TIME"]
    avg_win  = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0

    peak, mdd = seed, 0
    for v in equity_curve:
        if v > peak: peak = v
        dd = (v - peak) / peak * 100
        if dd < mdd: mdd = dd

    span_days = max((klines_exec[-1][0] - klines_exec[start_exec][0]) / 86_400_000, 1)
    expectancy = sum(t.pnl_pct for t in trades) / len(trades)

    return {
        "seed": seed, "final_capital": capital,
        "total_return_pct": (capital - seed) / seed * 100,
        "trades": trades, "trade_count": len(trades),
        "trades_per_day": len(trades) / span_days,
        "win_count": len(wins), "loss_count": len(losses), "time_count": len(times),
        "win_rate": len(wins) / len(trades),
        "avg_win_pct": avg_win, "avg_loss_pct": avg_loss,
        "mdd_pct": mdd, "expectancy_pct": expectancy,
        "span_days": span_days,
    }
