"""Two-venue arbitrage engine: Entropy vs one hedge venue.

The signal is a fixed band around a configured midline (config.yaml):

    SELL entropy / BUY hedge  when executable premium >= midline + upper (+fees)
    BUY entropy / SELL hedge  when executable premium <= midline - lower (+fees)

Around the signal: per-direction persistence arming,
per-venue inventory ladder + position caps, per-venue order budgets and
reactive rate-limit exclusion, net-delta hedging, venue-outage pausing with
probing, and periodic on-chain reconciliation. There is no paper mode: the
bot either trades live or runs --record-only (data collection, no strategy).
Both venues' books are recorded to 1-minute CSV bars throughout.
"""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import time
from collections import deque
from typing import Dict, List, Optional

import aiohttp
from aiohttp import web

from .book import ArbPlan, floor_step, plan_arb
from .config import Config
from .notifier import Notifier
from .recorder import MinuteRecorder
from .slippage import SlipModel
from .venue_hl import HLVenue
from .venue_lighter import LighterVenue
from .webui import PAGE_HTML, status_payload

log = logging.getLogger("engine")

CSV_HEADER = ["ts", "direction", "buy_venue", "sell_venue", "qty",
              "buy_limit", "sell_limit", "buy_notional", "sell_notional",
              "exp_edge_usd", "gross_edge_usd", "marginal_premium_bps",
              "midline_bps", "inv_add_bps", "ok", "buy_fill", "sell_fill",
              "buy_status", "sell_status", "fill_edge_usd",
              "buy_lat_ms", "sell_lat_ms", "slip_buy_bps", "slip_sell_bps",
              "signal_age_sec", "dyn_protect_buy_bps", "dyn_protect_sell_bps"]
BALANCE_POLL_SEC = 30.0
FORCE_RECONCILE_GRACE_SEC = 3.0


def liq_distance(mark: float, liq: float) -> float:
    """Distance from mark to the liquidation price, as a fraction of mark
    (always positive: liq sits on the loss side of mark)."""
    return abs(mark - liq) / mark


class Engine:
    def __init__(self, cfg: Config, record_only: bool = False) -> None:
        self.cfg = cfg
        self.record_only = record_only
        self.session: Optional[aiohttp.ClientSession] = None
        self.entropy = None
        self.hedge = None
        self.venues: Dict[str, object] = {}
        self.recorder: Optional[MinuteRecorder] = None
        self.markets_ready = False
        self.stop = asyncio.Event()
        self._update_evt = asyncio.Event()
        self._reconcile_evt = asyncio.Event()
        # per-venue locks: an execution holds both; a reconcile holds one, so
        # a chain read can never race an in-flight order on that venue
        self._venue_locks: Dict[str, asyncio.Lock] = {}
        self._exec_tasks: set = set()
        self.halted = False
        self.consec_errors = 0
        self.last_trade_ts = 0.0
        self.trades = 0
        self.hedges = 0
        self.total_exp_edge = 0.0
        self.total_fill_edge = 0.0
        self.start_ts = time.time()
        self._last_skiplog = 0.0
        self._poke_due: Optional[float] = None
        # per-direction persistence arming: direction key -> first-seen ts
        self._armed: Dict[str, Optional[float]] = {"sell_entropy": None,
                                                   "buy_entropy": None}
        self._step = 1e-4
        self._min_base = 0.0
        self._min_notional = 10.0
        self._mtm_baseline: Optional[float] = None
        # proactive per-venue send budget: timestamps of recent order sends
        self._sends: Dict[str, deque] = {}
        # reactive per-venue throttle: venue key -> excluded until
        self._venue_limited_until: Dict[str, float] = {}
        # unresolved-outcome fuse: a leg whose fill state is unknown after the
        # settle window fuses its venue until reconcile confirms chain state
        self._venue_unresolved_until: Dict[str, float] = {}
        # venue outage tracking: key -> down-since ts; a down venue pauses
        # trading and is probed every venue_probe_sec until it answers
        self._venue_down: Dict[str, float] = {}
        self._venue_probe_at: Dict[str, float] = {}
        self._venue_fetch_fails: Dict[str, int] = {}
        # per-execution records for the dashboard (newest last)
        self.recent_trades: deque = deque(maxlen=50)
        # midline drift sentinel (P1-3): sampled premiums + halt flag
        self._premium_hist: deque = deque(maxlen=int(cfg.drift_window_sec) + 2)
        self._drift_started: Optional[float] = None
        self._last_drift_check = 0.0
        self._drift_halted = False
        self._drift_back_since: Optional[float] = None
        # telegram alerts (no-op without credentials)
        self.notifier = Notifier.from_env()
        # realized-slippage model (二期③); None when disabled
        self.slippage: Optional[SlipModel] = None
        if cfg.slippage.enabled:
            sc = cfg.slippage
            self.slippage = SlipModel(
                state_file=sc.state_file, window_n=sc.window_n,
                window_hours=sc.window_hours, min_samples=sc.min_samples,
                miss_threshold=sc.miss_threshold,
                protect_mult=sc.protect_mult,
                protect_floor_bps=sc.protect_floor_bps,
                protect_cap_bps=sc.protect_cap_bps,
                gate_weight=sc.gate_weight)
            log.info("[slip] slippage model enabled (%s)",
                     sc.state_file)

    # ------------------------------------------------------------- utilities

    def _vlock(self, key: str) -> asyncio.Lock:
        lock = self._venue_locks.get(key)
        if lock is None:
            lock = self._venue_locks[key] = asyncio.Lock()
        return lock

    def _venue_rate_ok(self, v) -> bool:
        """True while the venue is under its max_orders_per_min (sliding 60s)."""
        dq = self._sends.setdefault(v.key, deque())
        now = time.time()
        while dq and now - dq[0] > 60.0:
            dq.popleft()
        return len(dq) < v.orders_per_min

    def _venue_limited(self, v) -> bool:
        if time.time() < self._venue_limited_until.get(v.key, 0.0):
            return True
        return bool(self._venue_unresolved_until.get(v.key, 0.0))

    def _mark_limited(self, v) -> None:
        self._venue_limited_until[v.key] = time.time() + self.cfg.rate_limit_pause_sec
        log.warning("[%s] rate limited — trading paused for %.0fs",
                    v.name, self.cfg.rate_limit_pause_sec)

    async def _notify(self, text: str) -> None:
        if self.notifier.enabled:
            self.notifier.send(text)

    def _record_send(self, v) -> None:
        self._sends.setdefault(v.key, deque()).append(time.time())

    def request_stop(self) -> None:
        self.stop.set()
        self._update_evt.set()
        self._reconcile_evt.set()

    # ------------------------------------------------------------- lifecycle

    async def run(self) -> None:
        # Long keepalive so order-path connections survive quiet spells; the
        # keepalive loop pings inside this window to hold them open.
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(
            keepalive_timeout=75.0, ttl_dns_cache=300))
        try:
            await self._run_inner()
        finally:
            await self.session.close()

    def _make_venue(self, vc):
        if vc.kind == "lighter":
            return LighterVenue(vc, self.session, self.cfg.settle_timeout_sec)
        return HLVenue(vc, self.cfg.hl_api_url, self.cfg.hl_ws_url,
                       self.session, self.cfg.settle_timeout_sec)

    async def _run_inner(self) -> None:
        cfg = self.cfg
        self.entropy = self._make_venue(cfg.entropy)
        self.hedge = self._make_venue(cfg.hedge)
        self.venues = {"entropy": self.entropy, "hedge": self.hedge}
        await asyncio.gather(self.entropy.load_market(), self.hedge.load_market())
        self.markets_ready = True

        live = not self.record_only
        if live:
            if not cfg.creds_complete:
                raise RuntimeError(
                    "live trading needs credentials for both venues in .env "
                    "(see .env.example); use --record-only to run without "
                    "them / 实盘需要在 .env 中配置两个交易所的密钥，仅采集数据"
                    "请用 --record-only")
            self.entropy.init_signer()
            self.hedge.init_signer()
            if self.hedge.kind == "hl":
                self.entropy.share_nonces_with(self.hedge)
        if (self.hedge.kind == "hl"
                and self.entropy._query_address()
                and self.entropy._query_address() == self.hedge._query_address()):
            self.hedge.include_core_equity = False  # shared account: count once

        self._step = 10 ** -min(self.entropy.size_decimals,
                                self.hedge.size_decimals)
        self._min_base = max(self.entropy.min_base, self.hedge.min_base,
                             self._step)
        self._min_notional = max(cfg.min_order_notional,
                                 self.entropy.min_quote, self.hedge.min_quote)
        log.info("pair ENTROPY(%s)-%s(%s): midline=%+.2fbps band=[-%.2f, +%.2f] "
                 "fees=%.2f+%.2f step=%g min_ntl=$%g",
                 self.entropy.conf.symbol, self.hedge.name,
                 self.hedge.conf.symbol, cfg.midline_bps, cfg.lower_bps,
                 cfg.upper_bps, self.entropy.fee_bps, self.hedge.fee_bps,
                 self._step, self._min_notional)

        if self.record_only:
            log.warning("RECORD-ONLY — collecting minute data, no strategy, "
                        "no orders")
        else:
            log.warning("LIVE — real orders will be sent (use --record-only "
                        "for credential-less data collection)")
            await self._reconcile_positions(hedge=False, strict=True)
            log.info("starting positions: %s (net %+.6g)",
                     " ".join(f"{v.name}={v.position:+.6g}"
                              for v in self.venues.values()),
                     sum(v.position for v in self.venues.values()))

        tasks: List[asyncio.Task] = []
        for v in self.venues.values():
            tasks += v.start_tasks(self.stop, self._update_evt.set, live,
                                   data_staleness_sec=cfg.data_staleness_sec)
        if cfg.recorder_enabled or self.record_only:
            self.recorder = MinuteRecorder(cfg.recorder_csv, self.entropy.book,
                                           self.hedge.book, cfg.staleness_sec,
                                           data_staleness_sec=cfg.data_staleness_sec)
            tasks.append(asyncio.create_task(self.recorder.run(self.stop),
                                             name="recorder"))
        if not self.record_only:
            tasks.append(asyncio.create_task(self._strategy_loop(),
                                             name="strategy"))
            tasks.append(asyncio.create_task(self._balance_loop(),
                                             name="balances"))
            tasks.append(asyncio.create_task(self._http_keepalive_loop(),
                                             name="keepalive"))
            tasks.append(asyncio.create_task(self._risk_loop(), name="risk"))
            tasks.append(asyncio.create_task(self._drift_loop(), name="drift"))
        if self.notifier.enabled:
            tasks.append(asyncio.create_task(self.notifier.run(self.session),
                                             name="notify"))
        if cfg.web_dashboard_enabled:
            tasks.append(asyncio.create_task(self._web_loop(),
                                             name="webdash"))
        tasks.append(asyncio.create_task(self._status_loop(), name="status"))
        if live:
            tasks.append(asyncio.create_task(self._reconcile_loop(),
                                             name="reconcile"))

        await self.stop.wait()
        if self._exec_tasks:  # let in-flight executions settle, never cancel
            log.info("waiting for %d in-flight execution(s) to settle",
                     len(self._exec_tasks))
            # worst-case leg now runs settle_timeout + 3×(REST query + 1s);
            # keep the window wide enough to never abandon an unresolved leg
            await asyncio.wait(self._exec_tasks,
                               timeout=cfg.settle_timeout_sec + 8.0)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.notifier.enabled:
            await self.notifier.close()
        if self.slippage is not None:
            self.slippage.save()
        for v in self.venues.values():
            await v.close()
        log.info("shutdown — %d trades, %d hedges, exp edge $%.4f, "
                 "fill edge $%.4f", self.trades, self.hedges,
                 self.total_exp_edge, self.total_fill_edge)

    # --------------------------------------------------------------- signals

    def _inv_add_bps(self, buy, sell) -> float:
        """Inventory ladder: a surcharge that grows once a venue's position
        passes floor_frac of its cap in the direction the trade would add to
        (buying adds when that venue is >= flat long; selling adds when the
        venue is <= flat short). Max of the two venues' ramps."""
        scale = self.cfg.inventory_scale_bps
        if scale <= 0:
            return 0.0
        floor = min(max(self.cfg.inventory_floor_frac, 0.0), 0.99)

        def ramp(v, adding: bool) -> float:
            if not adding:
                return 0.0
            ref = v.book.mid()
            if ref is None:
                return 0.0
            u = min(abs(v.position) * ref / v.cap_usd, 1.0)
            if u <= floor:
                return 0.0
            return scale * (u - floor) / (1.0 - floor)

        return max(ramp(buy, buy.position >= 0), ramp(sell, sell.position <= 0))

    def _eff_threshold(self, buy, sell) -> float:
        """Net hurdle (bps, on top of fees) for the direction buy->sell.

        selling entropy: executable premium must clear midline + upper;
        buying entropy: the reverse premium must clear lower - midline.

        Funding cost and expected slippage are folded into the hurdle for
        OPENING directions only — a reducing trade is never priced out."""
        parts = self._hurdle_breakdown(buy, sell)
        return parts["base"] + parts["inventory"] + parts["funding"] + \
            parts["slip_gate"]

    def _hurdle_breakdown(self, buy, sell) -> Dict[str, float]:
        """Decompose the net hurdle into its parts (for webui/dashboard)."""
        cfg = self.cfg
        if sell.key == "entropy":
            base = cfg.midline_bps + cfg.upper_bps
        else:
            base = cfg.lower_bps - cfg.midline_bps
        inv = self._inv_add_bps(buy, sell)
        funding = slip_gate = 0.0
        if not self._direction_reduces(buy, sell):
            funding = min(self._funding_cost_bps(buy, sell) * 0.5,
                          cfg.funding_cap_bps)
            if self.slippage is not None:
                slip_gate = self.slippage.gate_bps(buy.key, sell.key,
                                                   cfg.symbol)
        return {"base": base, "inventory": inv, "funding": funding,
                "slip_gate": slip_gate}

    def _direction_reduces(self, buy, sell) -> bool:
        """True when this direction reduces the existing pair inventory
        (closing an entropy or hedge position toward zero)."""
        if sell.key == "entropy":
            return self.entropy.position > 0 or self.hedge.position < 0
        return self.entropy.position < 0 or self.hedge.position > 0

    def _funding_cost_bps(self, buy, sell) -> float:
        """Expected funding cost in bps of holding this direction for
        funding_hold_hours (long the buy leg, short the sell leg). Venues
        report bps/hour; positive funding = longs pay shorts. Only the
        adverse side counts; unknown rates contribute 0."""
        cost_h = 0.0
        for v, is_long in ((buy, True), (sell, False)):
            f = getattr(v, "funding_bps_h", None)
            if f is None:
                continue
            if is_long and f > 0:
                cost_h += f
            elif not is_long and f < 0:
                cost_h += -f
        return cost_h * max(self.cfg.funding_hold_hours, 0.0)

    def _headroom(self, buy, sell, ref_px: float) -> float:
        hb = buy.cap_usd - buy.position * ref_px
        hs = sell.cap_usd + sell.position * ref_px
        return min(hb, hs)

    def _plan(self, buy, sell, cap_notional: float):
        return plan_arb(
            buy.book, sell.book,
            threshold_bps=self._eff_threshold(buy, sell),
            buy_fee_bps=buy.fee_bps, sell_fee_bps=sell.fee_bps,
            take_fraction=self.cfg.take_fraction,
            cap_notional=cap_notional,
            min_base=self._min_base,
            min_notional=self._min_notional,
            size_step=self._step,
        )

    # -------------------------------------------------------------- strategy

    async def _strategy_loop(self) -> None:
        while not self.stop.is_set():
            await self._update_evt.wait()
            self._update_evt.clear()
            if self.stop.is_set():
                break
            try:
                await self._evaluate()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("evaluate failed")

    def _schedule_poke(self, delay: float) -> None:
        loop = asyncio.get_running_loop()
        due = loop.time() + max(delay, 0.01)
        if self._poke_due is not None and self._poke_due <= due + 0.02:
            return

        def _fire() -> None:
            self._poke_due = None
            self._update_evt.set()

        self._poke_due = due
        loop.call_at(due, _fire)

    def _skiplog(self, fmt: str, *args) -> None:
        now = time.time()
        if now - self._last_skiplog >= 2.0:
            self._last_skiplog = now
            log.info(fmt, *args)

    async def _evaluate(self) -> None:
        cfg = self.cfg
        if self.halted:
            return
        now = time.time()
        if now - self.last_trade_ts < cfg.cooldown_sec:
            self._schedule_poke(cfg.cooldown_sec - (now - self.last_trade_ts))
            return
        best = self._scan(now)
        if best is None:
            return
        buy, sell, plan, armed_ts = best
        # _scan verified both locks free and nothing ran since (no awaits),
        # so these acquires take the no-suspension fast path
        await self._vlock(buy.key).acquire()
        await self._vlock(sell.key).acquire()
        # run as a task so a shutdown cancels the strategy loop's await, never
        # the in-flight execution itself (both legs must settle)
        t = asyncio.create_task(self._execute_locked(buy, sell, plan, armed_ts))
        self._exec_tasks.add(t)
        t.add_done_callback(self._exec_tasks.discard)
        await asyncio.shield(t)

    async def _execute_locked(self, buy, sell, plan: ArbPlan,
                              armed_ts: Optional[float]) -> None:
        """Run one execution while holding both venue locks (acquired by the
        caller), then release them and settle the aftermath: unresolved
        outcomes escalate to reconcile, everything else gets a net-delta
        check."""
        unresolved = False
        try:
            unresolved = await self._execute(buy, sell, plan, armed_ts)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("execute failed")
        finally:
            self._vlock(buy.key).release()
            self._vlock(sell.key).release()
        if unresolved:
            self._reconcile_evt.set()
        else:
            await self._maybe_hedge()
        self._update_evt.set()  # freed venues may have a queued opportunity

    def _scan(self, now: float):
        """Evaluate both directions; returns the best executable
        (buy, sell, plan), or None."""
        cfg = self.cfg
        best = None
        for buy, sell, dkey in ((self.hedge, self.entropy, "sell_entropy"),
                                (self.entropy, self.hedge, "buy_entropy")):
            plan_cap = cfg.max_order_notional
            if self._drift_halted:
                # drift sentinel fired: only position-REDUCING directions may
                # trade (sell_entropy closes an entropy long, buy_entropy
                # closes an entropy short); flat = fully paused
                if (dkey == "sell_entropy" and self.entropy.position <= 0) \
                        or (dkey == "buy_entropy" and self.entropy.position >= 0):
                    continue
                # size clamp: never let a "reduce" slice cross through zero
                # into a fresh position while the center is untrustworthy.
                # cap_notional applies to the BUY leg, so reference its ask:
                # qty = cap / buy_ask <= |position| holds exactly.
                m = buy.book.best_ask()
                if m:
                    plan_cap = min(plan_cap,
                                   abs(self.entropy.position) * m)
            if not (buy.book.is_fresh(cfg.staleness_sec,
                                      cfg.data_staleness_sec)
                    and sell.book.is_fresh(cfg.staleness_sec,
                                           cfg.data_staleness_sec)):
                self._armed[dkey] = None  # world untrustworthy: re-arm fresh
                continue
            if not (buy.ready_to_trade() and sell.ready_to_trade()):
                self._armed[dkey] = None  # settlement channel down: re-arm
                continue
            if self._venue_down:
                self._armed[dkey] = None  # outage: re-arm on recovery
                continue  # a venue in outage pauses the (only) pair
            if self._vlock(buy.key).locked() or self._vlock(sell.key).locked():
                continue  # mid-execution or mid-reconcile: signal real, keep
            if self._venue_limited(buy) or self._venue_limited(sell):
                self._armed[dkey] = None  # excluded: re-arm when un-fused
                continue  # reactive 429 exclusion / unresolved fuse
            if not (self._venue_rate_ok(buy) and self._venue_rate_ok(sell)):
                self._skiplog("%s deferred: venue order budget exhausted", dkey)
                continue  # signal real, just throttled: keep armed
            # never refire into books that predate the venue's own last trade
            if (buy.book.last_update_ts <= buy.last_traded_ts
                    or sell.book.last_update_ts <= sell.last_traded_ts):
                continue
            plan, reason = self._plan(buy, sell, plan_cap)
            edge_present = reason not in ("no_edge", "empty_book")
            if not edge_present:
                self._armed[dkey] = None
                continue
            armed = self._armed.get(dkey)
            if armed is None:
                # premium persistence: only fire if the edge survives
                # premium_persist_sec (filters one-tick phantoms)
                self._armed[dkey] = now
                self._schedule_poke(cfg.premium_persist_sec)
                continue
            if now - armed < cfg.premium_persist_sec:
                self._schedule_poke(cfg.premium_persist_sec - (now - armed))
                continue
            if plan is None:
                continue
            headroom = self._headroom(buy, sell, plan.buy_limit)
            if headroom < plan.buy_notional:
                plan, _ = self._plan(buy, sell,
                                     min(plan_cap, headroom))
                if plan is None:
                    self._skiplog("%s blocked by position caps (headroom $%.0f)",
                                  dkey, max(headroom, 0.0))
                    continue
            # margin pre-check: skip before any order hits the exchange —
            # do not rely on margin rejections to pause the venue
            m_buy = self._margin_ok(buy, plan.buy_notional)
            m_sell = self._margin_ok(sell, plan.sell_notional)
            if not (m_buy and m_sell):
                detail = " | ".join(
                    f"{v.name} free=${(v.free if v.free is not None else float('nan')):.2f}"
                    f" need=${self._margin_need(n):.2f}"
                    for v, n, ok in ((buy, plan.buy_notional, m_buy),
                                     (sell, plan.sell_notional, m_sell))
                    if not ok)
                self._skiplog("%s skipped: free margin insufficient — %s",
                              dkey, detail)
                continue
            if best is None or plan.exp_edge_usd > best[2].exp_edge_usd:
                # carry THIS round's arming timestamp so _execute can measure
                # arm->send for this order (the global _armed value would
                # include time spent in previous trades on a persistent edge)
                best = (buy, sell, plan, self._armed.get(dkey))
        return best

    def _margin_need(self, notional: float) -> float:
        """Margin an order actually consumes at the configured leverage,
        times the reserve factor (1x leverage assumed unless config says
        otherwise — a 10x account only ties up notional/10, and a 1x
        assumption would block it even with plenty of buying power)."""
        lev = max(self.cfg.margin_leverage, 1.0)
        return (notional / lev) * self.cfg.margin_reserve_factor

    def _margin_ok(self, v, notional: float) -> bool:
        """True when the venue's available balance covers the margin the
        order needs (_margin_need). Unknown balance (poll not ready) does
        not block."""
        free = getattr(v, "free", None)
        if free is None:
            return True
        return free >= self._margin_need(notional)

    # ------------------------------------------------------------- execution

    async def _execute(self, buy, sell, plan: ArbPlan,
                       armed_ts: Optional[float] = None) -> bool:
        """Send both legs and settle the fills. Both venue locks are held by
        the caller. Returns True when an outcome is unresolved and the caller
        must escalate to reconcile."""
        if self.halted:
            return False
        cfg = self.cfg
        inv_bps = self._inv_add_bps(buy, sell)
        direction = "sell_entropy" if sell.key == "entropy" else "buy_entropy"
        self.last_trade_ts = time.time()
        log.info("[ARB] %s: BUY %s %.6g @<=%.6g | SELL %s @>=%.6g | "
                 "take $%.0f of $%.0f | prem %.2fbps | exp $%.4f",
                 direction, buy.name, plan.qty, plan.buy_limit, sell.name,
                 plan.sell_limit, plan.buy_notional, plan.q_max_notional,
                 plan.marginal_premium_bps, plan.exp_edge_usd)
        # per-leg adaptive protection (二期③): each leg uses its own realized
        # p90 once the model has enough samples, else the static width. The
        # dynamic values are recorded as shadow columns for later inspection.
        if self.slippage is not None:
            dyn_buy = self.slippage.protect_bps(buy.key, cfg.symbol,
                                                cfg.leg_slippage_bps)
            dyn_sell = self.slippage.protect_bps(sell.key, cfg.symbol,
                                                 cfg.leg_slippage_bps)
        else:
            dyn_buy = dyn_sell = cfg.leg_slippage_bps
        buy_bound = buy.px_round(plan.buy_limit * (1 + dyn_buy / 1e4),
                                 round_up=False)
        sell_bound = sell.px_round(plan.sell_limit * (1 - dyn_sell / 1e4),
                                   round_up=True)
        self._record_send(buy)
        self._record_send(sell)
        # this order's arm->send age (armed_ts carried by _scan, so repeated
        # fires on a persistent edge each report their own age, not the
        # edge's total lifetime)
        signal_age = time.time() - (armed_ts or time.time())

        async def _timed(coro):
            t0 = time.perf_counter()
            r = await coro
            if isinstance(r, dict):
                r["latency_ms"] = (time.perf_counter() - t0) * 1e3
            return r

        res = await asyncio.gather(
            _timed(buy.send_taker(is_buy=True, qty=plan.qty,
                                  limit_px=buy_bound)),
            _timed(sell.send_taker(is_buy=False, qty=plan.qty,
                                   limit_px=sell_bound)),
            return_exceptions=True)
        binfo, sinfo = (r if isinstance(r, dict) else
                        {"status": "send-failed", "filled_base": 0.0,
                         "avg_px": None, "err": repr(r), "unresolved": False}
                        for r in res)
        for v, info, side in ((buy, binfo, "buy"), (sell, sinfo, "sell")):
            if info.get("err"):
                log.error("[%s] %s leg: %s", v.name, side, info["err"])
        bfill = binfo["filled_base"]
        sfill = sinfo["filled_base"]
        buy.position += bfill
        sell.position -= sfill
        if bfill:
            bpx = binfo.get("avg_px") or plan.buy_limit
            buy.cash -= bfill * bpx * (1 + plan.buy_fee)
            buy.volume_usd += bfill * bpx
        if sfill:
            spx = sinfo.get("avg_px") or plan.sell_limit
            sell.cash += sfill * spx * (1 - plan.sell_fee)
            sell.volume_usd += sfill * spx

        matched = min(bfill, sfill)
        fill_edge = 0.0
        if matched > 0 and binfo.get("avg_px") and sinfo.get("avg_px"):
            fill_edge = matched * (sinfo["avg_px"] * (1 - plan.sell_fee)
                                   - binfo["avg_px"] * (1 + plan.buy_fee))
            self.total_fill_edge += fill_edge
        log.info("[SETTLED] %s: buy %s %s %.6g/%.6g | sell %s %s %.6g/%.6g | "
                 "matched %.6g | fill edge $%.4f", direction,
                 buy.name, binfo["status"], bfill, plan.qty,
                 sell.name, sinfo["status"], sfill, plan.qty, matched, fill_edge)
        buy.last_traded_ts = sell.last_traded_ts = time.time()

        unresolved = binfo.get("unresolved") or sinfo.get("unresolved")
        for v, info in ((buy, binfo), (sell, sinfo)):
            if info.get("unresolved"):
                # fuse the venue: no new orders until reconcile confirms the
                # on-chain position (see _reconcile_venue)
                self._venue_unresolved_until[v.key] = float("inf")
                log.critical("[%s] unresolved leg outcome — venue fused "
                             "until reconcile confirms chain state", v.name)
                await self._notify(f"⚠️ [{v.name}] 订单状态未确认，"
                                   f"已熔断待对账")
        hard_err = (binfo.get("err") is not None
                    or sinfo.get("err") is not None)
        rate_limited = False
        for v, info in ((buy, binfo), (sell, sinfo)):
            if str(info.get("err", "")).startswith("RATE_LIMITED"):
                rate_limited = True
                self._mark_limited(v)
            elif "margin" in str(info.get("status", "")).lower():
                log.warning("[%s] margin rejection — collateral exhausted, "
                            "pausing venue", v.name)
                self._mark_limited(v)
        sent_ok = not hard_err and not unresolved
        if sent_ok:
            self.consec_errors = 0
        elif not rate_limited:
            self.consec_errors += 1
            if self.consec_errors >= cfg.max_consecutive_errors:
                self.halted = True
                log.critical("HALTED after %d consecutive execution problems "
                             "— flatten manually and restart / 连续执行异常，"
                             "引擎已停止，请手动平仓后重启", self.consec_errors)
                await self._notify("🛑 引擎连续执行异常已停机（HALTED），"
                                   "请人工检查平仓")
        if sent_ok:
            self.trades += 1
            self.total_exp_edge += plan.exp_edge_usd
        # per-leg realized slippage vs the plan's walk-depth limits — only
        # when the exchange reported an avg_px (never backfill: that would
        # pollute the calibration sample)
        slip_buy_bps = ((binfo["avg_px"] / plan.buy_limit - 1.0) * 1e4
                        if binfo.get("avg_px") else None)
        slip_sell_bps = ((1.0 - sinfo["avg_px"] / plan.sell_limit) * 1e4
                         if sinfo.get("avg_px") else None)
        if self.slippage is not None:
            # feed the model (slip is None on misses, which still count in
            # the miss-rate pool — that is exactly the survivor-bias guard)
            self.slippage.observe(buy.key, cfg.symbol, slip_buy_bps,
                                  bfill, plan.qty)
            self.slippage.observe(sell.key, cfg.symbol, slip_sell_bps,
                                  sfill, plan.qty)
        self._record_trade(direction, plan,
                           None if unresolved else fill_edge,
                           f"{binfo['status']}/{sinfo['status']}", sent_ok)
        self._log_csv(direction, buy, sell, plan, sent_ok, bfill, sfill,
                      binfo["status"], sinfo["status"], fill_edge, inv_bps,
                      binfo.get("latency_ms"), sinfo.get("latency_ms"),
                      slip_buy_bps, slip_sell_bps, signal_age,
                      dyn_buy, dyn_sell)
        self.last_trade_ts = time.time()
        return bool(unresolved)

    def _record_trade(self, direction: str, plan: ArbPlan, fill_edge,
                      status: str, ok: bool) -> None:
        self.recent_trades.append({
            "ts": time.time(), "direction": direction, "qty": plan.qty,
            "notional": plan.buy_notional,
            "prem_bps": plan.marginal_premium_bps,
            "exp": plan.exp_edge_usd, "fill": fill_edge, "status": status,
            "ok": ok})

    async def _maybe_hedge(self) -> None:
        net = sum(v.position for v in self.venues.values())
        if abs(net) > self.cfg.net_tolerance_base:
            await self._hedge()

    async def _hedge(self) -> None:
        """Reduce the venue carrying the imbalance back toward net zero with
        BOUNDED exposure: retry with widening slippage on a short deadline,
        then force-close at market; if the residual still cannot be
        flattened, halt + alert. The tail loss of a one-legged fill is
        capped by hedge_force_close_timeout_sec instead of being "unknown"
        until the next reconcile cycle."""
        cfg = self.cfg
        slips = cfg.hedge_retry_slips_bps or (cfg.hedge_slippage_bps,)
        deadline = time.time() + cfg.hedge_force_close_timeout_sec
        attempt = 0
        while True:
            slip_bps = max(cfg.hedge_slippage_bps,
                           slips[min(attempt, len(slips) - 1)])
            attempted = await self._hedge_once(slip_bps / 1e4)
            net = sum(v.position for v in self.venues.values())
            if abs(net) <= cfg.net_tolerance_base:
                return  # flat again
            if not attempted or time.time() >= deadline:
                break
            attempt += 1
            log.warning("[HEDGE] exposure %+.6g not reduced (attempt %d, "
                        "slip %.0f bps) — retrying", net, attempt, slip_bps)
            await asyncio.sleep(cfg.hedge_retry_interval_sec)
        # deadline hit or no venue to try: one last force-close attempt with
        # the WIDEST protection in the sequence (a zero-slip limit pins the
        # order to the touch — the least likely market order to fill, the
        # opposite of what the fallback needs). Real fills still happen at
        # book levels, the wide limit only sets the worst accepted price.
        await self._hedge_once(cfg.hedge_force_close_slip_bps / 1e4)
        residual = sum(v.position for v in self.venues.values())
        if abs(residual) <= cfg.net_tolerance_base:
            return
        if abs(residual) < self._min_hedgeable(1.0 if residual > 0 else -1.0):
            log.warning("[HEDGE] net %+.6g below hedgeable minimum — "
                        "carrying (next reconcile retries)", residual)
            return
        self.halted = True
        log.critical("HALTED — net exposure %+.6g could not be flattened "
                     "within %.0fs; manual action required / 净敞口无法在 "
                     "%.0fs 内削平，引擎已停机，请人工处理", residual,
                     cfg.hedge_force_close_timeout_sec,
                     cfg.hedge_force_close_timeout_sec)
        await self._notify(f"🚨 净敞口 {residual:+.6g} 未能削平，"
                           f"引擎已停机，请人工处理")

    def _min_hedgeable(self, sgn: float) -> float:
        """Smallest qty (base units) a carrying venue can actually hedge —
        below this, the residual is dust that must be carried until it grows
        or reconcile catches it."""
        cfg = self.cfg
        best = 0.0
        for v in self.venues.values():
            if v.position * sgn <= 0:
                continue
            ref = v.book.best_bid() if sgn > 0 else v.book.best_ask()
            if ref is None or ref <= 0:
                continue
            best = max(best, v.min_base,
                       max(cfg.min_order_notional, v.min_quote) / ref)
        return best

    async def _hedge_once(self, slip: float) -> bool:
        """Single reduce-only attempt on the venue carrying the imbalance
        (net recomputed from current positions). Returns True when an order
        was actually sent (regardless of fill); False when no venue was
        usable (blind/down/fused/locked/dust)."""
        cfg = self.cfg
        net = sum(v.position for v in self.venues.values())
        if abs(net) <= cfg.net_tolerance_base:
            return False
        is_sell = net > 0
        sgn = 1.0 if net > 0 else -1.0
        for v in sorted(self.venues.values(),
                        key=lambda x: (self._venue_limited(x), -x.position * sgn)):
            if v.position * sgn <= 0:
                continue
            if v.key in self._venue_down \
                    or not v.book.is_fresh(cfg.staleness_sec,
                                           cfg.data_staleness_sec):
                continue  # unreachable or blind: cannot hedge here
            if v.key in self._venue_unresolved_until:
                continue  # fill state unknown: hedging off a guessed position
            lk = self._vlock(v.key)
            if lk.locked():
                continue
            qty = floor_step(min(abs(net), abs(v.position)), self._step)
            if qty < v.min_base:
                continue
            ref = v.book.best_bid() if is_sell else v.book.best_ask()
            if ref is None:
                continue
            limit = v.px_round(ref * (1 - slip), False) if is_sell \
                else v.px_round(ref * (1 + slip), True)
            if qty * limit < max(cfg.min_order_notional, v.min_quote):
                continue
            await lk.acquire()  # verified free, no awaits since: fast path
            try:
                log.warning("[HEDGE] net %+.6g — %s %.6g on %s @%.6g",
                            net, "SELL" if is_sell else "BUY", qty, v.name, limit)
                self.hedges += 1
                self._record_send(v)  # counts toward the budget, never blocked
                info = await v.send_taker(is_buy=not is_sell, qty=qty,
                                          limit_px=limit, reduce_only=True)
                if info.get("err") or info.get("unresolved"):
                    log.error("[HEDGE] %s: %s", v.name,
                              info.get("err") or "unresolved")
                    await self._notify(f"⚠️ [{v.name}] 对冲失败："
                                       f"{info.get('err') or 'unresolved'}")
                    if str(info.get("err", "")).startswith("RATE_LIMITED"):
                        self._mark_limited(v)
                    elif info.get("unresolved"):
                        # same treatment as a taker leg: unknown fill fuses
                        # the venue until reconcile confirms chain state
                        self._venue_unresolved_until[v.key] = float("inf")
                        log.critical("[%s] unresolved hedge outcome — venue "
                                     "fused until reconcile confirms", v.name)
                    self._reconcile_evt.set()
                else:
                    fill = info["filled_base"]
                    v.position += -fill if is_sell else fill
                    if fill:
                        px = info.get("avg_px") or limit
                        fee = v.fee_bps / 1e4
                        v.cash += fill * px * (1 - fee) if is_sell \
                            else -fill * px * (1 + fee)
                        v.volume_usd += fill * px
                    log.info("[HEDGE SETTLED] %s %s %.6g/%.6g",
                             v.name, info["status"], fill, qty)
                v.last_traded_ts = time.time()
            finally:
                lk.release()
            return True
        return False

    # --------------------------------------------------- reconcile / status

    # Lighter's REST account state lags its ws settlements; overwriting a
    # venue that traded seconds ago "restores" stale positions and triggers
    # phantom hedge oscillations. Grace-guard + venue lock prevent that.
    RECONCILE_GRACE_SEC = 5.0

    async def _reconcile_positions(self, hedge: bool, strict: bool = False,
                                   force_keys: Optional[set] = None) -> None:
        now = time.time()
        vs = []
        for v in self.venues.values():
            force = bool(force_keys and v.key in force_keys)
            grace = (FORCE_RECONCILE_GRACE_SEC if force
                     else self.RECONCILE_GRACE_SEC)
            if now - v.last_traded_ts <= grace:
                continue  # just traded: chain read would be stale
            if v.key in self._venue_down \
                    and now < self._venue_probe_at.get(v.key, 0.0):
                continue  # down venue: probe only every venue_probe_sec
            vs.append((v, force))
        if not vs:
            return
        got = await asyncio.gather(
            *(self._reconcile_venue(v, strict, force) for v, force in vs),
            return_exceptions=True)
        for r in got:
            if isinstance(r, BaseException):
                raise r  # strict startup: fail loudly
        if hedge:
            await self._maybe_hedge()

    async def _reconcile_venue(self, v, strict: bool,
                               force: bool = False) -> None:
        async with self._vlock(v.key):
            now = time.time()
            grace = (FORCE_RECONCILE_GRACE_SEC if force
                     else self.RECONCILE_GRACE_SEC)
            if now - v.last_traded_ts <= grace:
                return  # traded while waiting for the lock
            # force (unresolved) reconciles must not un-fuse on a single
            # read: Lighter's REST account state lags its ws settlements, so
            # a read taken right after the fill may return the PRE-fill
            # position. Require two consistent reads ~1s apart (the old
            # position_sync_confirmations=2 recipe) and retry a few rounds;
            # while unconfirmed the venue stays fused — it cannot trade
            # anyway, so waiting is free.
            r = None
            err: Optional[Exception] = None
            if force:
                for _ in range(3):
                    a = b = None
                    try:
                        a = await v.fetch_position(force=True)
                        await asyncio.sleep(1.0)
                        b = await v.fetch_position(force=True)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        err = e
                        await asyncio.sleep(1.0)
                        continue
                    if abs(a - b) <= 1e-12:
                        r = b
                        break
                    err = None  # reads differ: REST catching up, try again
                    await asyncio.sleep(1.0)
            else:
                try:
                    r = await v.fetch_position()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    err = e
            if r is None:
                if err is None:
                    # the reads kept differing (REST catching up to a recent
                    # fill): not an API failure — stay fused, no venue-down
                    # penalty, and retry on the next reconcile cycle
                    log.warning("[%s] force reconcile reads inconsistent — "
                                "staying fused, retrying next cycle", v.name)
                    return
                if strict:
                    raise RuntimeError(
                        f"[{v.name}] cannot fetch starting position: {err!r}")
                # exchange unreachable (e.g. scheduled maintenance): pause
                # trading and keep probing until it answers again
                n = self._venue_fetch_fails.get(v.key, 0) + 1
                self._venue_fetch_fails[v.key] = n
                self._venue_probe_at[v.key] = now + self.cfg.venue_probe_sec
                if n >= 3 and v.key not in self._venue_down:
                    self._venue_down[v.key] = now
                    log.critical("[%s] API unreachable (%d attempts) — "
                                 "trading PAUSED; probing every %.0fs until "
                                 "it recovers", v.name, n,
                                 self.cfg.venue_probe_sec)
                    await self._notify(f"🚫 [{v.name}] API 不可达，"
                                       f"交易已暂停")
                elif v.key not in self._venue_down:
                    log.warning("[%s] position fetch failed (%d): %r",
                                v.name, n, err)
                return
            if v.key in self._venue_down:
                log.warning("[%s] API recovered after %.0fs outage — "
                            "trading RESUMED", v.name,
                            now - self._venue_down.pop(v.key))
                self._update_evt.set()
            self._venue_fetch_fails[v.key] = 0
            if v.key in self._venue_unresolved_until:
                # chain state confirmed: the unresolved leg is now known —
                # lift the fuse and let the venue trade again
                del self._venue_unresolved_until[v.key]
                log.warning("[%s] chain state confirmed — unresolved fuse "
                            "cleared, venue un-fused", v.name)
                await self._notify(f"✅ [{v.name}] 对账确认链上仓位，"
                                   f"熔断已解除")
                self._update_evt.set()
            delta = r - v.position
            if abs(delta) > 1e-12:
                if abs(delta) > self.cfg.net_tolerance_base:
                    log.warning("[%s] reconcile: chain %+.6g vs local %+.6g "
                                "— adopting chain", v.name, r, v.position)
                mid = v.book.mid()
                if mid is not None:
                    v.cash -= delta * mid
                v.position = r

    async def _reconcile_loop(self) -> None:
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self._reconcile_evt.wait(),
                                       timeout=self.cfg.reconcile_sec)
                self._reconcile_evt.clear()
                await asyncio.sleep(1.0)
            except asyncio.TimeoutError:
                pass
            if self.stop.is_set():
                break
            try:
                await self._reconcile_positions(
                    hedge=True,
                    force_keys={k for k, ts in self._venue_unresolved_until.items()
                                if ts})
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("reconcile failed")

    # -------------------------------------------------------- risk / drift

    async def _risk_loop(self) -> None:
        """Liquidation-risk watch: mark vs liquidation distance per venue,
        flatten + halt when a position gets too close."""
        cfg = self.cfg
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(),
                                       timeout=cfg.risk_loop_sec)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self._check_liquidation()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("risk check failed")

    async def _check_liquidation(self) -> None:
        cfg = self.cfg
        threshold = cfg.liquidation_distance_pct / 100.0
        for v in self.venues.values():
            try:
                r = await v.fetch_risk()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("[%s] risk fetch failed: %r", v.name, e)
                continue
            if r is None:
                continue
            mark, liq = r
            if mark <= 0 or liq <= 0:
                continue
            dist = liq_distance(mark, liq)
            if dist <= threshold:
                # halt FIRST: once the risk path commits to flattening, no
                # new signal may reopen the position we are about to close
                self.halted = True
                log.critical("[%s] LIQUIDATION RISK — mark %.6g vs liq %.6g "
                             "(%.2f%% away); halting and flattening",
                             v.name, mark, liq, dist * 100.0)
                await self._notify(
                    f"🚨 [{v.name}] 强平风险：mark {mark:.4g}，距强平价仅 "
                    f"{dist * 100:.1f}%——正在停机并平仓")
                await self._flatten_all()
                log.critical("HALTED by liquidation risk — flatten done; "
                             "restart after manual review / 强平风险停机，"
                             "已平仓，请人工检查后重启")
                await self._notify("🛑 引擎已因强平风险停机")
                return
            if dist <= threshold * 2:
                log.warning("[%s] liquidation distance %.1f%% (warning zone)",
                            v.name, dist * 100.0)

    async def _flatten_all(self) -> None:
        """Reduce-only flatten both venues to zero (liquidation-risk path)."""
        cfg = self.cfg
        slip = cfg.hedge_slippage_bps / 1e4
        for v in sorted(self.venues.values(), key=lambda x: -abs(x.position)):
            if v.position == 0.0:
                continue
            if v.key in self._venue_down or not v.book.is_fresh(
                    cfg.staleness_sec, cfg.data_staleness_sec):
                log.critical("[%s] cannot flatten — venue unreachable or "
                             "blind; manual action required", v.name)
                continue
            lk = self._vlock(v.key)
            async with lk:
                qty = floor_step(abs(v.position), self._step)
                if qty < v.min_base:
                    continue
                is_sell = v.position > 0
                ref = v.book.best_bid() if is_sell else v.book.best_ask()
                if ref is None:
                    log.critical("[%s] no book to flatten against — manual "
                                 "action required", v.name)
                    continue
                limit = v.px_round(ref * (1 - slip), False) if is_sell \
                    else v.px_round(ref * (1 + slip), True)
                self._record_send(v)
                info = await v.send_taker(is_buy=not is_sell, qty=qty,
                                          limit_px=limit, reduce_only=True)
                if info.get("err") or info.get("unresolved"):
                    log.critical("[%s] flatten order failed: %s — manual "
                                 "action required", v.name,
                                 info.get("err") or "unresolved")
                    await self._notify(f"🚨 [{v.name}] 平仓失败："
                                       f"{info.get('err') or 'unresolved'}，"
                                       f"请人工处理")
                else:
                    fill = info["filled_base"]
                    v.position += -fill if is_sell else fill
                    if fill:
                        px = info.get("avg_px") or limit
                        fee = v.fee_bps / 1e4
                        v.cash += fill * px * (1 - fee) if is_sell \
                            else -fill * px * (1 + fee)
                        v.volume_usd += fill * px
                    log.warning("[FLATTEN] %s %s %.6g/%.6g", v.name,
                                "SELL" if is_sell else "BUY", fill, qty)
                v.last_traded_ts = time.time()

    async def _drift_loop(self) -> None:
        """Midline drift sentinel: sample the premium 1/sec, every
        drift_check_sec compare the trailing-window mean against the midline;
        a sustained breach for drift_halt_sec halts opening trades (only
        position-reducing directions keep trading) with a critical alert.
        The midline itself is never changed automatically."""
        cfg = self.cfg
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=1.0)
                return
            except asyncio.TimeoutError:
                pass
            try:
                self._sample_premium()
                await self._check_drift()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("drift monitor failed")

    def _sample_premium(self) -> None:
        p = self.premium_bps()
        if p is not None:
            self._premium_hist.append((time.time(), p))

    async def _check_drift(self) -> None:
        cfg = self.cfg
        now = time.time()
        if now - self._last_drift_check < cfg.drift_check_sec:
            return
        self._last_drift_check = now
        if not self._premium_hist:
            return
        cutoff = now - cfg.drift_window_sec
        vals = [p for ts, p in self._premium_hist if ts >= cutoff]
        if not vals:
            return
        mean = sum(vals) / len(vals)
        limit = (cfg.upper_bps + cfg.lower_bps) / 2.0 * cfg.drift_band_factor
        if abs(mean - cfg.midline_bps) <= limit:
            if self._drift_started is not None:
                log.info("premium mean %.2f bps back inside drift band — "
                         "sentinel disarmed", mean)
            self._drift_started = None
            if self._drift_halted:
                if cfg.drift_auto_resume_sec > 0:
                    if self._drift_back_since is None:
                        self._drift_back_since = now
                        log.warning("premium mean %.2f bps back inside band "
                                    "— auto-resume in %.0fs if sustained",
                                    mean, cfg.drift_auto_resume_sec)
                    elif now - self._drift_back_since \
                            >= cfg.drift_auto_resume_sec:
                        self._drift_halted = False
                        self._drift_back_since = None
                        log.warning("DRIFT auto-resume — premium back inside "
                                    "band for %.0fs; opening resumed / "
                                    "漂移已回到带内，恢复开仓",
                                    cfg.drift_auto_resume_sec)
                        await self._notify("🧭 漂移已回到带内持续 "
                                           f"{cfg.drift_auto_resume_sec:.0f}s，"
                                           "引擎恢复开仓")
                else:
                    log.warning("premium mean %.2f bps back inside band — "
                                "still DRIFT-HALTED; restart to resume / "
                                "漂移均值已回带内但仍停机，请人工确认后重启",
                                mean)
            else:
                self._drift_back_since = None
            return
        # still breaching: reset any pending auto-resume wait
        self._drift_back_since = None
        if self._drift_started is None:
            self._drift_started = now
            log.warning("premium mean %.2f bps drifting from midline %.2f "
                        "(limit %.2f) — watching for %.0fs", mean,
                        cfg.midline_bps, limit, cfg.drift_halt_sec)
        elif now - self._drift_started >= cfg.drift_halt_sec \
                and not self._drift_halted:
            self._drift_halted = True
            log.critical("DRIFT HALT — premium mean %.2f bps vs midline "
                         "%.2f sustained %.0fs; opening paused, only "
                         "position-reducing trades allowed / 中枢漂移停机，"
                         "仅放行减仓方向，请人工确认后调整配置重启",
                         mean, cfg.midline_bps, cfg.drift_halt_sec)
            await self._notify(f"🧭 中枢漂移告警：均值 {mean:+.2f} bps vs "
                               f"midline {cfg.midline_bps:+.2f} 持续超限"
                               f"——已停开仓，仅减仓")

    async def _balance_loop(self) -> None:
        while not self.stop.is_set():
            for v in self.venues.values():
                try:
                    got = await v.fetch_equity()
                    if got is not None:
                        v.equity, v.free = got
                        if v.start_equity is None:
                            v.start_equity = v.equity
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.debug("[%s] equity poll failed: %r", v.name, e)
                # funding follows the same slow cadence (P1-3 gate input)
                try:
                    await v.fetch_funding()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.debug("[%s] funding poll failed: %r", v.name, e)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=BALANCE_POLL_SEC)
            except asyncio.TimeoutError:
                pass

    async def _http_keepalive_loop(self) -> None:
        if self.cfg.http_keepalive_sec <= 0:
            return
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(),
                                       timeout=self.cfg.http_keepalive_sec)
                return
            except asyncio.TimeoutError:
                pass
            await asyncio.gather(*(v.warm_http() for v in self.venues.values()),
                                 return_exceptions=True)

    def account_delta(self) -> Optional[float]:
        """Change in real account equity since start (both venues)."""
        total = 0.0
        for v in self.venues.values():
            if v.equity is None or v.start_equity is None:
                return None
            total += v.equity - v.start_equity
        return total

    def session_pnl(self) -> Optional[float]:
        total = 0.0
        for v in self.venues.values():
            m = v.book.mid()
            if m is None:
                return None
            total += v.cash + v.position * m
        if self._mtm_baseline is None:
            self._mtm_baseline = total
        return total - self._mtm_baseline

    def premium_bps(self) -> Optional[float]:
        em, hm = self.entropy.book.mid(), self.hedge.book.mid()
        if not (em and hm):
            return None
        return (em / hm - 1.0) * 1e4

    async def _web_loop(self) -> None:
        """Embedded HTTP status dashboard (GET / and /api/status). Never
        interrupts trading: bind failure is logged and the loop exits."""
        cfg = self.cfg

        async def index(request):
            return web.Response(text=PAGE_HTML, content_type="text/html")

        async def api_status(request):
            return web.json_response(status_payload(self))

        app = web.Application()
        app.router.add_get("/", index)
        app.router.add_get("/api/status", api_status)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, cfg.web_dashboard_host,
                               cfg.web_dashboard_port)
            await site.start()
        except Exception as e:
            log.warning("[web] dashboard bind %s:%d failed: %r — trading "
                        "unaffected", cfg.web_dashboard_host,
                        cfg.web_dashboard_port, e)
            await runner.cleanup()
            return
        log.info("[web] dashboard http://%s:%d", cfg.web_dashboard_host,
                 cfg.web_dashboard_port)
        try:
            await self.stop.wait()
        finally:
            await runner.cleanup()

    async def _status_loop(self) -> None:
        cfg = self.cfg
        while not self.stop.is_set():
            try:
                await asyncio.sleep(cfg.status_interval_sec)
            except asyncio.CancelledError:
                raise
            books = " | ".join(
                f"{v.name} {v.book.best_bid() or '—'}/{v.book.best_ask() or '—'}"
                + ("" if v.book.is_fresh(cfg.staleness_sec,
                                         cfg.data_staleness_sec) else " STALE")
                + (" RATE-LTD" if self._venue_limited(v) else "")
                + (" DOWN" if v.key in self._venue_down else "")
                + (" UNRESOLVED" if v.key in self._venue_unresolved_until else "")
                for v in self.venues.values())
            prem = self.premium_bps()
            prem_s = f"{prem:+.2f}" if prem is not None else "—"
            pos = " ".join(f"{v.name} {v.position:+.6g}"
                           for v in self.venues.values())
            net = sum(v.position for v in self.venues.values())
            pnl = self.session_pnl()
            rec = (f" | rec {self.recorder.rows_written} rows"
                   if self.recorder else "")
            log.info("[status] %s | prem %s bps (band %+.2f..%+.2f) | pos %s "
                     "net %+.6g | trades %d hedges %d | MTM %s expEdge $%.4f "
                     "fillEdge $%.4f%s%s%s",
                     books, prem_s, cfg.midline_bps - cfg.lower_bps,
                     cfg.midline_bps + cfg.upper_bps, pos, net, self.trades,
                     self.hedges,
                     f"${pnl:+.4f}" if pnl is not None else "—",
                     self.total_exp_edge, self.total_fill_edge, rec,
                     " *** DRIFT ***" if self._drift_halted else "",
                     " *** HALTED ***" if self.halted else "")

    def _log_csv(self, direction, buy, sell, plan: ArbPlan, ok: bool, bfill,
                 sfill, bstatus, sstatus, fill_edge, inv_bps,
                 buy_lat_ms=None, sell_lat_ms=None, slip_buy_bps=None,
                 slip_sell_bps=None, signal_age=None,
                 dyn_buy=None, dyn_sell=None) -> None:
        try:
            path = self.cfg.trades_csv
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            if os.path.exists(path):
                with open(path) as fh0:
                    if fh0.readline().strip() != ",".join(CSV_HEADER):
                        os.replace(path, path + ".old")
            new = not os.path.exists(path)

            def cell(x, fmt=".3f"):
                return "" if x is None else f"{x:{fmt}}"

            with open(path, "a", newline="") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(CSV_HEADER)
                w.writerow([f"{time.time():.3f}",
                            direction, buy.name, sell.name, f"{plan.qty:.8g}",
                            plan.buy_limit, plan.sell_limit,
                            f"{plan.buy_notional:.2f}", f"{plan.sell_notional:.2f}",
                            f"{plan.exp_edge_usd:.4f}", f"{plan.gross_edge_usd:.4f}",
                            f"{plan.marginal_premium_bps:.3f}",
                            f"{self.cfg.midline_bps:.3f}",
                            f"{inv_bps:.3f}", int(ok), f"{bfill:.8g}",
                            f"{sfill:.8g}", bstatus, sstatus, f"{fill_edge:.4f}",
                            cell(buy_lat_ms), cell(sell_lat_ms),
                            cell(slip_buy_bps), cell(slip_sell_bps),
                            cell(signal_age), cell(dyn_buy), cell(dyn_sell)])
        except Exception:
            log.exception("csv write failed")
