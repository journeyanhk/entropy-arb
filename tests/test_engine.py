"""Engine signal math: midline band directions, inventory ladder, scan.

Run:  python3 -m pytest tests/  (or  python3 tests/test_engine.py)
"""
import asyncio
import os
import sys
import tempfile

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
        self.orders_per_min = 30
        self.last_traded_ts = 0.0
        self.free = None
        self.book = OrderBook()

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


def test_scan_fires_sell_entropy_above_band():
    eng = make_engine(midline=5.0, upper=4.0, lower=3.0)
    # entropy 15 bps rich vs hedge: above midline+upper=9 -> sell entropy
    eng.entropy.set_book(100.14, 100.16)
    eng.hedge.set_book(99.99, 100.01)
    best = run_scan(eng)
    assert best is not None
    buy, sell, plan = best
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
    buy, sell, plan = best
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
    buy, sell, plan = best
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name:40s} OK")
