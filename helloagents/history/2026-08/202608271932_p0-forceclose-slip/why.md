# 变更提案: P0 强平单滑点修复（review4）

## 需求背景

review4 P0 复核发现必修 bug：`_hedge()` 超时后的兜底强平调用 `_hedge_once(0.0)`——零滑点 = 保护价钉在盘口最优价，对吃单 IOC 恰恰是**最不容易成交**的一单。行情正因快速移动才走到强平，此时零滑点大概率打空 → 直接 halt。兜底反而比重试更弱。

修复：强平单用全序列最宽滑点（新增配置 `hedge_force_close_slip_bps: 200`，SNDK 上 200 bps ≈ $3/股保护空间，实际成交仍按盘口逐档价）。

## 变更内容

1. `_hedge()` 强平调用改为 `_hedge_once(cfg.hedge_force_close_slip_bps / 1e4)`
2. 新增配置键 `hedge_force_close_slip_bps`（默认 200.0，>0 校验）

## 影响范围

- **文件:** `entropy_arb/engine.py`、`entropy_arb/config.py`、`config.example.yaml`、`tests/test_engine.py`、`tests/test_config.py`

## 核心场景

### 需求: 强平单必须最宽滑点
**模块:** engine

#### 场景: 对冲超时触发兜底强平
- 前提: 5s 截止后仍有净敞口
- 预期结果: 以 200 bps（全序列最宽）保护价下单，成交确定性最高；仍失败才 halt

## 风险评估

- **风险:** 200 bps 宽保护价在极端行情下可能吃深——但这是兜底路径（正常路径 20→100 bps 已失败），且实际按盘口逐档成交；可配置
- **风险:** 滑点未超配置上限——校验 >0 且默认保守