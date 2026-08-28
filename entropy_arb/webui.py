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
<html lang="zh" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<title>entropy-arb · 实时面板</title>
<style>
  :root { color-scheme: dark; }
  :root[data-theme="light"] { color-scheme: light; }

  /* --- 主题变量：极简扁平双主题 --- */
  :root {
    --bg: #0f1115; --card: #171a21; --line: #23272f;
    --text: #d7dce3; --dim: #8a919c; --accent: #4f8cff;
    --pos: #3fb950; --neg: #f85149; --warn: #d29922;
    --badge-run: #2ea043; --badge-stop: #da3633; --badge-warn-bg: #d29922;
  }
  :root[data-theme="light"] {
    --bg: #f6f7f9; --card: #ffffff; --line: #e4e7ec;
    --text: #1f2328; --dim: #6a737d; --accent: #2563eb;
    --pos: #1a7f37; --neg: #d1242f; --warn: #9a6700;
    --badge-run: #1a7f37; --badge-stop: #d1242f; --badge-warn-bg: #d4a72c;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    background: var(--bg); color: var(--text);
    font: 13px/1.55 -apple-system, "PingFang SC", "Microsoft YaHei",
         "Segoe UI", system-ui, sans-serif;
    padding: 14px; min-width: 0;
  }
  h1 { font-size: 15px; font-weight: 600; letter-spacing: .2px; }
  .top { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
         margin-bottom: 14px; }
  .top .spacer { flex: 1; }
  #clock { font-variant-numeric: tabular-nums; }

  .badge { padding: 3px 10px; border-radius: 6px; font-weight: 600;
           font-size: 12px; white-space: nowrap; }
  .b-run  { background: var(--badge-run);  color: #fff; }
  .b-rec  { background: var(--warn);       color: #fff; }
  .b-stop { background: var(--badge-stop); color: #fff; }
  .b-drift{ background: var(--badge-warn-bg); color: #1f2328; }
  .b-stale{ background: var(--warn);       color: #fff; }

  button { font: inherit; cursor: pointer; }
  #themeBtn {
    background: var(--card); color: var(--dim); border: 1px solid var(--line);
    border-radius: 8px; padding: 5px 12px; font-size: 13px;
    transition: color .15s;
  }
  #themeBtn:hover { color: var(--accent); border-color: var(--accent); }

  .grid { display: grid; gap: 12px; min-width: 0;
          grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); }
  .card { background: var(--card); border: 1px solid var(--line);
          border-radius: 10px; padding: 12px 14px; min-width: 0;
          overflow: hidden; }
  .card h2 { font-size: 11px; color: var(--dim); text-transform: uppercase;
             letter-spacing: .8px; margin-bottom: 10px; font-weight: 600; }
  .card .body { min-width: 0; }

  /* 表：卡片内横向滚动，永不截断/顶破边框 */
  .tbl-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch;
              margin: 0 -4px; padding: 0 4px; }
  table { border-collapse: collapse; width: 100%; min-width: 100%; }
  td, th { padding: 4px 8px; text-align: right; white-space: nowrap;
           font-variant-numeric: tabular-nums; }
  td:first-child, th:first-child { text-align: left; }
  th { color: var(--dim); font-weight: 500; font-size: 11px; }
  td { font-size: 12.5px; }
  tr + tr td { border-top: 1px solid var(--line); }
  .tbl-wrap::-webkit-scrollbar { height: 6px; }
  .tbl-wrap::-webkit-scrollbar-thumb { background: var(--line); border-radius: 3px; }

  .pos { color: var(--pos); } .neg { color: var(--neg); }
  .dim { color: var(--dim); }
  .ok { color: var(--pos); } .err { color: var(--neg); }

  .spark-wrap { min-width: 0; }
  canvas { width: 100%; height: 110px; display: block; }

  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
         margin-right: 6px; vertical-align: middle; }
  .d-on { background: var(--pos); } .d-off { background: var(--line); }

  .bd { display: block; font-size: 11px; color: var(--dim); margin-top: 2px;
        font-variant-numeric: tabular-nums; }

  .sigline { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
             margin-bottom: 8px; }
  .sigline b { font-variant-numeric: tabular-nums; }
  .kpi { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
         gap: 2px 14px; min-width: 0; }
  .kpi .row { display: flex; justify-content: space-between; gap: 8px;
              padding: 3px 0; min-width: 0; }
  .kpi .row span:first-child { color: var(--dim); }
  .kpi .row span:last-child { font-variant-numeric: tabular-nums; }

  /* 移动端：单列、触控友好 */
  @media (max-width: 640px) {
    body { padding: 10px; font-size: 12.5px; }
    .grid { grid-template-columns: 1fr; }
    td { font-size: 12px; padding: 5px 6px; }
    .card { padding: 10px 12px; }
    #themeBtn { padding: 6px 14px; }
    .sigline { gap: 6px; }
  }
</style>
</head>
<body>
<div class="top">
  <h1>entropy-arb <span class="dim" id="sym"></span></h1>
  <span class="badge" id="badge">连接中…</span>
  <span class="dim" id="uptime"></span>
  <span class="spacer"></span>
  <span class="dim" id="clock"></span>
  <button id="themeBtn" title="切换主题 / toggle theme">🌙</button>
</div>
<div class="grid">
  <div class="card"><h2>信号 · premium vs 带宽</h2>
    <div class="sigline">
      <span>mid premium <b id="prem"></b> bps</span>
      <span>midline <b id="midline"></b></span>
      <span>band [<b id="band"></b>]</span>
    </div>
    <div class="spark-wrap"><canvas id="spark"></canvas></div>
  </div>
  <div class="card"><h2>方向门槛</h2>
    <div class="tbl-wrap"><table id="dirs"></table></div>
  </div>
  <div class="card"><h2>交易所</h2>
    <div class="tbl-wrap"><table id="venues"></table></div>
  </div>
  <div class="card"><h2>会话</h2>
    <div class="kpi" id="session"></div>
  </div>
  <div class="card"><h2>最近成交</h2>
    <div class="tbl-wrap"><table id="trades"><tr><td class="dim">暂无成交</td></tr></table></div>
  </div>
</div>
<script>
const $ = id => document.getElementById(id);
let hist = [];

/* --- 主题：跟随系统 + 手动切换持久化 --- */
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  $('themeBtn').textContent = t === 'dark' ? '🌙' : '☀️';
  try { localStorage.setItem('ea-theme', t); } catch (e) {}
}
function initTheme() {
  let t = null;
  try { t = localStorage.getItem('ea-theme'); } catch (e) {}
  if (!t) t = matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  setTheme(t);
}
$('themeBtn').addEventListener('click', () => {
  setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
});

function fmt(x, d = 4) {
  return x === null || x === undefined ? '—' : Number(x).toFixed(d);
}
function usd(x, d = 4) {
  if (x === null || x === undefined) return '—';
  const s = Math.abs(x).toLocaleString('en-US', {minimumFractionDigits: d,
                                                 maximumFractionDigits: d});
  return (x < 0 ? '-' : '') + '$' + s;
}
function badge(s) {
  const b = $('badge');
  if (s.halted)       { b.textContent = 'HALTED';    b.className = 'badge b-stop'; }
  else if (s.drift_halted) { b.textContent = 'DRIFT'; b.className = 'badge b-drift'; }
  else if (s.venue_down.length) { b.textContent = 'VENUE DOWN'; b.className = 'badge b-stop'; }
  else if (s.stale)   { b.textContent = 'STALE';     b.className = 'badge b-stale'; }
  else if (s.record_only) { b.textContent = 'RECORD-ONLY'; b.className = 'badge b-rec'; }
  else                { b.textContent = 'RUNNING';   b.className = 'badge b-run'; }
}
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
    const bd = d.hurdle_breakdown || null;
    let bdHtml = '';
    if (bd) {
      const parts = ['base ' + fmt(bd.base, 2),
                     'inv ' + fmt(bd.inventory, 2),
                     'fund ' + fmt(bd.funding, 2),
                     'slip ' + fmt(bd.slip_gate, 2)];
      bdHtml = '<span class="bd">' + parts.join(' + ') + '</span>';
    }
    tr.innerHTML = `<td>${d.label}${bdHtml}</td>
      <td>${fmt(d.premium, 2)}</td>
      <td>${fmt(d.hurdle, 2)}</td>
      <td class="${gap >= 0 ? 'ok' : 'dim'}">${fmt(gap, 2)}</td>
      <td><span class="dot ${d.armed ? 'd-on' : 'd-off'}"></span></td>`;
    dr.appendChild(tr);
  }

  const v = $('venues'); v.innerHTML =
    '<tr><th>所</th><th>bid/ask</th><th>spread</th><th>age</th>' +
    '<th>position</th><th>equity</th><th>free</th><th>fund/h</th><th>miss%</th></tr>';
  for (const x of s.venues) {
    const tr = document.createElement('tr');
    const flags = (x.down ? ' DOWN' : '') + (x.limited ? ' LTD' : '') +
                  (x.unresolved ? ' UNRES' : '') + (x.stale ? ' STALE' : '');
    tr.innerHTML = `<td>${x.name}${flags}</td>
      <td>${x.bid ?? '—'}/${x.ask ?? '—'}</td>
      <td>${fmt(x.spread_bps, 1)}</td>
      <td>${fmt(x.data_age, 1)}s</td>
      <td class="${x.position > 0 ? 'pos' : x.position < 0 ? 'neg' : 'dim'}">${fmt(x.position, 6)}</td>
      <td>${usd(x.equity, 2)}</td><td>${usd(x.free, 2)}</td>
      <td>${x.funding_bps_h === null ? '—' : fmt(x.funding_bps_h, 2)}</td>
      <td>${x.miss_rate === null ? '—' : (x.miss_rate * 100).toFixed(1)}</td>`;
    v.appendChild(tr);
  }

  const se = $('session'); se.innerHTML = '';
  const rows = [
    ['PnL (MTM)', usd(s.mtm_pnl), s.mtm_pnl > 0 ? 'pos' : s.mtm_pnl < 0 ? 'neg' : ''],
    ['账户权益变动', usd(s.account_delta), s.account_delta > 0 ? 'pos' : s.account_delta < 0 ? 'neg' : ''],
    ['Σ 权益', usd(s.total_equity, 2), ''],
    ['Σ 预期收益', usd(s.exp_edge), ''],
    ['Σ 实际收益', usd(s.fill_edge), ''],
    ['执行 / 对冲', s.trades + ' / ' + s.hedges, ''],
    ['净敞口', fmt(s.net_delta, 6), Math.abs(s.net_delta) > 0.003 ? 'err' : ''],
    ['连续错误', s.consec_errors, s.consec_errors ? 'err' : ''],
    ['分钟数据行', s.recorder_rows, ''],
    ['上次执行', s.last_trade_ago === null ? '—' : fmt(s.last_trade_ago, 0) + 's 前', ''],
  ];
  for (const [k, val, cls] of rows) {
    const row = document.createElement('div');
    row.className = 'row';
    const l = document.createElement('span'); l.textContent = k;
    const r = document.createElement('span'); r.textContent = val;
    if (cls) r.className = cls;
    row.appendChild(l); row.appendChild(r);
    se.appendChild(row);
  }

  const t = $('trades'); t.innerHTML = '';
  if (!s.recent_trades.length) {
    t.innerHTML = '<tr><td class="dim">暂无成交</td></tr>';
    return;
  }
  t.innerHTML = '<tr><th>时间</th><th>方向</th><th>数量</th><th>名义</th>' +
    '<th>溢价bps</th><th>预期$</th><th>实际$</th><th>状态</th></tr>';
  for (const r of s.recent_trades.slice().reverse()) {
    const tr = document.createElement('tr');
    const cls = r.ok ? 'ok' : 'err';
    tr.innerHTML = `<td>${new Date(r.ts * 1000).toLocaleTimeString()}</td>
      <td>${r.direction}</td><td>${fmt(r.qty, 6)}</td><td>${usd(r.notional, 0)}</td>
      <td>${fmt(r.prem_bps, 1)}</td><td>${usd(r.exp)}</td>
      <td class="${cls}">${r.fill === null ? '—' : usd(r.fill)}</td>
      <td class="${cls}">${r.status}</td>`;
    t.appendChild(tr);
  }
}
function drawSpark(vals) {
  const c = $('spark'), ctx = c.getContext('2d');
  const w = c.clientWidth, h = c.clientHeight;
  if (!w || !h) return;
  c.width = w * devicePixelRatio;
  c.height = h * devicePixelRatio;
  ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (vals.length < 2) return;
  const lo = Math.min(...vals), hi = Math.max(...vals), span = (hi - lo) || 1;
  const css = getComputedStyle(document.documentElement);
  ctx.strokeStyle = css.getPropertyValue('--accent').trim();
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  for (let i = 0; i < vals.length; i++) {
    const x = i / (vals.length - 1) * w;
    const y = h - (vals[i] - lo) / span * (h - 8) - 4;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  }
  ctx.stroke();
  ctx.fillStyle = css.getPropertyValue('--dim').trim();
  ctx.font = '10px monospace';
  ctx.fillText(lo.toFixed(2) + ' … ' + hi.toFixed(2) + ' bps', 6, h - 4);
}
initTheme();
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
        "funding_bps_h": getattr(v, "funding_bps_h", None),
        "miss_rate": None,
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
        if eng.slippage is not None:
            row["miss_rate"] = eng.slippage.miss_rate(v.key, cfg.symbol)
        if v.key in eng._venue_down:
            down_keys.add(v.key)
        venues.append(row)
    prem = eng.premium_bps()
    directions = []
    for buy, sell, dkey, label in (
            (eng.hedge, eng.entropy, "sell_entropy", "卖出 ENTROPY → 买入 对冲"),
            (eng.entropy, eng.hedge, "buy_entropy", "买入 ENTROPY → 卖出 对冲")):
        ba, sb = buy.book.best_ask(), sell.book.best_bid()
        # decomposed so "why isn't it firing" is answerable at a glance
        breakdown = eng._hurdle_breakdown(buy, sell)
        hurdle = (breakdown["base"] + breakdown["inventory"]
                  + breakdown["funding"] + breakdown["slip_gate"]
                  + buy.fee_bps + sell.fee_bps)
        directions.append({
            "label": label,
            "premium": None if not (ba and sb) else (sb / ba - 1.0) * 1e4,
            "hurdle": hurdle,
            "hurdle_breakdown": breakdown,
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