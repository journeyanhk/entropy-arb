# 变更提案: review5 部署前修复（funding 口径归一化 / signal_age 传值）

## 需求背景

review5 部署前最高优先级两项：

1. **funding 周期口径**：查证结论——HL `metaAndAssetCtxs.funding` 与 Lighter `funding-rates.rate` **均为 8h 口径**（HL 每小时支付其 1/8），两所一致。当前 `_funding_cost_bps` 把 8h 费率直接当持仓成本，隐含"持仓满 8h"，短期持仓成本被**高估 8 倍**（review5 担心的方向相反但同样是口径 bug）。修法：内部统一存 **bps/hour**（÷8），成本 = 不利侧小时费率 × `funding_hold_hours`（预期持仓时长）。
2. **signal_age 跨笔累积**：`_execute` 用 `_armed[dkey]` 全局时间戳，第 2、3 笔上度量的是"边际总存在时长"而非"本笔武装→发单"。修法：`_scan` 返回 `(buy, sell, plan, armed_ts)` 四元组传值，`_execute` 用传入值。

## 变更内容

1. venue `funding_bps_8h` → `funding_bps_h`（bps/hour，÷8）；`_funding_cost_bps` = 不利侧和 × `funding_hold_hours`
2. 新增配置 `funding_hold_hours`（默认 4.0，>0 校验）
3. `_scan` 返回四元组（含本轮 armed_ts），`_evaluate`/`_execute_locked`/`_execute` 贯通，signal_age 用传入值
4. 更新测试（四元组解包、funding 小时口径、hold_hours 折算）

## 影响范围

- **文件:** `entropy_arb/engine.py`、`entropy_arb/venue_hl.py`、`entropy_arb/venue_lighter.py`、`entropy_arb/config.py`、`entropy_arb/webui.py`（字段名引用）、`config.example.yaml`、`tests/test_engine.py`、`tests/test_config.py`

## 核心场景

### 需求: funding 按小时口径折算
**模块:** engine / venue

#### 场景: 持仓 1 小时、8h 费率 8 bps
- 前提: 不利侧 funding 8 bps/8h，hold_hours=4
- 预期结果: 成本 = 8/8 × 4 = 4 bps，门槛 +min(2, cap)

### 需求: signal_age 为本笔武装→发单
**模块:** engine

#### 场景: 连续边际第 3 笔
- 预期结果: age = 本轮武装时间戳到发单的间隔，而非边际总存在时长

## 风险评估

- **风险:** funding 字段口径若与查证不符——以 8h 口径实现，若实际为小时口径则成本低估 8 倍；建议跑一天用实际 funding 流水反推（文档已注明）
- **风险:** hold_hours 默认值——4h 折中（过短则闸门失效，过长则频繁阻断）；用户可按实测持仓时长调整