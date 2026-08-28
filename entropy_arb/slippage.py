"""Per-venue realized-slippage model (二期③).

Feeds the engine back two ways:

  1. OPENING-side hurdle: `gate_bps()` charges the whole round trip
     (open two legs + close two legs) at open time — `one_way × 2 × weight`
     where `one_way = max(0, p50_buy) + max(0, p50_sell)`. Reducing trades
     are never priced out by slippage (mirrors the funding gate), and the
     round-trip charge preserves the "net >= upper+lower - costs" guarantee.

  2. Per-leg price protection: `protect_bps()` adapts the taker limit to the
     venue's realized distribution — `clamp(p90*protect_mult, floor, cap)`.
     The cap is shared with the current static value so the first week it can
     only TIGHTEN, never loosen (loosening needs miss-data before it's safe).

Statistical-honesty guards (the design review's four points):
  - window is both "recent N trades" AND "recent T hours" (time decay, so a
    low-vol regime's old samples don't get blended into today's).
  - negative slippage is floored at 0 on the HURDLE side only; the p90 for
    protection keeps the raw distribution (negatives safely pull it down).
  - survivor bias: fills are counted (filled/partial/miss) so `miss_rate()`
    exposes how often protection is rejecting orders — the evidence a too-tight
    limit is throwing away fills. Loosening is still gated by config, not
    self-served.
  - state is keyed by (venue_key, symbol) so multi-venue / multi-symbol stays
    clean and portable.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

log = logging.getLogger("slippage")

Key = Tuple[str, str]                      # (venue_key, symbol)
Sample = Tuple[float, float]               # (ts, slip_bps)
FillEvent = Tuple[float, bool]             # (ts, filled?)

MISS_WINDOW_SEC = 24 * 3600


def _pctl(sorted_vals: list, q: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * q / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


class _VenueState:
    __slots__ = ("samples", "fills")

    def __init__(self) -> None:
        self.samples: Deque[Sample] = deque()
        self.fills: Deque[FillEvent] = deque()


class SlipModel:
    def __init__(self, state_file: str = "logs/slip_state.json",
                 window_n: int = 200, window_hours: float = 72.0,
                 min_samples: int = 30, miss_threshold: float = 0.15,
                 protect_mult: float = 1.5, protect_floor_bps: float = 10.0,
                 protect_cap_bps: float = 30.0,
                 gate_weight: float = 1.0) -> None:
        self.state_file = state_file
        self.window_n = max(window_n, 1)
        self.window_hours = max(window_hours, 0.0)
        self.min_samples = max(min_samples, 1)
        self.miss_threshold = miss_threshold
        self.protect_mult = protect_mult
        self.protect_floor_bps = protect_floor_bps
        self.protect_cap_bps = protect_cap_bps
        self.gate_weight = gate_weight
        self._venues: Dict[Key, _VenueState] = {}
        self._dirty = 0
        self.load()

    # ------------------------------------------------------------- plumbing

    def _st(self, venue_key: str, symbol: str) -> _VenueState:
        return self._venues.setdefault((venue_key, symbol), _VenueState())

    def _slice(self, st: _VenueState) -> list:
        now = time.time()
        cutoff = now - self.window_hours * 3600
        if cutoff > 0:
            vals = [s for ts, s in st.samples if ts >= cutoff]
        else:
            vals = [s for _, s in st.samples]
        if len(vals) > self.window_n:
            vals = vals[-self.window_n:]
        return vals

    # -------------------------------------------------------------- observe

    def observe(self, venue_key: str, symbol: str, slip_bps: Optional[float],
                filled_qty: float, order_qty: float) -> None:
        """Record one leg settlement. slip_bps None when no avg_px (bias the
        miss pool); filled_qty vs order_qty classify full/partial/miss."""
        st = self._st(venue_key, symbol)
        if slip_bps is not None and filled_qty > 0:
            st.samples.append((time.time(), slip_bps))
        filled = filled_qty > 0
        st.fills.append((time.time(), filled))
        while st.fills and time.time() - st.fills[0][0] > MISS_WINDOW_SEC:
            st.fills.popleft()
        self._dirty += 1
        if self._dirty >= 20:
            self.save()

    @staticmethod
    def _windowed_fills(st: _VenueState) -> list:
        cutoff = time.time() - MISS_WINDOW_SEC
        return [f for ts, f in st.fills if ts >= cutoff]

    # -------------------------------------------------------------- queries

    def p50(self, venue_key: str, symbol: str) -> Optional[float]:
        return self._pctl_venue(venue_key, symbol, 50)

    def p90(self, venue_key: str, symbol: str) -> Optional[float]:
        return self._pctl_venue(venue_key, symbol, 90)

    def _pctl_venue(self, venue_key: str, symbol: str, q: float) -> Optional[float]:
        st = self._venues.get((venue_key, symbol))
        if st is None:
            return None
        vals = sorted(self._slice(st))
        if len(vals) < self.min_samples:
            return None
        return _pctl(vals, q)

    def gate_bps(self, buy_key: str, sell_key: str, symbol: str) -> float:
        """Expected whole-round-trip slippage to charge at OPEN time.
        Returns 0 until either leg has enough samples (cold-start fallback).
        Negatives are floored on this side so luck never subsidises pricing."""
        a = self.p50(buy_key, symbol)
        b = self.p50(sell_key, symbol)
        if a is None or b is None:
            return 0.0
        one_way = max(0.0, a) + max(0.0, b)
        return one_way * 2.0 * self.gate_weight

    def protect_bps(self, venue_key: str, symbol: str, fallback: float) -> float:
        """Per-leg price-protection width. Returns the static fallback until
        the venue has enough samples; then clamp(p90*mult, floor, cap). The
        cap defaults to the current static width so it can only TIGHTEN for
        the first week — loosening is a data-driven config decision, not
        self-served."""
        v = self.p90(venue_key, symbol)
        if v is None:
            return fallback
        slip = max(self.protect_floor_bps, v * self.protect_mult)
        return min(self.protect_cap_bps, slip)

    def miss_rate(self, venue_key: str, symbol: str) -> Optional[float]:
        """Fraction of recent settles (rolling 24h) that returned zero fill.
        None until enough events. A rising miss rate is the evidence a
        tightened protection is rejecting orders needlessly."""
        st = self._venues.get((venue_key, symbol))
        if st is None:
            return None
        fills = self._windowed_fills(st)
        if len(fills) < self.min_samples:
            return None
        return sum(1 for f in fills if not f) / len(fills)

    # ---------------------------------------------------------- persistence

    def save(self) -> None:
        payload = {"window_n": self.window_n, "window_hours": self.window_hours,
                   "venues": {}}
        for (vkey, sym), st in self._venues.items():
            payload["venues"][f"{vkey}|{sym}"] = {
                "samples": [[ts, s] for ts, s in st.samples],
                "fills": [[ts, f] for ts, f in st.fills],
            }
        try:
            d = os.path.dirname(self.state_file)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self.state_file + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self.state_file)
        except Exception as e:
            log.warning("[slip] state save failed: %r", e)
        self._dirty = 0

    def load(self) -> None:
        try:
            with open(self.state_file) as fh:
                payload = json.load(fh)
            for key, v in (payload.get("venues") or {}).items():
                vk, _, sym = key.rpartition("|")
                if not vk:
                    continue
                st = self._venues.setdefault((vk, sym), _VenueState())
                st.samples = deque((ts, float(s)) for ts, s in
                                   (v.get("samples") or []))
                st.fills = deque((ts, bool(f)) for ts, f in
                                 (v.get("fills") or []))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("[slip] state load failed, starting empty: %r", e)