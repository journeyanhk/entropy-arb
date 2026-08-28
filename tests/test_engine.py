"""Engine signal math: midline band directions, inventory ladder, scan.

Run:  python3 -m pytest tests/  (or  python3 tests/test_engine.py)
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from entropy_arb.book import OrderBook  # noqa: E402
from entropy_arb.config import load_config  # noqa: E402
from entropy_arb.engine import Engine  # noqa: E402

NO_ENV = os.path.join(tempfile.gettempdir(), "entropy-arb-no-such.env")


def make_cfg(midline=5.0, upper=4.0, lower=3.0):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    f.write(f"""
thresholds:
  midline_bps: {midline}
  upper_bps: {upper}
  lower_bps: {lower}
execution:
  premium_persist_sec: 0.0
slippage:
  enabled: false     # tests: keep old threshold math clean (no shared state file)
""")
    f.close()
    return load_config(f.name, NO_ENV,
                       symbol="SNDK", hedge_venue="lighter-rh")


class StubVenue:
    def __init__(self, key, label, cap=10000.0, fee=0.0):
        self.key, self.name = key, label
        self.cap_usd, self.fee_bps = cap, fee
        self.size_decimals, self.min_base, self.min_quote = 4, 1e-4, 10.0
        self.position, self.cash = 0.0, 0.0
        self.volume_usd = 0.0
        self.equity = None
        self.orders_per_min = 30
        self.last_traded_ts = 0.0
        self.free = None
        self.book = OrderBook()

    def px_round(self, px, round_up):
        f = 10 ** self.size_decimals
        import math
        v = math.ceil(px * f - 1e-9) / f if round_up else \
            math.floor(px * f + 1e-9) / f
        return round(v, 8)

    def ready_to_trade(self):
        return True

    def set_book(self, bid, ask, sz=50.0):
        self.book.apply_hl([[{"px": str(bid), "sz": str(sz)}],
                            [{"px": str(ask), "sz": str(sz)}]])


def make_engine(**thr):
    cfg = make_cfg(**thr)
    eng = Engine(cfg)
    eng.entropy = StubVenue("entropy", "ENTROPY")
    eng.hedge = StubVenue("hedge", "RH")
    eng.venues = {"entropy": eng.entropy, "hedge": eng.hedge}
    eng._step, eng._min_base, eng._min_notional = 1e-4, 1e-4, 10.0
    return eng


def approx(a, b, tol=1e-9):
    assert abs(a - b) <= tol, f"{a} != {b}"


def test_eff_threshold_directions():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    e, h = eng.entropy, eng.hedge
    # sell entropy: hurdle = midline + upper = 9
    approx(eng._eff_threshold(buy=h, sell=e), 9.0)
    # buy entropy: hurdle = lower - midline = -2 (unwind side of a positive
    # midline is deliberately cheap — that's what completes the round trip)
    approx(eng._eff_threshold(buy=e, sell=h), -2.0)
    # round trip nets upper + lower regardless of midline sign
    for m in (-7.0, 0.0, 12.5):
        eng.cfg.midline_bps = m
        total = eng._eff_threshold(buy=h, sell=e) + eng._eff_threshold(buy=e, sell=h)
        approx(total, 7.0)


def test_inventory_ladder():
    eng = make_engine()
    eng.cfg.inventory_scale_bps, eng.cfg.inventory_floor_frac = 10.0, 0.5
    e, h = eng.entropy, eng.hedge
    e.set_book(99.9, 100.1)   # mid 100
    h.set_book(99.9, 100.1)
    approx(eng._inv_add_bps(e, h), 0.0)          # flat: dead zone
    e.position = 90.0                             # long $9k of $10k cap
    v = eng._inv_add_bps(e, h)                    # buying entropy adds long
    assert 7.5 < v < 8.5, v                       # u=0.9 -> ~+8
    approx(eng._inv_add_bps(h, e), 0.0)           # selling entropy reduces
    h.position = -90.0                            # hedge short $9k too
    v2 = eng._inv_add_bps(e, h)                   # both legs add -> max()
    assert abs(v2 - v) < 0.6, (v, v2)             # max, not sum


def run_scan(eng):
    async def go():
        # first pass arms the direction, second passes the persistence gate
        # (premium_persist_sec is 0 in the test config)
        eng._scan(__import__("time").time())
        return eng._scan(__import__("time").time())
    return asyncio.run(go())


def unpack(best):
    """(buy, sell, plan, armed_ts) -> (buy, sell, plan)."""
    return best[0], best[1], best[2]


def test_scan_fires_sell_entropy_above_band():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    # entropy 15 bps rich vs hedge: above midline+upper=9 -> sell entropy
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    best = run_scan(eng)
    assert best is not None
    buy, sell, plan = unpack(best)
    assert sell.key == "entropy" and buy.key == "hedge"
    assert plan.exp_edge_usd > 0


def test_scan_quiet_inside_band():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    # entropy 5 bps rich = exactly on the midline: inside the band, no trade
    eng.entropy.set_book(100.04, 100.06)
    eng.hedge.set_book(99.99, 100.01)
    assert run_scan(eng) is None


def test_scan_fires_buy_entropy_below_band():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    # entropy 5 bps CHEAP (premium -5): below midline-lower=+2 -> buy entropy
    eng.entropy.set_book(99.94, 99.96)
    eng.hedge.set_book(99.99, 100.01)
    best = run_scan(eng)
    assert best is not None
    buy, sell, plan = unpack(best)
    assert buy.key == "entropy" and sell.key == "hedge"


def test_scan_respects_position_caps():
    eng = make_engine(midline=0.0, upper=1.0, lower=1.0)
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    eng.entropy.position = -100.0   # entropy already short at its cap
    eng.entropy.cap_usd = 10000.0
    eng.hedge.position = 100.0
    eng.hedge.cap_usd = 10000.0
    assert run_scan(eng) is None


def test_scan_skips_when_free_margin_insufficient():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    eng.entropy.free = 0.0            # no free balance on the sell leg
    assert run_scan(eng) is None


def test_margin_skip_logs_venue_and_numbers(caplog):
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    eng.entropy.free = 0.0
    with caplog.at_level(__import__("logging").INFO, logger="engine"):
        run_scan(eng)
    assert any("free margin insufficient" in r.message and "ENTROPY" in r.message
               and "free=$0.00" in r.message and "need=$" in r.message
               for r in caplog.records)


def test_margin_need_respects_leverage():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    eng.entropy.free = eng.hedge.free = 25.0
    eng.cfg.max_order_notional = 200.0
    eng.cfg.take_fraction = 1.0
    # $200 notional at 10x ties up $20 × 1.2 = $24 — a $25 free balance passes
    eng.cfg.margin_leverage = 10.0
    approx(eng._margin_need(200.0), 24.0)
    assert run_scan(eng) is not None
    # same notional at 1x needs $240: $25 free must block
    eng.cfg.margin_leverage = 1.0
    approx(eng._margin_need(200.0), 240.0)
    assert run_scan(eng) is None


def test_scan_fires_with_sufficient_margin():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    eng.entropy.free = eng.hedge.free = 1e9
    best = run_scan(eng)
    assert best is not None


def test_drift_halt_only_allows_reducing_direction():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    eng._drift_halted = True
    eng.entropy.position = 0.0        # flat: no position to reduce
    assert run_scan(eng) is None
    eng.entropy.position = 10.0       # long entropy: only sell_entropy reduces
    eng.hedge.position = -10.0
    best = run_scan(eng)
    assert best is not None
    buy, sell, plan = unpack(best)
    assert sell.key == "entropy"      # the adding direction must not fire


def test_liq_distance_both_sides():
    from entropy_arb.engine import liq_distance
    assert abs(liq_distance(100.0, 90.0) - 0.10) < 1e-12    # long
    assert abs(liq_distance(100.0, 110.0) - 0.10) < 1e-12   # short
    assert abs(liq_distance(50.0, 49.0) - 0.02) < 1e-12


def test_drift_sentinel_triggers_halt():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.cfg.drift_window_sec = 60.0
    eng.cfg.drift_check_sec = 1.0
    eng.cfg.drift_halt_sec = 10.0
    now = __import__("time").time()

    async def go():
        # 30 minutes worth of premium far above the midline
        for i in range(3600):
            eng._premium_hist.append((now - 1800 + i, 30.0))
        eng._drift_started = now - 20.0     # sustained breach for 20s
        eng._last_drift_check = now - 10.0
        await eng._check_drift()
        assert eng._drift_halted

    asyncio.run(go())


def test_drift_sentinel_disarms_when_premium_returns():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.cfg.drift_window_sec = 60.0
    eng.cfg.drift_check_sec = 1.0
    now = __import__("time").time()

    async def go():
        for i in range(3600):
            eng._premium_hist.append((now - 1800 + i, 5.0))   # on the midline
        eng._drift_started = now - 20.0
        eng._last_drift_check = now - 10.0
        await eng._check_drift()
        assert not eng._drift_halted
        assert eng._drift_started is None

    asyncio.run(go())


def test_drift_stays_halted_without_auto_resume():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.cfg.drift_window_sec = 60.0
    eng.cfg.drift_check_sec = 1.0
    eng._drift_halted = True
    now = __import__("time").time()

    async def go():
        for i in range(3600):
            eng._premium_hist.append((now - 1800 + i, 5.0))
        eng._last_drift_check = now - 10.0
        await eng._check_drift()
        assert eng._drift_halted          # manual restart only (default)

    asyncio.run(go())


def test_drift_auto_resume_after_sustained_return():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.cfg.drift_window_sec = 60.0
    eng.cfg.drift_check_sec = 1.0
    eng.cfg.drift_auto_resume_sec = 5.0
    eng._drift_halted = True
    now = __import__("time").time()

    async def go():
        for i in range(3600):
            eng._premium_hist.append((now - 1800 + i, 5.0))
        eng._last_drift_check = now - 10.0
        eng._drift_back_since = now - 6.0     # back inside for 6s already
        await eng._check_drift()
        assert not eng._drift_halted
        assert eng._drift_back_since is None

    asyncio.run(go())


def test_drift_auto_resume_waits_for_sustained_return():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.cfg.drift_window_sec = 60.0
    eng.cfg.drift_check_sec = 1.0
    eng.cfg.drift_auto_resume_sec = 30.0
    eng._drift_halted = True
    now = __import__("time").time()

    async def go():
        for i in range(3600):
            eng._premium_hist.append((now - 1800 + i, 5.0))
        eng._last_drift_check = now - 10.0
        eng._drift_back_since = now - 5.0     # not sustained yet
        await eng._check_drift()
        assert eng._drift_halted              # still waiting
        assert eng._drift_back_since is not None

    asyncio.run(go())


def test_drift_halt_clamps_reduce_size():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    eng.entropy.free = eng.hedge.free = 1e9
    eng._drift_halted = True
    eng.entropy.position = 0.15               # ~$15 notional left
    eng.hedge.position = -0.15
    best = run_scan(eng)
    assert best is not None
    plan = best[2]
    assert plan.qty <= 0.15 + 1e-9            # never crosses through zero
    assert plan.buy_notional <= 15.0 * 1.05   # clamped to |pos| × price
    assert plan.buy_notional < 30.0           # far below the 500 default cap


class ScriptedVenue(StubVenue):
    def __init__(self, key, label, reads):
        super().__init__(key, label)
        self._reads = list(reads)
        self.force_reads = []

    async def fetch_position(self, force=False):
        self.force_reads.append(force)
        if self._reads:
            return self._reads.pop(0)
        return 0.0


def test_force_reconcile_requires_consistent_pair():
    eng = make_engine()
    eng.hedge = ScriptedVenue("hedge", "RH", reads=[10.0, 10.0])
    eng.venues = {"entropy": eng.entropy, "hedge": eng.hedge}
    eng._venue_unresolved_until["hedge"] = float("inf")

    asyncio.run(eng._reconcile_venue(eng.hedge, strict=False, force=True))
    assert "hedge" not in eng._venue_unresolved_until
    assert abs(eng.hedge.position - 10.0) < 1e-12     # adopted
    assert eng.hedge.force_reads == [True, True]      # both bypass cache


def test_force_reconcile_retries_until_consistent():
    eng = make_engine()
    # first pair differs (REST catching up), second pair agrees
    eng.hedge = ScriptedVenue("hedge", "RH", reads=[5.0, 10.0, 10.0, 10.0])
    eng.venues = {"entropy": eng.entropy, "hedge": eng.hedge}
    eng._venue_unresolved_until["hedge"] = float("inf")

    asyncio.run(eng._reconcile_venue(eng.hedge, strict=False, force=True))
    assert "hedge" not in eng._venue_unresolved_until
    assert abs(eng.hedge.position - 10.0) < 1e-12


def test_force_reconcile_stays_fused_when_never_consistent():
    eng = make_engine()
    eng.hedge = ScriptedVenue("hedge", "RH",
                              reads=[5.0, 10.0] * 3)   # 3 rounds all differ
    eng.venues = {"entropy": eng.entropy, "hedge": eng.hedge}
    eng._venue_unresolved_until["hedge"] = float("inf")

    asyncio.run(eng._reconcile_venue(eng.hedge, strict=False, force=True))
    assert "hedge" in eng._venue_unresolved_until      # still fused
    assert eng.hedge.position == 0.0                   # nothing adopted
    assert eng.hedge.key not in eng._venue_down        # no venue-down penalty


def test_liquidation_halts_before_flatten():
    eng = make_engine()

    async def risk_venue():
        return 100.0, 90.0        # 10% from liquidation: at the threshold

    async def risk_none():
        return None

    eng.entropy.fetch_risk = risk_venue
    eng.hedge.fetch_risk = risk_none
    order = []

    async def fake_flatten():
        order.append(("flatten", eng.halted))

    eng._flatten_all = fake_flatten
    asyncio.run(eng._check_liquidation())
    assert eng.halted
    assert order == [("flatten", True)]   # halted was set BEFORE flattening


def _hedge_engine():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.entropy.set_book(100.0, 100.02)
    eng.hedge.set_book(99.99, 100.01)
    eng.entropy.position = 0.3            # +0.3 long entropy
    eng.hedge.position = -0.1             # net +0.2 -> reduce entropy
    eng.cfg.hedge_force_close_timeout_sec = 2.0
    eng.cfg.hedge_retry_interval_sec = 0.01
    return eng


def test_hedge_retries_with_widening_slip():
    eng = _hedge_engine()
    calls = []

    async def fake_send(is_buy, qty, limit_px, reduce_only):
        calls.append((is_buy, qty, limit_px))
        if len(calls) == 1:
            return {"status": "send-failed", "filled_base": 0.0,
                    "avg_px": None, "err": "network", "unresolved": False}
        return {"status": "filled", "filled_base": qty,
                "avg_px": 100.0, "err": None, "unresolved": False}

    eng.entropy.send_taker = fake_send
    asyncio.run(eng._hedge())
    assert len(calls) == 2                       # first failed, second ok
    assert calls[1][2] < calls[0][2]             # wider slip -> lower sell limit
    assert not eng.halted
    assert eng.entropy.position < 0.2            # reduced
    assert eng.hedges == 2


def test_hedge_force_close_halts_after_timeout():
    eng = _hedge_engine()
    calls = []

    async def fake_send(is_buy, qty, limit_px, reduce_only):
        calls.append((is_buy, qty, limit_px))
        return {"status": "send-failed", "filled_base": 0.0,
                "avg_px": None, "err": "network", "unresolved": False}

    eng.entropy.send_taker = fake_send
    asyncio.run(eng._hedge())
    assert eng.halted                          # exposure could not be bounded
    assert len(calls) >= 3                     # retries + force-close last shot
    assert eng.entropy.position == 0.3         # nothing filled
    # the last-chance order carries the WIDEST protection (200 bps = 98.0),
    # not a zero-slip limit pinned to the touch (100.0)
    assert calls[-1][2] < calls[-2][2]
    assert calls[-1][2] <= 98.0 + 1e-6


def test_hedge_small_residual_carries_not_halts():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.entropy.set_book(100.0, 100.02)
    eng.hedge.set_book(99.99, 100.01)
    eng.entropy.position = 0.001               # ~$0.1 residual: below min
    eng.hedge.position = -0.0
    eng.cfg.hedge_force_close_timeout_sec = 0.05
    eng.cfg.hedge_retry_interval_sec = 0.01
    asyncio.run(eng._hedge())
    assert not eng.halted                      # dust carries, no false halt


def test_status_payload_fields():
    from entropy_arb.webui import status_payload
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    eng.entropy.position, eng.hedge.position = 0.1, -0.1
    eng.entropy.free = eng.hedge.free = 50.0
    eng.trades = 3
    eng.halted = True
    eng._drift_halted = True
    p = status_payload(eng)
    assert p["symbol"] == "SNDK" and p["hedge_name"] == "RH"
    assert p["halted"] is True and p["drift_halted"] is True
    assert p["midline_bps"] == 5.0
    assert p["band_low"] == 2.0 and p["band_high"] == 9.0
    assert p["premium_bps"] is not None
    assert len(p["venues"]) == 2
    v = p["venues"][0]
    for k in ("name", "bid", "ask", "spread_bps", "data_age", "position",
              "equity", "free", "volume_usd", "cap_usd", "stale", "down",
              "limited", "unresolved"):
        assert k in v
    assert len(p["directions"]) == 2
    for d in p["directions"]:
        assert d["label"] and "premium" in d and "hurdle" in d
        assert "armed" in d
    assert p["trades"] == 3
    assert p["net_delta"] == 0.0
    assert p["recent_trades"] == []
    assert p["premium_history"] == []
    assert p["uptime_sec"] >= 0


def test_status_loop_log_format(capsys):
    """The [status] line must format cleanly (placeholder/arg mismatch would
    raise inside the logging handler)."""
    eng = make_engine()
    eng.entropy.set_book(100.0, 100.02)
    eng.hedge.set_book(99.99, 100.01)
    eng.entropy.position, eng.hedge.position = 0.1, -0.1
    eng._drift_halted = True
    eng.halted = True
    eng.cfg.status_interval_sec = 0.01

    async def go():
        task = asyncio.create_task(eng._status_loop())
        await asyncio.sleep(eng.cfg.status_interval_sec + 0.05)
        eng.request_stop()
        task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await task

    asyncio.run(go())
    err = capsys.readouterr().err
    assert "Logging error" not in err
    assert "not all arguments converted" not in err


def test_log_placeholders_match_args():
    """Static guard: every %-style log call in the package must have exactly
    as many arguments as placeholders (catches the class of bug where a
    marker arg is added without its %s)."""
    import ast
    import re
    import pathlib

    root = pathlib.Path(os.path.join(os.path.dirname(__file__), "..",
                                     "entropy_arb"))
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("debug", "info", "warning",
                                           "error", "critical", "exception")):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant) \
                    or not isinstance(node.args[0].value, str):
                continue
            fmt = node.args[0].value
            # %% is a literal percent: strip it before matching so the
            # flags class can never span across a "%% word" pair
            n = len(re.findall(r"%(?:\d+\$)?[-+ #0]*\d*(?:\.\d+)?"
                               r"[diouxXeEfFgGcrsa]", fmt.replace("%%", "")))
            if n != len(node.args) - 1:
                raise AssertionError(
                    f"{path.name}:{node.lineno} log.{node.func.attr} has "
                    f"{n} placeholders but {len(node.args) - 1} args: "
                    f"{fmt[:70]}")


def test_armed_reset_when_book_goes_stale():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    now = __import__("time").time()

    def scan(t):
        async def go():
            return eng._scan(t)
        asyncio.run(go())

    scan(now)                              # arms sell_entropy
    assert eng._armed["sell_entropy"] is not None
    eng.entropy.book.ready = False         # book dies mid-armed
    scan(now + 5.0)
    assert eng._armed["sell_entropy"] is None   # re-armed on recovery
    eng.entropy.set_book(100.14, 100.16)
    scan(now + 6.0)                        # fresh book: re-arm, not fire
    assert eng._armed["sell_entropy"] is not None
    # (premium_persist_sec is 0 in the test config, so the re-armed pass
    # fires on the NEXT scan — the reset itself is the key assertion)


def test_armed_kept_when_venue_locked_or_throttled():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    now = __import__("time").time()

    def scan(t):
        async def go():
            return eng._scan(t)
        asyncio.run(go())

    scan(now)
    assert eng._armed["sell_entropy"] is not None
    # budget exhausted: keep armed (signal real, just throttled)
    eng._sends.setdefault("entropy", __import__("collections").deque())
    for _ in range(100):
        eng._sends["entropy"].append(now)
    scan(now + 5.0)
    assert eng._armed["sell_entropy"] is not None


def test_funding_gate_only_for_opening_directions():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    e, h = eng.entropy, eng.hedge
    eng.cfg.funding_hold_hours = 1.0
    # hourly rates: entropy -3 bps/h (shorts pay), hedge +2 bps/h (longs pay)
    e.funding_bps_h, h.funding_bps_h = -3.0, 2.0
    # flat: both directions are OPENING -> funding cost counts.
    # sell_entropy = short entropy (pays 3) + long hedge (pays 2)
    # -> adverse cost 5 bps/h × 1h = 5 -> +min(2.5, cap)
    approx(eng._funding_cost_bps(buy=h, sell=e), 5.0)
    approx(eng._eff_threshold(buy=h, sell=e), 9.0 + 2.5)
    # buy_entropy = long entropy (funding -3: longs RECEIVE) + short hedge
    # (funding +2: shorts RECEIVE) -> no adverse side -> cost 0
    approx(eng._funding_cost_bps(buy=e, sell=h), 0.0)
    approx(eng._eff_threshold(buy=e, sell=h), -2.0)
    # with inventory this direction reduces, funding is NOT gated
    e.position = 10.0   # long entropy: sell_entropy reduces -> no gate
    h.position = -10.0
    approx(eng._eff_threshold(buy=h, sell=e), 9.0)


def test_funding_cost_scales_with_hold_hours():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    e, h = eng.entropy, eng.hedge
    # 8 bps per 8h on the adverse side == 1 bps/h
    e.funding_bps_h, h.funding_bps_h = None, None
    e.funding_bps_h = -8.0 / 8.0   # short entropy pays 1 bps/h
    h.funding_bps_h = 0.0
    eng.cfg.funding_hold_hours = 4.0
    approx(eng._funding_cost_bps(buy=h, sell=e), 4.0)   # 1 bps/h × 4h
    eng.cfg.funding_hold_hours = 8.0
    approx(eng._funding_cost_bps(buy=h, sell=e), 8.0)
    eng.cfg.funding_hold_hours = 0.5
    approx(eng._funding_cost_bps(buy=h, sell=e), 0.5)


def test_funding_gate_capped_and_missing_rates_ignored():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    e, h = eng.entropy, eng.hedge
    eng.cfg.funding_hold_hours = 1.0
    e.funding_bps_h, h.funding_bps_h = -100.0, 0.0
    eng.cfg.funding_cap_bps = 5.0
    # raw cost 100 × 0.5 = 50, capped at 5
    approx(eng._eff_threshold(buy=h, sell=e), 9.0 + 5.0)
    # unknown rates contribute nothing
    e.funding_bps_h, h.funding_bps_h = None, None
    approx(eng._funding_cost_bps(buy=h, sell=e), 0.0)
    approx(eng._eff_threshold(buy=h, sell=e), 9.0)


def test_slippage_gate_only_on_opening_direction():
    from entropy_arb.slippage import SlipModel
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    # fresh model: isolated temp state so a repo's logs/slip_state.json can't
    # leak samples in (that file is a shared runtime artifact, not test input)
    eng.slippage = SlipModel(state_file=os.path.join(tempfile.mkdtemp(),
                                                     "slip.json"),
                             min_samples=3)
    # both legs with known p50 = 2 bps -> round-trip gate = (2+2)*2 = 8
    for i in range(3):
        eng.slippage.observe("entropy", "SNDK", 2.0, 1.0, 1.0)
        eng.slippage.observe("hedge", "SNDK", 2.0, 1.0, 1.0)
    e, h = eng.entropy, eng.hedge
    # flat: both directions opening -> gate charged
    assert abs(eng._eff_threshold(buy=h, sell=e) - (9.0 + 8.0)) < 1e-9
    # an inventory this direction reduces -> no slip gate
    e.position = 10.0
    h.position = -10.0
    assert abs(eng._eff_threshold(buy=h, sell=e) - 9.0) < 1e-9


def test_slippage_disabled_no_gate_no_model():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.slippage = None                 # disabled path -> no model, no gate
    assert abs(eng._eff_threshold(buy=eng.hedge, sell=eng.entropy)
               - 9.0) < 1e-9


def test_slippage_observe_filters_nonmarket_legs():
    """err/unresolved legs must not enter the slip/miss pools."""
    from entropy_arb.slippage import SlipModel
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.slippage = SlipModel(state_file=os.path.join(tempfile.mkdtemp(),
                                                     "slip.json"),
                             min_samples=1)
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    plan, reason = eng._plan(eng.hedge, eng.entropy, 500.0)
    assert reason == "ok"

    async def fake_send_err(is_buy, qty, limit_px, reduce_only=False):
        return {"status": "send-failed", "filled_base": 0.0, "avg_px": None,
                "err": "network", "unresolved": False}

    async def fake_send_unresolved(is_buy, qty, limit_px, reduce_only=False):
        return {"status": "timeout", "filled_base": 0.0, "avg_px": None,
                "err": None, "unresolved": True}

    eng.entropy.send_taker = fake_send_err
    eng.hedge.send_taker = fake_send_unresolved
    asyncio.run(eng._execute(eng.hedge, eng.entropy, plan, time.time()))
    assert len(eng.slippage._venues.get(("entropy", "SNDK"),
                                        __import__("collections").deque())) == 0
    # both legs skipped entirely -> no venue state created
    assert eng.slippage.miss_rate("entropy", "SNDK") is None
    assert eng.slippage.miss_rate("hedge", "SNDK") is None


def test_slippage_observe_counts_market_miss():
    """a real IOC miss (no err, no unresolved, zero fill) enters the pool."""
    from entropy_arb.slippage import SlipModel
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    eng.slippage = SlipModel(state_file=os.path.join(tempfile.mkdtemp(),
                                                     "slip.json"),
                             min_samples=1)
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    plan, reason = eng._plan(eng.hedge, eng.entropy, 500.0)
    assert reason == "ok"

    async def fake_send_miss(is_buy, qty, limit_px, reduce_only=False):
        return {"status": "canceled", "filled_base": 0.0, "avg_px": None,
                "err": None, "unresolved": False}

    eng.entropy.send_taker = fake_send_miss
    eng.hedge.send_taker = fake_send_miss
    asyncio.run(eng._execute(eng.hedge, eng.entropy, plan, time.time()))
    assert eng.slippage.miss_rate("entropy", "SNDK") == 1.0
    assert eng.slippage.miss_rate("hedge", "SNDK") == 1.0


def test_miss_alert_warns_and_rate_limits(caplog):
    """miss_rate > threshold -> warning + notify, at most once/hour/venue."""
    from entropy_arb.config import SlippageConf
    from entropy_arb.slippage import SlipModel
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    # SlippageConf is frozen: rebuild it with the lower threshold
    sc = eng.cfg.slippage
    eng.cfg.slippage = SlippageConf(enabled=sc.enabled, state_file=sc.state_file,
                                    min_samples=sc.min_samples,
                                    window_n=sc.window_n,
                                    window_hours=sc.window_hours,
                                    gate_weight=sc.gate_weight,
                                    protect_mult=sc.protect_mult,
                                    protect_floor_bps=sc.protect_floor_bps,
                                    protect_cap_bps=sc.protect_cap_bps,
                                    miss_threshold=0.1)
    eng.slippage = SlipModel(state_file=os.path.join(tempfile.mkdtemp(),
                                                     "slip.json"),
                             min_samples=3)
    for _ in range(5):
        eng.slippage.observe("entropy", "SNDK", None, 0.0, 1.0)  # 100% miss
    notified = []

    async def fake_notify(text):
        notified.append(text)

    eng._notify = fake_notify
    with caplog.at_level(__import__("logging").WARNING, logger="engine"):
        asyncio.run(eng._check_miss_alert())
        asyncio.run(eng._check_miss_alert())      # second call same hour
    assert len(notified) == 1                      # rate-limited to 1/hour
    assert any("miss rate" in r.message for r in caplog.records)


def test_hurdle_breakdown_exposes_parts():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    p = eng._hurdle_breakdown(buy=eng.hedge, sell=eng.entropy)
    assert set(p) == {"base", "inventory", "funding", "slip_gate"}
    assert p["base"] == 9.0 and p["inventory"] == 0.0
    assert p["funding"] == 0.0 and p["slip_gate"] == 0.0
    # reconstruct matches _eff_threshold
    assert abs(eng._eff_threshold(buy=eng.hedge, sell=eng.entropy)
               - (p["base"] + p["inventory"] + p["funding"] + p["slip_gate"])
               ) < 1e-9


def test_trades_csv_shadow_columns_present():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    path = os.path.join(tempfile.mkdtemp(), "trades.csv")
    eng.cfg.trades_csv = path
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    plan, reason = eng._plan(eng.hedge, eng.entropy, 500.0)
    assert reason == "ok"
    from entropy_arb.engine import CSV_HEADER
    eng._log_csv("sell_entropy", eng.hedge, eng.entropy, plan, True,
                 0.5, 0.5, "filled", "filled", 0.5, 1.0,
                 12.3, 45.6, 2.1, 1.5, 0.8, 25.0, 27.0)
    with open(path, newline="") as fh:
        import csv as _csv
        rows = list(_csv.reader(fh))
    assert rows[0] == CSV_HEADER
    assert len(rows[1]) == len(CSV_HEADER)
    assert rows[1][-2:] == ["25.000", "27.000"]
    assert "dyn_protect_buy_bps" in CSV_HEADER


def test_trades_csv_row_width_matches_header():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    path = os.path.join(tempfile.mkdtemp(), "trades.csv")
    eng.cfg.trades_csv = path
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    plan, reason = eng._plan(eng.hedge, eng.entropy, 500.0)
    assert reason == "ok"
    from entropy_arb.engine import CSV_HEADER
    eng._log_csv("sell_entropy", eng.hedge, eng.entropy, plan, True,
                 0.5, 0.5, "filled", "filled", 0.5, 1.0,
                 12.3, 45.6, 2.1, 1.5, 0.8)
    with open(path, newline="") as fh:
        import csv as _csv
        rows = list(_csv.reader(fh))
    assert rows[0] == CSV_HEADER
    assert len(rows[1]) == len(CSV_HEADER)
    # missing telemetry cells stay empty (no "None" pollution)
    path2 = os.path.join(tempfile.mkdtemp(), "trades2.csv")
    eng.cfg.trades_csv = path2
    eng._log_csv("buy_entropy", eng.entropy, eng.hedge, plan, True,
                 0.5, 0.5, "filled", "filled", 0.5, 1.0)
    with open(path2, newline="") as fh:
        rows2 = list(_csv.reader(fh))
    assert len(rows2[1]) == len(CSV_HEADER)
    assert rows2[1][-5:] == ["", "", "", "", ""]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name:40s} OK")
