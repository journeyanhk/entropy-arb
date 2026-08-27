# engine 模块

## 目的
双所套利策略主循环：信号扫描、执行、对冲、对账、风控。

## 模块概述
- **职责:** `_scan` 选方向 → 双腿锁 → `_execute` taker 双发 → `_hedge` 净敞口对冲；`_reconcile_loop` 链上对账；`_risk_loop` 强平风控；`_drift_loop` 中枢漂移哨兵；`_balance_loop` 余额轮询；`_status_loop` 状态日志
- **状态:** ✅稳定
- **最后更新:** 2026-08-27

## 规范

<!-- 🔁 针对每个需求重复以下格式 -->
### 需求: unresolved 即查即修（P0-1）
**模块:** engine
结算超时/未知成交的腿：`_execute` 对该 venue 设置 `_venue_unresolved_until[venue] = inf`（熔断禁开单），`_reconcile_loop` 以 force_keys 驱动 `_reconcile_positions`（宽限 3s、fetch 重试 3 轮），`_reconcile_venue` 采纳链上仓位后清除熔断并 `_update_evt.set()`。

#### 场景: Lighter 腿结算超时
- 前提: 超时后 REST 查单 3 次仍未确认终态
- 预期结果: unresolved 返回 → 该所熔断；对账确认后解除；裸露窗口 ~5s

### 需求: 强平/保证金风控（P0-2）
**模块:** engine
`_risk_loop`（30s）拉 `venue.fetch_risk()`；`liq_distance(mark, liq) = |mark-liq|/mark <= liquidation_distance_pct/100` → `_flatten_all()`（逐所 reduce-only 平到 0）+ HALT + Telegram。`_scan` 在发单前用 `_margin_ok(v, notional)`（free >= notional × margin_reserve_factor，free 未知不阻塞）预检两腿。

#### 场景: 接近强平价
- 预期结果: 双边平仓 + 停机 + 告警

#### 场景: 保证金不足
- 预期结果: 跳过该方向，不发单

### 需求: 中枢漂移哨兵（P1-3）
**模块:** engine
`_drift_loop` 每秒采样溢价入 `_premium_hist`；每 `drift_check_sec` 计算 `drift_window_sec` 内均值；`|均值−midline| > (upper+lower)/2 × drift_band_factor` 持续 `drift_halt_sec` → `_drift_halted=True` + critical + Telegram。`_scan` 在漂移停机时只放行减仓方向（sell_entropy 需 entropy.position>0；buy_entropy 需 entropy.position<0）。不自动改 midline。

#### 场景: 溢价中枢漂移
- 预期结果: 停开仓、仅减仓、人工确认后改配置重启

### 需求: Telegram 告警（⑥）
**模块:** engine / notifier
触发点：HALT（连续错误/强平）、对冲失败、漂移哨兵触发、强平告警。

## API接口
- `liq_distance(mark, liq) -> float` 清算距离计算（模块函数）
- `Engine._margin_ok(v, notional) -> bool` 保证金预检
- `Engine._flatten_all()` 双边 reduce-only 平仓
- `Engine._check_drift()` 漂移哨兵单次检查

## 数据模型
- `_venue_unresolved_until: Dict[str, float]` unresolved 熔断（对账成功后清除）
- `_premium_hist: deque[(ts, premium)]` 溢价采样（maxlen = drift_window_sec+2）
- `_drift_started / _drift_halted` 漂移状态

## 依赖
- book（plan_arb / floor_step）、config、notifier、recorder、venue_hl、venue_lighter

## 变更历史
- [202608271001_phase1-risk-hardening](../../history/2026-08/202608271001_phase1-risk-hardening/) - 一期实盘加固