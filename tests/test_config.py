"""Config loading: example file, validation, CLI-selected markets.

Run:  python3 -m pytest tests/  (or  python3 tests/test_config.py)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from entropy_arb.config import ConfigError, load_config  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
EXAMPLE = os.path.join(ROOT, "config.example.yaml")
NO_ENV = os.path.join(tempfile.gettempdir(), "entropy-arb-no-such.env")


def write_tmp(text: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    f.write(text)
    f.close()
    return f.name


MINIMAL = """
thresholds:
  midline_bps: 5.0
  upper_bps: 4.0
  lower_bps: 3.0
"""


def load(yaml_text: str, symbol="SNDK", hedge="lighter-rh"):
    return load_config(write_tmp(yaml_text), NO_ENV,
                       symbol=symbol, hedge_venue=hedge)


def test_example_config_loads():
    cfg = load_config(EXAMPLE, NO_ENV,
                      symbol="SNDK", hedge_venue="lighter-rh")
    assert cfg.symbol == "SNDK"
    assert cfg.entropy.kind == "hl" and cfg.entropy.hl_dex == "io"
    assert cfg.hedge_venue == "lighter-rh"
    assert cfg.hedge.kind == "lighter"
    assert cfg.hedge.lighter_profile.chain_id == 466324
    assert cfg.entropy.symbol == "SNDK" and cfg.hedge.symbol == "SNDK"
    assert cfg.recorder_enabled and cfg.recorder_csv
    assert cfg.dashboard and cfg.log_file


def test_minimal_defaults():
    cfg = load(MINIMAL, hedge="lighter")
    assert cfg.midline_bps == 5.0 and cfg.upper_bps == 4.0 and cfg.lower_bps == 3.0
    assert cfg.hedge.label == "LIGHTER"
    assert cfg.hedge.lighter_profile.chain_id == 304
    assert cfg.take_fraction == 0.5          # defaults kick in
    assert cfg.recorder_enabled is True


def test_tradexyz_hedge():
    cfg = load(MINIMAL, hedge="tradexyz")
    assert cfg.hedge.kind == "hl" and cfg.hedge.hl_dex == "xyz"
    assert cfg.hedge.label == "XYZ"


def expect_error(yaml_text: str, needle: str, **kw):
    try:
        load(yaml_text, **kw)
    except ConfigError as e:
        assert needle in str(e), f"{needle!r} not in {e}"
        return
    raise AssertionError(f"expected ConfigError containing {needle!r}")


def test_unknown_key_rejected():
    expect_error(MINIMAL + "\nthresholdz:\n  x: 1\n",
                 "unknown config key 'thresholdz'")
    expect_error(MINIMAL + "\nsizing:\n  take_fractionn: 0.5\n",
                 "sizing.take_fractionn")


def test_markets_no_longer_config_keys():
    # symbol / hedge_venue moved to --symbol / --hedge: leftovers in the
    # YAML must fail loudly, not silently override the flags
    expect_error("symbol: SNDK\n" + MINIMAL, "unknown config key 'symbol'")
    expect_error("hedge_venue: tradexyz\n" + MINIMAL,
                 "unknown config key 'hedge_venue'")


def test_bad_cli_markets():
    expect_error(MINIMAL, "--hedge", hedge="binance")
    expect_error(MINIMAL, "--symbol", symbol="")


def test_missing_thresholds():
    expect_error("recorder:\n  enabled: true\n", "thresholds.")


def test_nonpositive_band():
    expect_error("thresholds:\n"
                 "  midline_bps: 5\n  upper_bps: 0\n  lower_bps: 3\n",
                 "must be > 0")


def test_phase1_defaults():
    cfg = load(MINIMAL)
    assert cfg.premium_persist_sec == 0.5        # phantom filter on by default
    assert cfg.staleness_sec == 2.5              # taker-tight freshness
    assert cfg.cooldown_sec == 1.0
    assert cfg.data_staleness_sec == 60.0
    assert cfg.drift_window_sec == 1800.0
    assert cfg.drift_check_sec == 60.0
    assert cfg.drift_halt_sec == 600.0
    assert cfg.drift_band_factor == 1.0
    assert cfg.drift_auto_resume_sec == 0.0   # manual restart only
    assert cfg.risk_loop_sec == 30.0
    assert cfg.liquidation_distance_pct == 10.0
    assert cfg.margin_reserve_factor == 1.2
    assert cfg.margin_leverage == 1.0        # conservative default (1x)
    assert cfg.hedge_retry_slips_bps == (20.0, 50.0, 100.0)
    assert cfg.hedge_retry_interval_sec == 0.5
    assert cfg.hedge_force_close_timeout_sec == 5.0
    assert cfg.hedge_force_close_slip_bps == 200.0
    assert cfg.funding_cap_bps == 5.0
    assert cfg.funding_hold_hours == 4.0


def test_risk_config_overrides():
    cfg = load(MINIMAL + """
execution:
  risk_loop_sec: 15.0
  liquidation_distance_pct: 5.0
  margin_reserve_factor: 2.0
  margin_leverage: 10.0
  data_staleness_sec: 30.0
  drift_auto_resume_sec: 120.0
  hedge_retry_slips_bps: [10, 30]
  hedge_retry_interval_sec: 0.2
  hedge_force_close_timeout_sec: 3.0
  hedge_force_close_slip_bps: 300.0
  funding_cap_bps: 8.0
  funding_hold_hours: 6.0
""")
    assert cfg.risk_loop_sec == 15.0
    assert cfg.liquidation_distance_pct == 5.0
    assert cfg.margin_reserve_factor == 2.0
    assert cfg.margin_leverage == 10.0
    assert cfg.data_staleness_sec == 30.0
    assert cfg.drift_auto_resume_sec == 120.0
    assert cfg.hedge_retry_slips_bps == (10.0, 30.0)
    assert cfg.hedge_retry_interval_sec == 0.2
    assert cfg.hedge_force_close_timeout_sec == 3.0
    assert cfg.hedge_force_close_slip_bps == 300.0
    assert cfg.funding_cap_bps == 8.0
    assert cfg.funding_hold_hours == 6.0


def test_web_dashboard_defaults():
    cfg = load(MINIMAL)
    assert cfg.web_dashboard_enabled is True
    assert cfg.web_dashboard_host == "127.0.0.1"
    assert cfg.web_dashboard_port == 8787


def test_web_dashboard_override():
    cfg = load(MINIMAL + """
web_dashboard:
  enabled: false
  host: 0.0.0.0
  port: 9090
""")
    assert cfg.web_dashboard_enabled is False
    assert cfg.web_dashboard_host == "0.0.0.0"
    assert cfg.web_dashboard_port == 9090


def test_web_dashboard_validation():
    expect_error(MINIMAL + "\nweb_dashboard:\n  port: 0\n",
                 "web_dashboard.port")
    expect_error(MINIMAL + "\nweb_dashboard:\n  port: 70000\n",
                 "web_dashboard.port")
    expect_error(MINIMAL + "\nweb_dashboard:\n  enabled: maybe\n",
                 "web_dashboard.enabled")


def test_risk_config_validation():
    expect_error(MINIMAL + "\nexecution:\n  liquidation_distance_pct: 0\n",
                 "liquidation_distance_pct")
    expect_error(MINIMAL + "\nexecution:\n  liquidation_distance_pct: 120\n",
                 "liquidation_distance_pct")
    expect_error(MINIMAL + "\nexecution:\n  margin_reserve_factor: 0.5\n",
                 "margin_reserve_factor")
    expect_error(MINIMAL + "\nexecution:\n  margin_leverage: 0.5\n",
                 "margin_leverage")
    expect_error(MINIMAL + "\nexecution:\n  hedge_retry_slips_bps: []\n",
                 "hedge_retry_slips_bps")
    expect_error(MINIMAL + "\nexecution:\n  hedge_retry_slips_bps: [20, -5]\n",
                 "hedge_retry_slips_bps")
    expect_error(MINIMAL + "\nexecution:\n  hedge_retry_slips_bps: [20, x]\n",
                 "hedge_retry_slips_bps")
    expect_error(MINIMAL + "\nexecution:\n  hedge_retry_slips_bps: 20\n",
                 "hedge_retry_slips_bps")
    expect_error(MINIMAL + "\nexecution:\n  hedge_force_close_slip_bps: 0\n",
                 "hedge_force_close_slip_bps")
    expect_error(MINIMAL + "\nexecution:\n  funding_cap_bps: -1\n",
                 "funding_cap_bps")
    expect_error(MINIMAL + "\nexecution:\n  funding_hold_hours: 0\n",
                 "funding_hold_hours")


def test_example_config_phase1_values():
    cfg = load_config(EXAMPLE, NO_ENV, symbol="SNDK", hedge_venue="lighter-rh")
    assert cfg.entropy.cap_usd == 40 and cfg.hedge.cap_usd == 40
    assert cfg.max_order_notional == 20
    assert cfg.inventory_floor_frac == 0.5
    assert cfg.premium_persist_sec == 0.5
    assert cfg.upper_bps == 5.0 and cfg.lower_bps == 5.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name:40s} OK")
