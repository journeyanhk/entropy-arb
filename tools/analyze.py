#!/usr/bin/env python3
"""Analyze recorded minute data and suggest config.yaml thresholds.

Reads the CSV written by the built-in recorder (logs/minutes.csv by default)
and prints:

  * the premium distribution (midline candidates),
  * how often each candidate upper/lower band would have fired,
  * a ready-to-paste `thresholds:` snippet.

分析机器人自动采集的分钟级盘口数据，输出溢价分布、各档阈值的触发频率，
以及可直接粘贴进 config.yaml 的 thresholds 建议值。

Usage:
    python3 tools/analyze.py                    # logs/minutes.csv
    python3 tools/analyze.py --csv path.csv --hours 24 --min-samples 10
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
import time

CANDIDATES = [1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 15.0, 20.0]


def pctl(sorted_vals: list, q: float) -> float:
    """Linear-interpolated percentile of a pre-sorted list, q in [0, 100]."""
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * q / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def load_rows(path: str, hours: float, min_samples: int) -> list:
    cutoff = time.time() - hours * 3600 if hours > 0 else 0.0
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                if float(r["minute_ts"]) < cutoff:
                    continue
                if int(r["samples"]) < min_samples:
                    continue
                rows.append({
                    "ts": float(r["minute_ts"]),
                    "prem": float(r["premium_close_bps"]),
                    "prem_mean": float(r["premium_mean_bps"]),
                    "sell_max": float(r["sell_edge_max_bps"]),
                    "buy_max": float(r["buy_edge_max_bps"]),
                })
            except (KeyError, ValueError):
                continue
    return rows


def _f(x, default=None):
    """Float-parse a CSV cell, returning default for empty/absent values."""
    try:
        return float(x) if x not in (None, "") else default
    except (TypeError, ValueError):
        return default


def analyze_trades(path: str) -> None:
    """Distribution of per-leg latency, realized slippage and signal age from
    trades.csv (columns added by the P1 telemetry). Old files without the new
    columns are handled gracefully (missing cells skipped)."""
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append(r)
    if not rows:
        print(f"\n{path}: no rows / 无成交记录", file=sys.stderr)
        return
    print(f"\n=== {path}: {len(rows)} executions ===\n")

    lat_b = sorted(x for x in (_f(r.get("buy_lat_ms")) for r in rows)
                   if x is not None)
    lat_s = sorted(x for x in (_f(r.get("sell_lat_ms")) for r in rows)
                   if x is not None)
    slip_b = sorted(x for x in (_f(r.get("slip_buy_bps")) for r in rows)
                    if x is not None)
    slip_s = sorted(x for x in (_f(r.get("slip_sell_bps")) for r in rows)
                    if x is not None)
    ages = sorted(x for x in (_f(r.get("signal_age_sec")) for r in rows)
                  if x is not None)
    gap = sorted(x for x in ((_f(r.get("exp_edge_usd"))
                              - _f(r.get("fill_edge_usd"))) for r in rows)
                 if x is not None and abs(x) < 1e6)

    def dist(name: str, vals: list, unit: str = "") -> None:
        if not vals:
            print(f"  {name}: no data / 无数据")
            return
        print(f"  {name}: p50 {pctl(vals, 50):.2f}{unit}   "
              f"p90 {pctl(vals, 90):.2f}{unit}   "
              f"p99 {pctl(vals, 99):.2f}{unit}   "
              f"(n={len(vals)})")

    dist("buy  leg latency (ms)", lat_b)
    dist("sell leg latency (ms)", lat_s)
    dist("buy  leg realized slip (bps)", slip_b)
    dist("sell leg realized slip (bps)", slip_s)
    dist("signal age (sec)", ages)
    if gap:
        mean_gap = sum(gap) / len(gap)
        print(f"  exp−fill edge gap ($): mean {mean_gap:.4f}   "
              f"p75 {pctl(gap, 75):.4f}   (n={len(gap)})")
        print(f"    -> slippage tax per trade; watch it against "
              f"leg_slippage_bps / 每笔滑点税，与 leg_slippage_bps 对照")
    print()
    print("  latency feeds venue choice & VPS region; slip feeds "
          "leg_slippage_bps; age feeds premium_persist_sec. "
          "/ 延迟决定机房选址，滑点决定保护价，信号年龄决定持续性闸门。")


def main() -> None:
    p = argparse.ArgumentParser(description="suggest thresholds from recorded "
                                            "minute data")
    p.add_argument("--csv", default="logs/minutes.csv")
    p.add_argument("--trades", default=None,
                   help="also analyze execution telemetry from trades.csv "
                        "(latency/slippage/signal age)")
    p.add_argument("--hours", type=float, default=0.0,
                   help="only use the last N hours (0 = all data)")
    p.add_argument("--min-samples", type=int, default=10,
                   help="skip minutes with fewer fresh samples than this")
    p.add_argument("--fees-bps", type=float, default=0.0,
                   help="SUM of both venues' taker fees in bps (each crossing "
                        "pays both legs); recorded edges are pre-fee, so this "
                        "is subtracted before counting firings (default 0.0 — "
                        "pass ~1.0 with a tradexyz hedge)")
    args = p.parse_args()

    if args.trades:
        try:
            analyze_trades(args.trades)
        except FileNotFoundError:
            print(f"{args.trades} not found / 未找到成交文件",
                  file=sys.stderr)

    try:
        rows = load_rows(args.csv, args.hours, args.min_samples)
    except FileNotFoundError:
        print(f"{args.csv} not found — run the bot (even --record-only) to "
              f"collect data first / 未找到数据文件，请先运行机器人采集数据",
              file=sys.stderr)
        sys.exit(1)
    if len(rows) < 30:
        print(f"only {len(rows)} usable minute(s) in {args.csv} — collect at "
              f"least a few hours before trusting the numbers / 数据太少，"
              f"建议至少采集数小时", file=sys.stderr)
        if not rows:
            sys.exit(1)

    span_h = (rows[-1]["ts"] - rows[0]["ts"]) / 3600.0 + 1 / 60.0
    prem = sorted(r["prem"] for r in rows)
    mean = sum(prem) / len(prem)
    var = sum((x - mean) ** 2 for x in prem) / len(prem)
    median = pctl(prem, 50)

    print(f"\n=== {args.csv}: {len(rows)} minutes over {span_h:.1f}h ===\n")
    print("premium of Entropy over hedge, minute close (bps) / "
          "Entropy 相对对冲腿的溢价:")
    print(f"  mean {mean:+.2f}   std {math.sqrt(var):.2f}   "
          f"median {median:+.2f}")
    print(f"  p5 {pctl(prem, 5):+.2f}   p25 {pctl(prem, 25):+.2f}   "
          f"p75 {pctl(prem, 75):+.2f}   p95 {pctl(prem, 95):+.2f}")

    midline = round(median, 1) or 0.0   # normalize -0.0
    # room beyond the midline that was actually executable each minute, net
    # of taker fees (config thresholds are net-of-fee: the engine adds fees
    # on top, and recorded edges are pre-fee)
    fees = args.fees_bps
    sell_room = sorted((r["sell_max"] - midline - fees for r in rows),
                       reverse=True)
    buy_room = sorted((r["buy_max"] + midline - fees for r in rows),
                      reverse=True)

    print(f"\nwith midline_bps = {midline:+.1f} (median) and {fees:.1f} bps "
          f"round-trip taker fees, minutes each band would have fired / "
          f"各档净阈值触发的分钟数:")
    print(f"  {'band bps':>9} | {'SELL entropy':>17} | {'BUY entropy':>17}")
    print(f"  {'':>9} | {'minutes':>8} {'per day':>8} | "
          f"{'minutes':>8} {'per day':>8}")
    per_day = 24.0 / span_h if span_h > 0 else 0.0
    for t in CANDIDATES:
        s_hits = sum(1 for x in sell_room if x >= t)
        b_hits = sum(1 for x in buy_room if x >= t)
        print(f"  {t:>9.1f} | {s_hits:>8} {s_hits * per_day:>8.1f} | "
              f"{b_hits:>8} {b_hits * per_day:>8.1f}")

    # default suggestion: the band that fired in ~10% of minutes (p90 of the
    # fee-adjusted executable room), floored at 1 bps — tune from the table
    sug_upper = max(round(pctl(sorted(sell_room), 90) * 2) / 2, 1.0)
    sug_lower = max(round(pctl(sorted(buy_room), 90) * 2) / 2, 1.0)
    print(f"""
suggested starting point (fires ~10% of minutes, already net of the
{fees:.1f} bps fees passed via --fees-bps; a full round trip nets
>= upper+lower bps after fees) /
建议起点（约 10% 的分钟触发；已扣除 --fees-bps 传入的 {fees:.1f} bps 手续费，
一次完整往返扣费后净赚 >= upper+lower bps）:

thresholds:
  midline_bps: {midline}
  upper_bps: {sug_upper}
  lower_bps: {sug_lower}

Re-run with --hours to focus on recent regimes; premiums drift, so refresh
these numbers regularly. / 溢价中枢会漂移，请定期重新分析并更新配置。
""")


if __name__ == "__main__":
    main()
