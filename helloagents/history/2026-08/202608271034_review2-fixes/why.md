# 变更提案: 一期修复复查轮（R1–R7）

## 需求背景

review2 对 `3e2656f` 的验收结论：六项修复全部落地，但发现 2 个部署前必修的时序竞态 + 5 个低优先级问题：

- **R1（中）**：force 对账解熔只凭单次 REST 读数。Lighter REST 滞后 WS 结算时可能读到成交前仓位就解熔，把"幻影对冲震荡"从后门放回来。修法：采纳前要求间隔 1 s 的连续两次读数一致（position_sync_confirmations=2 老配方）。
- **R2（中）**：强平路径先平仓后置 `halted`——平仓与停机之间新信号可把刚平的仓加回。修法一行：先 `halted=True` 再 flatten。
- **R3（低-中）**：drift 减仓方向无数量钳制，$5 持仓会被 $20 单穿过零成反向新仓。修法：drift-halt 下 `cap_notional = min(单笔帽, |entropy.position| × 熵侧成交价)`。
- **R4（低）**：`_drift_halted` 永不复位但日志写 "sentinel disarmed" 误导。修法：日志改明确 + 新增 `drift_auto_resume_sec` 配置（默认 0 = 不自动解除；>0 时回带内持续 N 秒自动恢复）。
- **R5（低）**：`_query_order` 端点未经实盘验证——操作验证项（VPS 上 settle_timeout 临时 0.5 s 发最小单），无代码改动。
- **R6（低）**：shutdown 等待窗口 `settle_timeout_sec + 2 = 12 s` 短于最坏腿时长 14–16 s。改为 `+ 8`。
- **R7（低）**：Lighter `_account()` 无缓存，account 读每 30 s 被拉 2–3 次挤占下单预算。加 3 s TTL 缓存，force 对账路径绕过。

## 变更内容

1. R1: `_reconcile_venue` force 模式双读确认（间隔 1 s、两次一致才采纳解熔，最多 3 轮）
2. R2: `_check_liquidation` 先 `halted=True` 再 `_flatten_all()`
3. R3: `_scan` drift-halt 下 `plan_cap` 钳制到 |entropy.position| × 熵侧价（含 headroom replan 统一使用）
4. R4: `_check_drift` 回带内分支日志修正 + `drift_auto_resume_sec` 自动恢复（默认关）
5. R6: shutdown 等待窗口 `settle_timeout_sec + 8`
6. R7: Lighter `_account()` 3 s TTL 缓存 + `fetch_position(force=)` 参数（HL 同步加兼容参数）

## 影响范围

- **模块:** engine, venue_lighter, venue_hl, config
- **文件:** `entropy_arb/engine.py`、`entropy_arb/venue_lighter.py`、`entropy_arb/venue_hl.py`、`entropy_arb/config.py`、`config.example.yaml`、`tests/test_engine.py`、`tests/test_notifier.py`

## 核心场景

### 需求: force 对账双读确认（R1）
**模块:** engine / venue_lighter

#### 场景: REST 滞后导致单次读数旧
- 前提: unresolved 熔断后，REST 首读返回成交前仓位
- 预期结果: 两次读数不一致 → 不解熔，下一轮再试；一致才采纳解熔

### 需求: 强平先停机后平仓（R2）
**模块:** engine

#### 场景: 强平距离触线
- 预期结果: halted 先置位，任何新信号被挡；再执行 reduce-only 全平

### 需求: drift 减仓数量钳制（R3）
**模块:** engine

#### 场景: 持仓只剩 $5 时漂移停机放行减仓
- 预期结果: 单笔名义 ≤ |entropy.position| × 价，不穿过零

### 需求: 漂移自动恢复（R4，默认关）
**模块:** engine / config

#### 场景: 均值回带内持续 N 秒
- 预期结果: 配置 >0 时自动解除漂移停机并告警；默认 0 保持人工重启，日志明确提示

### 需求: 账户读 TTL 缓存（R7）
**模块:** venue_lighter

#### 场景: risk/balance/reconcile 循环叠加
- 预期结果: 3 s 内重复读复用缓存；force 对账强制新鲜读

## 风险评估

- **风险:** 双读确认增加 force 对账时长（最多 +3 s），熔断期间不交易无成本
- **风险:** 缓存 3 s 内读到旧仓位——普通路径有 5 s 宽限兜底；force 路径绕过缓存
- **风险:** R4 自动恢复默认关，行为与现状一致，无回退风险