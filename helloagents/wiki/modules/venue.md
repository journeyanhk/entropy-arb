# venue 模块（venue_hl / venue_lighter）

## 目的
两家交易所适配器：行情、账户、下单、结算，对外统一结果形状 `{status, filled_base, avg_px, err, unresolved}`。

## 模块概述
- **职责:** 市场元数据加载、签名器初始化、ws 行情任务、taker 下单、余额/持仓/风险拉取、keepalive
- **状态:** ✅稳定
- **最后更新:** 2026-08-27

## 规范

### 需求: unresolved 即查即修（P0-1）
**模块:** venue_lighter
`send_taker` 结算超时后调用 `_query_order(coi)`：REST `GET /api/v1/accountOrders?client_order_indexes=<coi>&account_index=<idx>`，header `authorization` = `signer.create_auth_token_with_expiry()`；匹配到非 OPEN_STATUSES 订单即返回 `{status, filled_base, avg_px}`（avg_px = filled_quote/filled_base）。3 次 × 1s 都查不到才返回 `unresolved: True`。

#### 场景: Lighter 腿结算超时
- 前提: settle_timeout 内未收到 ws 终态
- 预期结果: REST 查到 filled/canceled 返回真实结果；查不到才 unresolved

### 需求: 强平风控数据（P0-2）
**模块:** venue_hl / venue_lighter
`fetch_risk() -> Optional[(mark, liq)]`：HL 从 `clearinghouseState.assetPositions[].position` 取 `markPx/liquidationPx`；Lighter 从 account positions 行取 `liquidation_price`，mark 由 `|position_value/position|` 推导（无持仓值则用盘口中价兜底）。

#### 场景: 风控循环拉取
- 预期结果: 有持仓返回 (mark, liq)；无持仓/无 mark 返回 None

### 需求: 账户读 TTL 缓存（review2 R7）
**模块:** venue_lighter
`_account(cache_ttl=3.0)` 缓存账户快照（risk/balance/reconcile 循环叠加，合并重复请求）；`fetch_position(force=True)` 绕过缓存（force 对账必须读到新鲜仓位）。HL 侧 `fetch_position(force=False)` 为接口兼容参数。

#### 场景: 循环叠加拉取
- 预期结果: 3 s 内重复读复用缓存；force 对账强制新鲜读

## API接口
- `LighterVenue._query_order(coi) -> Optional[dict]` REST 查单
- `HLVenue.fetch_risk() / LighterVenue.fetch_risk()` 强平距离数据
- `LighterVenue._account(cache_ttl=3.0)` 账户快照（TTL 缓存）
- `HLVenue.fetch_position(force=False) / LighterVenue.fetch_position(force=False)` 持仓读取（force 绕过缓存）

## 依赖
- book、config、feeds

## 变更历史
- [202608271001_phase1-risk-hardening](../../history/2026-08/202608271001_phase1-risk-hardening/) - 一期实盘加固