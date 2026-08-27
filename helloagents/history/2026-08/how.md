# 技术设计: 一期实盘加固（P0/P1 六项）

## 技术方案

### 核心技术
- Python 3.14 / asyncio / aiohttp（现状不变）
- Lighter REST `GET /api/v1/accountOrders`（authorization 头 = `signer.create_auth_token_with_expiry()`，响应 `{"code", "orders": [{client_order_index, status, filled_base_amount, filled_quote_amount, ...}]}`）
- HL `clearinghouseState` → `assetPositions[].position.markPx / liquidationPx`
- Lighter `account` → `positions[].liquidation_price`，mark = `position_value/position` 或盘口中价

### 实现要点

**P0-1 venue_lighter.send_taker**：`TimeoutError` 分支先 `await self._query_order(coi)`（最多 3 次、间隔 1s）：生成 auth token → GET accountOrders → 从 `orders` 列表按 `client_order_index` 匹配；status 非 OPEN_STATUSES 即终态，`avg_px = filled_quote/filled_base`（若 filled_base>0）。全查不到才返回 `unresolved: True`。

**P0-1 engine**：
- 新增 `self._venue_unresolved_until: Dict[str, float]`（独立于限频熔断，避免与 RATE_LIMITED 互踩）；`_execute` 对 `unresolved` 腿设 `float("inf")`；`_venue_limited()` 合并判断。
- `_reconcile_positions(force_keys)`：force 集合内的 venue 宽限 5s→3s，`_reconcile_venue` force 模式 fetch 失败重试 3 轮（1s 间隔）。
- `_reconcile_venue` fetch 成功后清除该 venue 熔断并 `self._update_evt.set()`。

**P0-2**：
- venue 新增 `async fetch_risk() -> Optional[(mark, liq)]`；引擎新增 `_risk_loop`（30s）：`liq>0 and mark>0` 时 `dist = abs(mark-liq)/mark`，`dist <= liquidation_distance_pct/100` → `_flatten_all()` + `halted=True` + Telegram。
- `_flatten_all()`：逐所 reduce-only taker 平到 0（qty=|position|，价格按 best + hedge_slippage 保护，持所锁）；所不可达/盘口盲则 critical 记日志。
- `_scan` 保证金预检：`getattr(v, "free", None) is not None and v.free < notional × margin_reserve_factor` → 跳过该方向（两腿都查）。

**P1-3 漂移哨兵**：`_drift_loop` 每秒 append `(ts, premium)` 到 `self._premium_hist`（deque，maxlen=window+1）；每 `drift_check_sec` 算窗口内均值；超阈（`(upper+lower)/2 × drift_band_factor`）累计 `drift_halt_sec` → `self._drift_halted=True` + critical + Telegram。`_scan` 在 drift_halted 时只放行减仓方向：`sell_entropy` 仅当 `entropy.position > 0`，`buy_entropy` 仅当 `entropy.position < 0`。

**P1-5**：`book.is_fresh(max_age_sec, data_max_age_sec=None)` 追加 `time.time()-last_update_ts <= data_max_age_sec` 判据；`feeds.py` 两个 feed 内加 `_data_watch` 协程（每 5s 检查 book.last_update_ts，超时 `await ws.close()` 触发重连+重新订阅）；`data_staleness_sec` 经 `start_tasks(stop, notify, live, data_staleness_sec)` 传入。

**⑥ notifier.py**：`Notifier(token, chat_id)` + asyncio 队列 + 单 worker；`https://api.telegram.org/bot<token>/sendMessage`，失败重试 1 次，队列满丢弃并记日志；`enabled = token and chat_id`。engine 持有实例，HALT/对冲失败/漂移/强平 四点 `_notify()`。

**配置新增**（execution 段，均带默认值）：
`data_staleness_sec: 60.0`、`drift_window_sec: 1800.0`、`drift_check_sec: 60.0`、`drift_halt_sec: 600.0`、`drift_band_factor: 1.0`、`risk_loop_sec: 30.0`、`liquidation_distance_pct: 10.0`、`margin_reserve_factor: 1.2`。
默认值调整：`premium_persist_sec 0.3→0.5`、`staleness_sec 10→2.5`、`cooldown_sec 0→1.0`；`config.example.yaml` 同步 + `floor_frac: 0→0.5`。

## 架构决策 ADR

### ADR-01: unresolved 熔断独立于限频熔断
**上下文:** 报告建议直接复用 `_venue_limited_until=inf`；但限频熔断是"过段时间自动恢复"，unresolved 熔断是"必须对账确认后才恢复"，语义不同且会互相清除。
**决策:** 新增独立字典 `_venue_unresolved_until`，`_venue_limited()` 合并判断，对账成功单独清除。
**理由:** 职责分离，避免限频 10s 自动恢复与 unresolved 永久熔断互相覆盖。
**替代方案:** 复用 `_venue_limited_until` → 拒绝原因: 对账成功清除会误清限频暂停，反之限频到期会误解除 unresolved 熔断。
**影响:** 无外部依赖；dashboard 显示为 RATE-LTD（可观测）。

### ADR-02: 漂移哨兵只停开仓不自动改 midline
**上下文:** 上一代项目教训是全自动均值回归会"追着漂移的中枢满仓"。
**决策:** 哨兵只告警 + 停开仓（放行减仓方向），人工确认后改配置重启。
**理由:** 验证期以数据为准，杜绝自我强化。
**影响:** 需人工介入；哨兵持续监控并记录回归信息日志。

### ADR-03: Lighter 查单走 REST accountOrders 而非 ws 历史
**上下文:** `AccountOrdersFeed._terminal` 只缓存 ws 收到过的终态，超时后 ws 可能已断；SDK 有 `OrderApi.account_orders`（REST）。
**决策:** 直接以原生 aiohttp GET `/api/v1/accountOrders?client_order_indexes=<coi>&account_index=<idx>`，authorization 头用 `create_auth_token_with_expiry()` 现取。
**理由:** 不依赖 SDK 内部 ApiClient 生命周期；token 每次现取避免过期。
**影响:** 解析 `{code, orders[]}`；字段缺失视为查不到（保守）。

## 安全与性能
- **安全:** token 每次现取、不在日志打印；密钥仍只在 .env；不新增任何硬编码凭据。
- **性能:** 风控循环 30s 一次、查单最多 3 次 × 1s（仅超时路径）；漂移哨兵纯内存 deque；看门狗每 5s 一次比较，无网络开销。

## 测试与部署
- **测试:** `python3 -m pytest tests/`；新增：is_fresh 数据陈旧、margin 预检跳过、漂移哨兵减仓方向放行、清算距离计算、unresolved 查单解析（mock HTTP）、notifier 队列行为（无 token 时不发）。
- **部署:** 50U 配置（见报告）先在 `--record-only` 采集 ≥1 交易日，`tools/analyze.py` 定中枢后再实盘；仅美股常规时段运行。