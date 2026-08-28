"""Notifier: enable flag, queueing, drop-on-full; Lighter REST order query
and account-snapshot TTL cache.

Run:  python3 -m pytest tests/  (or  python3 tests/test_notifier.py)
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from entropy_arb.config import (LighterCreds, LighterProfile, VenueConf)  # noqa: E402
from entropy_arb.notifier import Notifier  # noqa: E402
from entropy_arb.venue_lighter import LighterVenue  # noqa: E402


def test_disabled_without_credentials():
    from entropy_arb.notifier import ServerChanChannel, TelegramChannel
    n = Notifier([TelegramChannel(None, None), ServerChanChannel(None)])
    assert not n.enabled
    n.send("anything")          # no-op, no queue, no exception


def test_enabled_flag_and_queue():
    from entropy_arb.notifier import TelegramChannel
    n = Notifier([TelegramChannel("tok", "123")])
    assert n.enabled
    n.send("hi")
    assert n._queue.qsize() == 1


def test_queue_full_drops():
    from entropy_arb.notifier import TelegramChannel
    n = Notifier([TelegramChannel("tok", "123")], max_queue=2)
    n.send("1")
    n.send("2")
    n.send("3")
    assert n._queue.qsize() == 2


class FakeResp:
    status = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    async def json(self):
        return self._payload


class FakeCtx:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return FakeResp(self._payload)

    async def __aexit__(self, *a):
        return False


class FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.last_params = None

    def get(self, url, params=None, headers=None, timeout=None):
        self.last_params = params
        return FakeCtx(self._payload)


class FakeSigner:
    def create_auth_token_with_expiry(self):
        return "auth-token", None


def make_venue(session):
    conf = VenueConf(key="hedge", kind="lighter", label="RH", symbol="SNDK",
                     fee_bps=0.0, cap_usd=40.0, orders_per_min=24,
                     lighter_profile=LighterProfile("rh", "https://api.rh.lighter.xyz",
                                                    "wss://api.rh.lighter.xyz/stream",
                                                    466324),
                     lighter_creds=LighterCreds(account_index=7,
                                                api_key_index=2,
                                                api_private_key="k"))
    v = LighterVenue(conf, None, 5.0)
    v.session = session
    v.signer = FakeSigner()
    return v


FILLED = {"code": 0, "orders": [
    {"client_order_index": 5, "status": "filled",
     "filled_base_amount": "2.5", "filled_quote_amount": "250.0"}]}

OPEN = {"code": 0, "orders": [
    {"client_order_index": 5, "status": "in-progress",
     "filled_base_amount": "0", "filled_quote_amount": "0"}]}

EMPTY = {"code": 0, "orders": []}


def test_query_order_resolves_terminal():
    s = FakeSession(FILLED)
    v = make_venue(s)
    info = asyncio.run(v._query_order(5))
    assert info == {"status": "filled", "filled_base": 2.5, "avg_px": 100.0}
    assert s.last_params == {"client_order_indexes": "5", "account_index": "7"}


def test_query_order_open_is_not_terminal():
    v = make_venue(FakeSession(OPEN))
    assert asyncio.run(v._query_order(5)) is None


def test_query_order_not_found():
    v = make_venue(FakeSession(EMPTY))
    assert asyncio.run(v._query_order(999)) is None


def test_query_order_bad_status_ignored():
    payload = {"code": 0, "orders": [
        {"client_order_index": 5, "status": "filled",
         "filled_base_amount": "1.0", "filled_quote_amount": "bad"}]}
    v = make_venue(FakeSession(payload))
    info = asyncio.run(v._query_order(5))
    assert info["status"] == "filled" and info["filled_base"] == 0.0
    assert info["avg_px"] is None


def test_serverchan_channel():
    from entropy_arb.notifier import ServerChanChannel
    assert not ServerChanChannel(None).enabled
    assert not ServerChanChannel("").enabled
    c = ServerChanChannel("sct-key-123")
    assert c.enabled and c.name == "serverchan"


def test_notifier_aggregates_channels():
    from entropy_arb.notifier import (ServerChanChannel,
                                      TelegramChannel)
    # only serverchan configured: still enabled, single channel
    n = Notifier([TelegramChannel(None, None),
                  ServerChanChannel("sct-key-123")])
    assert n.enabled
    assert [c.name for c in n._channels] == ["serverchan"]
    # neither configured: silent no-op
    n2 = Notifier([TelegramChannel(None, None), ServerChanChannel(None)])
    assert not n2.enabled


class FakePostCtx:
    def __init__(self, status, body="ok"):
        self.status = status
        self._body = body
        self._json = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def text(self):
        return self._body

    async def json(self):
        return self._json or {"code": 0}


class FakePostSession:
    def __init__(self, status=200, body="ok", json_body=None):
        self._status = status
        self._body = body
        self._json = json_body
        self.calls = []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        return FakePostCtx(self._status, self._body)


def test_serverchan_post_builds_request():
    from entropy_arb.notifier import ServerChanChannel
    c = ServerChanChannel("sct-key-123")
    s = FakePostSession()
    asyncio.run(c.post(s, "标题行\n详情第一行\n详情第二行"))
    url, kw = s.calls[0]
    assert url == "https://sctapi.ftqq.com/sct-key-123.send"
    assert kw["data"]["title"] == "标题行"
    assert "详情第一行" in kw["data"]["desp"]


def test_serverchan_post_failure_raises():
    from entropy_arb.notifier import ServerChanChannel
    c = ServerChanChannel("sct-key-123")
    # HTTP 500 -> raise (Notifier retries once)
    try:
        asyncio.run(c.post(FakePostSession(status=500), "x"))
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass


class CountingSession:
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        return FakeCtx(self._payload)


def test_account_cache_reuses_snapshot():
    s = CountingSession({"accounts": [{"total_asset_value": "10"}]})
    v = make_venue(s)

    async def go():
        a1 = await v._account()
        a2 = await v._account()
        assert a1 is a2 and s.calls == 1      # TTL: second read cached

    asyncio.run(go())


def test_account_cache_expires():
    s = CountingSession({"accounts": [{"total_asset_value": "10"}]})
    v = make_venue(s)

    async def go():
        await v._account()                        # fetch + cache (call 1)
        v._acct_cache_ts = time.time() - 10.0     # force expiry
        await v._account()                        # fetch again (call 2)
        assert s.calls == 2

    asyncio.run(go())


def test_fetch_position_force_bypasses_cache():
    payload = {"accounts": [{"positions": [
        {"market_id": 1, "sign": 1, "position": "5"}]}]}
    s = CountingSession(payload)
    v = make_venue(s)
    v.market_id = 1

    async def go():
        await v._account()                    # fills the cache (call 1)
        pos1 = await v.fetch_position()       # served from cache
        pos2 = await v.fetch_position(force=True)   # bypasses cache (call 2)
        assert pos1 == 5.0 and pos2 == 5.0
        assert s.calls == 2

    asyncio.run(go())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name:40s} OK")