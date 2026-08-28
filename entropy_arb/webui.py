"""Web status dashboard for the engine (方案 B).

A single-file HTML page rendered by aiohttp inside the engine process:
    GET /            -> the page (dark cards, canvas premium sparkline,
                        1s fetch polling, no external dependencies)
    GET /api/status  -> JSON payload assembled from the live engine object

status_payload() is deliberately dashboard-agnostic so tests can call it
without a network. The page shows the same signals the Rich dashboard does:
status badges, both venues, signal vs band, session numbers and recent
executions.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

PAGE_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>entropy-arb · 实时面板</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background:#0d1117; color:#c9d1d9; font:13px/1.5 -apple-system,
         "PingFang SC", "Microsoft YaHei", monospace; padding:12px; }
  h1 { font-size:15px; font-weight:600; }
  .top { display:flex; align-items:center; gap:10px; flex-wrap:wrap;
         margin-bottom:12px; }
  .badge { padding:2px 10px; border-radius:4px; font-weight:600; }
  .b-run { background:#1a7f37; color:#fff; }
  .b-rec { background:#9e6a03; color:#fff; }
  .b-stop { background:#b62324; color:#fff; }
  .b-drift { background:#d29922; color:#000; }
  .b-stale { background:#9e6a03; color:#fff; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr));
          gap:12px; }
  .card { background:#161b22; border:1px solid #30363d; border-radius:8px;
          padding:12px; }
  .card h2 { font-size:12px; color:#8b949e; text-transform:uppercase;
             margin-bottom:8px; letter-spacing:.5px; }
  table { width:100%; border-collapse:collapse; }
  td,th { padding:3px 6px; text-align:right; font-variant-numeric:tabular-nums; }
  th { color:#8b949e; font-weight:400; }
  td:first-child, th:first-child { text-align:left; }
  .pos { color:#3fb950; } .neg { color:#f85149; }
  .dim { color:#8b949e; }
  canvas { width:100%; height:120px; display:block; }
  .ok { color:#3fb950; } .err { color:#f85149; }
  .trades td { font-size:12px; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%;
         margin-right:6px; }
  .d-on { background:#3fb950; } .d-off { background:#30363d; }
</style>
</head>
<body>
<div class="top">
  <h1>entropy-arb <span class="dim" id="sym"></span></h1>
  <span class="badge" id="badge">连接中…</span>
  <span class="dim" id="uptime"></span>
  <span class="dim" id="clock"></span>
</div>
<div class="grid">
  <div class="card"><h2>信号 · premium vs 带宽</h2>
    <div>mid premium <b id="prem"></b> bps &nbsp;midline <b id="midline"></b>
         &nbsp;band [<span id="band"></span>]</div>
    <canvas id="spark"></canvas>
  </div>
  <div class="card"><h2>方向门槛</h2>
    <table id="dirs"></table>
  </div>
  <div class="card"><h2>交易所</h2>
    <table id="venues"></table>
  </div>
  <div class="card"><h2>会话</h2>
    <table id="session"></table>
  </div>
  <div class="card trades"><h2>最近成交</h2>
    <table id="trades"><tr><td class="dim">暂无成交</td></tr></table>
  </div>
</div>
<script>
const $ = id => document.getElementById(id);
let hist = [];
async function tick() {
  try {
    const s = await (await fetch('/api/status')).json();
    render(s);
  } catch (e) {
    $('badge').textContent = '连接断开';
    $('badge').className = 'badge b-stop';
  }
}
function badge(s) {
  const b = $('badge');
  if (s.halted) { b.textContent = 'HALTED'; b.className = 'badge b-stop'; }
  else if (s.drift_halted) { b.textContent = 'DRIFT'; b.className = 'badge b-drift'; }
  else if (s.venue_down) { b.textContent = 'VENUE DOWN'; b.className = 'badge b-stop'; }
  else if (s.stale) { b.textContent = 'STALE'; b.className = 'badge b-stale'; }
  else if (s.record_only) { b.textContent = 'RECORD-ONLY'; b.className = 'badge b-rec'; }
  else { b.textContent = 'RUNNING'; b.className = 'badge b-run'; }
}
function fmt(x, d=4) { return x === null || x === undefined ? '—' : Number(x).toFixed(d); }
function usd(x, d=4) { return x === null || x === undefined ? '—' : (x < 0 ? '-' : '') + '$' + Math.abs(x).toFixed(d); }
function render(s) {
  $('sym').textContent = s.symbol + ' × ' + s.hedge_name;
  badge(s);
  $('uptime').textContent = '运行 ' + fmt(s.uptime_sec, 0) + 's';
  $('clock').textContent = new Date(s.ts * 1000).toLocaleTimeString();
  $('prem').textContent = fmt(s.premium_bps, 2);
  $('midline').textContent = fmt(s.midline_bps, 2);
  $('band').textContent = fmt(s.band_low, 2) + ' … ' + fmt(s.band_high, 2);
  hist = (s.premium_history || []).slice(-300);
  drawSpark(hist.map(p => p[1]));
  const dr = $('dirs'); dr.innerHTML = '';
  for (const d of s.directions) {
    const gap = d.premium - d.hurdle;
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${d.label}</td><td>${fmt(d.premium,2)}</td>` +
      `<td>${fmt(d.hurdle,2)}</td><td class="${gap>=0?'ok':'dim'}">${fmt(gap,2)}</td>` +
      `<td><span class="dot ${d.armed?'d-on':'d-off'}"></span></td>`;
    dr.appendChild(tr);
  }
  const v = $('venues'); v.innerHTML =
    '<tr><th>所</th><th>bid/ask</th><th>spread</th><th>age</th>' +
    '<th>position</th><th>equity</th><th>free</th><th>funding8h</th></tr>';
  for (const x of s.venues) {
    const tr = document.createElement('tr');
    const flags = (x.down?' DOWN':'') + (x.limited?' LTD':'') +
                  (x.unresolved?' UNRES':'') + (x.stale?' STALE':'');
    tr.innerHTML = `<td>${x.name}${flags}</td>` +
      `<td>${x.bid??'—'}/${x.ask??'—'}</td><td>${fmt(x.spread_bps,1)}</td>` +
      `<td>${fmt(x.data_age,1)}s</td>` +
      `<td class="${x.position>0?'pos':x.position<0?'neg':'dim'}">${fmt(x.position,6)}</td>` +
      `<td>${usd(x.equity,2)}</td><td>${usd(x.free,2)}</td>` +
      `<td>${x.funding_bps_8h===null?'—':fmt(x.funding_bps_8h,2)}</td>`;
    v.appendChild(tr);
  }
  const se = $('session'); se.innerHTML = '';
  const rows = [
    ['PnL (MTM)', usd(s.mtm_pnl), s.mtm_pnl>0?'pos':s.mtm_pnl<0?'neg':''],
    ['账户权益变动', usd(s.account_delta), s.account_delta>0?'pos':s.account_delta<0?'neg':''],
    ['Σ 权益', usd(s.total_equity,2), ''],
    ['Σ 预期收益', usd(s.exp_edge), ''],
    ['Σ 实际收益', usd(s.fill_edge), ''],
    ['执行 / 对冲', s.trades + ' / ' + s.hedges, ''],
    ['净敞口', fmt(s.net_delta,6), Math.abs(s.net_delta)>0.003?'err':''],
    ['连续错误', s.consec_errors, s.consec_errors?'err':''],
    ['分钟数据行', s.recorder_rows, ''],
    ['上次执行', s.last_trade_ago===null?'—':fmt(s.last_trade_ago,0)+'s 前', ''],
  ];
  for (const [k,v,cls] of rows) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${k}</td><td class="${cls}">${v}</td>`; se.appendChild(tr);
  }
  const t = $('trades'); t.innerHTML = '';
  if (!s.recent_trades.length) {
    t.innerHTML = '<tr><td class="dim">暂无成交</td></tr>'; return;
  }
  t.innerHTML = '<tr><th>时间</th><th>方向</th><th>数量</th><th>名义</th>' +
    '<th>溢价bps</th><th>预期$</th><th>实际$</th><th>状态</th></tr>';
  for (const r of s.recent_trades.slice().reverse()) {
    const tr = document.createElement('tr');
    const ok = r.ok ? 'ok' : 'err';
    tr.innerHTML = `<td>${new Date(r.ts*1000).toLocaleTimeString()}</td>` +
      `<td>${r.direction}</td><td>${fmt(r.qty,6)}</td><td>${usd(r.notional,0)}</td>` +
      `<td>${fmt(r.prem_bps,1)}</td><td>${usd(r.exp)}</td>` +
      `<td class="${ok}">${r.fill===null?'—':usd(r.fill)}</td>` +
      `<td class="${ok}">${r.status}</td>`;
    t.appendChild(tr);
  }
}
function drawSpark(vals) {
  const c = $('spark'), ctx = c.getContext('2d');
  c.width = c.clientWidth * devicePixelRatio;
  c.height = 120 * devicePixelRatio;
  ctx.clearRect(0, 0, c.width, c.height);
  if (vals.length < 2) return;
  const lo = Math.min(...vals), hi = Math.max(...vals), span = (hi-lo)||1;
  const w = c.width, h = c.height, n = vals.length;
  ctx.strokeStyle = '#58a6ff'; ctx.lineWidth = 2;
  ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const x = i/(n-1)*w, y = h - (vals[i]-lo)/span*(h-8) - 4;
    i ? ctx.lineTo(x,y) : ctx.moveTo(x,y);
  }
  ctx.stroke();
  ctx.fillStyle = '#8b949e'; ctx.font = '10px monospace';
  ctx.fillText(lo.toFixed(2) + ' … ' + hi.toFixed(2) + ' bps', 6, h-4);
}
setInterval(tick, 1000);
tick();
</script>
</body>
</html>"""


def _venue_row(v) -> Dict[str, Any]:
    book = v.book
    bb, ba = book.best_bid(), book.best_ask()
    spread = (ba / bb - 1.0) * 1e4 if (bb and ba) else None
    return {
        "name": v.name,
        "key": v.key,
        "bid": bb, "ask": ba,
        "spread_bps": spread,
        "data_age": None if not book.ready else time.time() - book.last_update_ts,
        "position": v.position,
        "equity": v.equity,
        "free": v.free,
        "volume_usd": v.volume_usd,
        "cap_usd": v.cap_usd,
        "funding_bps_8h": getattr(v, "funding_bps_8h", None),
        "stale": not book.ready or not book.bids or not book.asks,
    }


def status_payload(eng) -> Dict[str, Any]:
    """Assemble the live state as JSON (no network, dashboard-agnostic)."""
    cfg = eng.cfg
    now = time.time()
    venues = []
    down_keys = set()
    for v in eng.venues.values():
        row = _venue_row(v)
        row["limited"] = eng._venue_limited(v)
        row["down"] = v.key in eng._venue_down
        row["unresolved"] = v.key in eng._venue_unresolved_until
        if v.key in eng._venue_down:
            down_keys.add(v.key)
        venues.append(row)
    prem = eng.premium_bps()
    directions = []
    for buy, sell, dkey, label in (
            (eng.hedge, eng.entropy, "sell_entropy", "卖出 ENTROPY → 买入 对冲"),
            (eng.entropy, eng.hedge, "buy_entropy", "买入 ENTROPY → 卖出 对冲")):
        ba, sb = buy.book.best_ask(), sell.book.best_bid()
        # _eff_threshold already includes inventory + funding: only fees add
        hurdle = eng._eff_threshold(buy, sell) + buy.fee_bps + sell.fee_bps
        directions.append({
            "label": label,
            "premium": None if not (ba and sb) else (sb / ba - 1.0) * 1e4,
            "hurdle": hurdle,
            "armed": bool(eng._armed.get(dkey)),
        })
    last_trade = eng.last_trade_ts or None
    return {
        "ts": now,
        "symbol": cfg.symbol,
        "hedge_name": eng.hedge.name,
        "mode": "record-only" if eng.record_only else "live",
        "record_only": eng.record_only,
        "halted": eng.halted,
        "drift_halted": eng._drift_halted,
        "stale": any(v["stale"] for v in venues),
        "venue_down": sorted(down_keys),
        "premium_bps": prem,
        "midline_bps": cfg.midline_bps,
        "band_low": cfg.midline_bps - cfg.lower_bps,
        "band_high": cfg.midline_bps + cfg.upper_bps,
        "premium_history": list(eng._premium_hist)[-300:],
        "venues": venues,
        "directions": directions,
        "net_delta": sum(v.position for v in eng.venues.values()),
        "mtm_pnl": eng.session_pnl(),
        "account_delta": eng.account_delta(),
        "total_equity": (sum(v.equity for v in eng.venues.values())
                         if all(v.equity is not None for v in eng.venues.values())
                         else None),
        "exp_edge": eng.total_exp_edge,
        "fill_edge": eng.total_fill_edge,
        "trades": eng.trades,
        "hedges": eng.hedges,
        "consec_errors": eng.consec_errors,
        "recorder_rows": eng.recorder.rows_written if eng.recorder else None,
        "last_trade_ago": None if not last_trade else now - last_trade,
        "uptime_sec": now - eng.start_ts,
        "recent_trades": list(eng.recent_trades)[-10:],
    }