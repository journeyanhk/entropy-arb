# 变更提案: review6 修复（miss 归因过滤 / samples 修剪 / miss 告警）

## 需求背景

review6 对 dev-gen2 滑点模块的数据质量层提出 3 个问题：

- **A（必修）**：`observe()` 无条件调用，`send-failed`/限频/margin 拒单/`unresolved` 均被计为 miss——网络抖动会推高 miss_rate，而它正是放宽保护价 cap 的依据，反馈信号被非市场噪声劫持。修法：err/unresolved 的腿不进任何池。
- **B（必修）**：`samples` 队列只进不出（对比 fills 有 24h 修剪），内存与状态文件随运行线性膨胀，且 save() 是结算热路径上的同步 IO。修法：observe 内按时间窗 + `window_n×2` 余量修剪。
- **C（该修）**：`miss_threshold` 配置了却无人消费——死配置。修法：status loop 检查 miss_rate 超阈值 → log.warning + 通知（每小时限频），把"何时放宽 cap"变成系统主动提醒。

## 变更内容

1. `engine._execute`：observe 前过滤 err/unresolved 腿
2. `slippage.observe`：samples 时间窗 + 数量上限修剪
3. `engine`：miss 告警检查（挂 status loop，1h 限频，通知带行动建议）

## 影响范围

- **文件:** `entropy_arb/engine.py`、`entropy_arb/slippage.py`、`tests/test_slippage.py`、`tests/test_engine.py`

## 核心场景

### 需求: miss 池只含市场性失败（A）
**模块:** engine / slippage

#### 场景: 网络抖动导致 send-failed
- 预期结果: 该腿不进 miss 池；miss_rate 不被噪声推高

#### 场景: unresolved 腿
- 预期结果: 不计数（结果未知，对账恢复仓位）

### 需求: samples 有界（B）
**模块:** slippage

#### 场景: 运行数周
- 预期结果: samples ≤ window_n×2 且窗内样本保留；状态文件恒定小

### 需求: miss 超阈值告警（C）
**模块:** engine

#### 场景: 24h miss 率 > 15%
- 预期结果: 每小时最多一次 log.warning + Server酱通知，文案含放宽 cap 的行动建议

## 风险评估

- **风险:** 过滤过严漏掉真 miss——unresolved 后对账确认未成交的腿丢失样本（可接受，比污染好）
- **风险:** 修剪误删——保留 window_n×2 余量，重启后时间窗仍有样本