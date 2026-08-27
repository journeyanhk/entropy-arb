# 变更提案: 一期实盘加固（P0/P1 六项）

## 需求背景

代码 review（entorpy-review1.md）针对 50U/边 SNDK 实盘验证给出 P0/P1 施工单。核心痛点：

1. **P0-1**：Lighter 腿 `send_taker` 结算超时直接返回 `unresolved`，引擎对该腿 5s 宽限后依赖周期对账（reconcile_sec=10s+），裸露窗口约 20s，期间仓位状态未知且该所仍可能继续开单。
2. **P0-2**：无强平/保证金风控——margin 拒单只会被动熔断 10s，且无距离强平价的告警与主动减仓；50U 小资金下 0.8 倍杠杆仍可能被单边 20% 波动击穿。
3. **P1-3**：`midline_bps` 是固定常数，中枢漂移时单边方向持续开单直至库存帽。
4. **P1-4**：`premium_persist_sec=0`（幻影信号直接开单）、`staleness_sec=10s`（对 taker 套利过宽）。
5. **P1-5**：`book.is_fresh` 只看连接心跳 `alive_ts`——盘口数据帧长时间不更新（股票永续盘外时段）仍被判为新鲜，实际盘口已失真。
6. **⑥**：无 Telegram 告警，无人值守时停机/强平/漂移无人知晓。

## 变更内容

1. Lighter 腿结算超时后通过 REST `GET /api/v1/accountOrders` 按 `client_order_index` 主动查单终态（1s 间隔重试 3 次），查不到才返回 `unresolved`。
2. `unresolved` 触发的对账对该 venue 熔断（禁开单）直至对账采纳链上仓位；强制模式跳过 5s 宽限改为 3s 并重试。
3. 新增 30s 风控循环：每所拉取 mark/清算价，距离 <10% → critical 告警 + 双边 reduce-only 平仓 + HALT；`_scan` 增加保证金预检（`free < notional × 1.2` 不发单）。
4. 中枢漂移哨兵：每秒采样溢价，每分钟算 30 分钟均值；`|均值−midline| > (upper+lower)/2` 持续 10 分钟 → 只放行减仓方向 + critical 告警（不自动改 midline）。
5. 默认参数：`premium_persist_sec=0.5`、`staleness_sec=2.5`、`cooldown_sec=1.0`、`inventory.floor_frac=0.5`。
6. 数据帧级新鲜度：`is_fresh` 增加 `last_update_ts` 判据（60s），数据帧超时主动断连重连强制重新订阅。
7. Telegram 告警：HALT、对冲失败、漂移哨兵、强平告警四个点推送。

## 影响范围

- **模块:** venue_lighter, venue_hl, engine, book, feeds, config, dashboard, recorder（只读引用）
- **文件:**
  - `entropy_arb/venue_lighter.py`、`entropy_arb/venue_hl.py`
  - `entropy_arb/engine.py`、`entropy_arb/book.py`、`entropy_arb/feeds.py`
  - `entropy_arb/config.py`、`entropy_arb/notifier.py`（新增）
  - `entropy_arb/dashboard.py`、`entropy_arb/recorder.py`
  - `config.example.yaml`、`.env.example`
  - `tests/test_engine.py`、`tests/test_book.py`、`tests/test_config.py`、`tests/test_notifier.py`（新增）
- **API:** Lighter `GET /api/v1/accountOrders`（查询本账户订单，需 authorization 头）
- **数据:** 无 schema 变更

## 核心场景

### 需求: unresolved 即查即修（P0-1）
**模块:** venue_lighter / engine

#### 场景: Lighter 腿结算超时
- 下单后 `settle_timeout` 内未收到 account_orders ws 终态
- 预期结果: REST 按 coi 查单，查到 filled/canceled 返回真实终态；3 次查不到才 unresolved，且该所熔断禁开单；对账采纳链上仓位后解除熔断

#### 场景: 对账加速
- unresolved 触发 `_reconcile_evt` 后
- 预期结果: 对该 venue 跳过 5s 宽限（改 3s），REST 滞后时多轮重试，裸露窗口从 ~20s 压到 ~5s

### 需求: 强平/保证金风控（P0-2）
**模块:** engine / venue_hl / venue_lighter

#### 场景: 接近强平价
- 任一所持仓距清算价 <10%
- 预期结果: critical 告警 + 双边 reduce-only 平到 0 + HALT + Telegram

#### 场景: 保证金不足
- `v.free` 低于 `plan.notional × 1.2`
- 预期结果: 跳过该方向（事前不发单），不再依赖 margin 拒单后被动熔断

### 需求: 中枢漂移哨兵（P1-3）
**模块:** engine

#### 场景: 溢价中枢漂移
- 30 分钟均值偏离 midline 超过 (upper+lower)/2 且持续 10 分钟
- 预期结果: critical 告警 + Telegram；只放行减仓方向；不自动改 midline（人工确认后改配置重启）

### 需求: 数据帧新鲜度（P1-5）
**模块:** book / feeds

#### 场景: 盘口数据长时间不更新
- 连接心跳正常但 `last_update_ts` 超过 60s
- 预期结果: `is_fresh` 判为盲；feed 主动断连重连强制重新订阅；引擎不开单、不对冲、不采样

### 需求: Telegram 告警（⑥）
**模块:** notifier / engine

#### 场景: 停机或风险事件
- HALT / 对冲失败 / 漂移哨兵 / 强平告警
- 预期结果: Telegram 推送消息；发送失败仅记日志不阻塞引擎

## 风险评估

- **风险:** REST 查单接口（accountOrders）需要 authorization 头，token 过期或接口字段变化会导致查单失败
  - **缓解:** 每次查单重新生成 auth token；解析失败视为未查到（保守回退 unresolved）；字段解析全部防御式
- **风险:** 熔断后无法解除导致该所永久停摆
  - **缓解:** 对账成功采纳链上仓位即解除；status 行显示熔断状态可观测
- **风险:** 强平平仓本身失败（所已不可达/盘口失真）
  - **缓解:** 平仓用 reduce-only + 滑点保护；失败则 critical 告警持续记录，保持 HALT
- **风险:** 数据帧看门狗在极清淡时段误判（股票永续盘外）
  - **缓解:** 默认 60s 窗口可配置；重连即强制重新订阅快照，不判死