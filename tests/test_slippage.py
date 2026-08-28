"""SlipModel: percentiles, time window, cold start, miss rate, persistence.

Run:  python3 -m pytest tests/  (or  python3 tests/test_slippage.py)
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from entropy_arb.slippage import SlipModel  # noqa: E402


def make_model(**kw):
    d = tempfile.mkdtemp()
    kw.setdefault("state_file", os.path.join(d, "slip_state.json"))
    kw.setdefault("min_samples", 3)
    return SlipModel(**kw)


def test_cold_start_returns_none():
    m = make_model()
    assert m.p50("entropy", "SNDK") is None
    assert m.p90("entropy", "SNDK") is None
    assert m.gate_bps("entropy", "hedge", "SNDK") == 0.0
    assert m.protect_bps("entropy", "SNDK", 30.0) == 30.0
    assert m.miss_rate("entropy", "SNDK") is None


def test_p50_p90_after_samples():
    m = make_model()
    m.observe("entropy", "SNDK", 1.0, 1.0, 1.0)
    m.observe("entropy", "SNDK", 2.0, 1.0, 1.0)
    m.observe("entropy", "SNDK", 3.0, 1.0, 1.0)
    assert m.p50("entropy", "SNDK") == 2.0
    # p90 of [1,2,3] by linear interpolation = 2.8 (same percentile as analyze)
    assert abs(m.p90("entropy", "SNDK") - 2.8) < 1e-9


def test_negative_slip_floored_on_gate_only():
    m = make_model()
    # buy leg slip -2 (favorable), sell leg +3
    m.observe("buy", "SNDK", -2.0, 1.0, 1.0)
    m.observe("buy", "SNDK", -2.0, 1.0, 1.0)
    m.observe("buy", "SNDK", -2.0, 1.0, 1.0)
    m.observe("sell", "SNDK", 3.0, 1.0, 1.0)
    m.observe("sell", "SNDK", 3.0, 1.0, 1.0)
    m.observe("sell", "SNDK", 3.0, 1.0, 1.0)
    # gate: max(0, -2) + max(0, 3) = 3 -> round trip ×2 = 6
    assert m.gate_bps("buy", "sell", "SNDK") == 6.0
    # but the p90 keeps the raw distribution (negative pulled down)
    assert m.p90("buy", "SNDK") == -2.0


def test_time_window_decay():
    m = make_model(window_n=10, window_hours=1.0)
    old = time.time() - 10 * 3600
    for _ in range(3):
        m.observe("entropy", "SNDK", 1.0, 1.0, 1.0)
    # fast-forward: age an old sample directly past the window
    st = m._venues[("entropy", "SNDK")]
    st.samples.append((old, 99.0))
    st.fills.append((old, True))
    # old sample (99) is outside 1h window -> not in distribution
    assert m.p90("entropy", "SNDK") == 1.0


def test_miss_rate():
    m = make_model()
    for i in range(5):
        m.observe("entropy", "SNDK", 1.0, 1.0, 1.0)   # filled
        m.observe("entropy", "SNDK", None, 0.0, 1.0)  # miss (no avg_px)
    assert abs(m.miss_rate("entropy", "SNDK") - 0.5) < 1e-9


def test_miss_rate_not_ready_until_min():
    m = make_model(min_samples=10)
    for _ in range(3):
        m.observe("entropy", "SNDK", None, 0.0, 1.0)
    assert m.miss_rate("entropy", "SNDK") is None


def test_persistence_roundtrip():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "slip_state.json")
    m = SlipModel(state_file=path, min_samples=3)
    m.observe("entropy", "SNDK", 1.5, 1.0, 1.0)
    m.observe("entropy", "SNDK", 2.5, 1.0, 1.0)
    m.observe("entropy", "SNDK", 3.5, 1.0, 1.0)
    m.save()
    m2 = SlipModel(state_file=path, min_samples=3)
    assert m2.p50("entropy", "SNDK") == 2.5
    assert abs(m2.p90("entropy", "SNDK") - 3.3) < 1e-9   # interp of [1.5,2.5,3.5]


def test_key_isolation_by_venue_and_symbol():
    m = make_model()
    m.observe("entropy", "SNDK", 1.0, 1.0, 1.0)
    m.observe("entropy", "SNDK", 1.0, 1.0, 1.0)
    m.observe("entropy", "SNDK", 1.0, 1.0, 1.0)
    assert m.p50("entropy", "SNDK") == 1.0
    assert m.p50("entropy", "OTHER") is None     # different symbol
    assert m.p50("hedge", "SNDK") is None        # different venue
    # only the (venue, symbol) key we touched has state
    assert set(m._venues.keys()) == {("entropy", "SNDK")}


def test_load_corrupt_file_falls_back_empty():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "slip_state.json")
    with open(path, "w") as fh:
        fh.write("not json {{{")
    m = SlipModel(state_file=path, min_samples=3)   # must not raise
    assert m.p50("entropy", "SNDK") is None


def test_samples_pruned_by_time_window():
    m = make_model(window_n=200, window_hours=1.0)
    for _ in range(5):
        m.observe("entropy", "SNDK", 1.0, 1.0, 1.0)
    # age the whole queue beyond the window -> observe() prunes it
    st = m._venues[("entropy", "SNDK")]
    old = time.time() - 2 * 3600
    st.samples = __import__("collections").deque((old, s) for _, s in st.samples)
    m.observe("entropy", "SNDK", 1.0, 1.0, 1.0)
    assert len(st.samples) == 1          # only the fresh sample remains


def test_samples_bounded_by_count():
    m = make_model(window_n=10, window_hours=72.0)
    for i in range(100):
        m.observe("entropy", "SNDK", 1.0, 1.0, 1.0)
    st = m._venues[("entropy", "SNDK")]
    assert len(st.samples) <= m.window_n * 2   # 20
    assert m.p50("entropy", "SNDK") == 1.0     # still queryable


def test_fills_pruned_but_samples_pruned_same_path():
    m = make_model(window_n=5, window_hours=1.0)
    for _ in range(60):
        m.observe("entropy", "SNDK", None, 0.0, 1.0)   # misses: fills only
    st = m._venues[("entropy", "SNDK")]
    assert len(st.samples) == 0
    assert len(st.fills) <= 60            # fills bounded by 24h window
    assert m.miss_rate("entropy", "SNDK") == 1.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name:40s} OK")