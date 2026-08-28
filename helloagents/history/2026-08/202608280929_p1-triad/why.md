# 变更提案: P1 三项（armed 重置 / 延迟滑点打点 / funding 过滤）

## 需求背景

review4 给出的 P1 三项：
1. **P1-1 armed 重置**：`_scan` 守卫分支（stale/not ready/down/limited）不清武装 → 盘口断流 30s 恢复后第一个 tick 秒开火，"持续性"验证的是断流前旧世界
2. **P1-2 打点**：腿级延迟/实现滑点/信号年龄落盘 trades.csv + analyze.py 分析——二期动态滑点与延迟优化的数据地基
3. **P1-3 funding 过滤**：中枢回归可能耗数小时，5.5 bps 净边际易被 funding 吃穿；开仓前折算不利 funding 成本进门槛（只影响开仓，永不阻碍减仓）

## 变更内容

1. P1-1：`_scan` 四类"世界不可信"守卫分支清除 `self._armed[dkey]`（锁占用/预算耗尽保留）
2. P1-2：`_execute` 记录 `buy_lat_ms/sell_lat_ms`（send_taker 计时）、`slip_buy_bps/slip_sell_bps`（avg_px 缺失跳过，不回填）、`signal_age_sec`；CSV_HEADER +5 列；`tools/analyze.py --trades` 输出延迟/滑点/age 分布
3. P1-3：venue 增 `funding_bps_8h`（HL `metaAndAssetCtxs`、Lighter `funding-rates`，均 ×1e4 转 bps，30s 随 _balance_loop 轮询）；`_funding_cost_bps`（多头付正 funding、空头付负 funding，只计不利侧）；`_eff_threshold` 对**加仓方向**加 `min(cost×0.5, funding_cap_bps)`，减仓方向不加
4. 顺带修复：webui 方向门槛重复加 inv_add（`_eff_threshold` 已含）；dashboard 方向门槛与引擎口径对齐（含 funding）

## 影响范围

- **文件:** `entropy_arb/engine.py`、`entropy_arb/venue_hl.py`、`entropy_arb/venue_lighter.py`、`entropy_arb/config.py`、`entropy_arb/webui.py`、`entropy_arb/dashboard.py`、`tools/analyze.py`、`config.example.yaml`、`tests/test_engine.py`、`tests/test_config.py`、`tests/test_dashboard.py`

## 核心场景

### 需求: armed 在数据不可信时重置（P1-1）
**模块:** engine

#### 场景: 盘口断流后恢复
- 前提: 信号武装 → 断流 30s → 恢复
- 预期结果: 恢复后须重新走满 premium_persist_sec 才开火

### 需求: 延迟/滑点打点（P1-2）
**模块:** engine / analyze

#### 场景: 跑满 100–200 笔后校准
- 预期结果: trades.csv 含两腿延迟 p50/p90/p99、滑点分布、signal age；analyze.py --trades 输出分布

### 需求: funding 方向过滤（P1-3）
**模块:** engine / venue

#### 场景: 开仓方向 funding 不利
- 前提: 空 Entropy 且 Entropy funding 为负（空头付）
- 预期结果: 门槛增加 min(cost×0.5, cap) bps；减仓方向不受影响；funding 温和时照常

## 风险评估

- **风险:** funding 单位假设（per-8h 分数 ×1e4）——两所口径一致（HL 与 Lighter 文档均确认 8h 分数费率）；系数 0.5 + cap 5 限制影响面
- **风险:** CSV 列错位——守卫测试校验 header 长度与写入行一致
- **风险:** 老 trades.csv 无新列——analyze 用 r.get() 防御，跳过