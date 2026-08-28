# slippage 模块

## 目的
按腿维护实测滑点分布，反馈到开仓门槛与自适应保护价（二期③）。

## 模块概述
- **职责:** `SlipModel` 滚动分位（p50/p90）、往返滑点门槛、每腿动态保护价、miss-rate 统计、状态持久化
- **状态:** 🚧开发中
- **最后更新:** 2026-08-28

## 规范

### 需求: 开仓方向往返滑点门槛
**模块:** slippage / engine
`gate_bps(buy, sell, symbol) = (max(0,p50_buy)+max(0,p50_sell)) × 2 × gate_weight`，只在**开仓方向**（`_direction_reduces` False）由 `_hurdle_breakdown` 加入 `_eff_threshold`——减仓永不因滑点卡住；负滑点在门槛侧截断（p90 保留原始分布给保护价）。

#### 场景: 开仓，p50 双腿各 2 bps
- 预期结果: 门槛 += 8 bps；减仓方向不加

### 需求: 每腿自适应保护价
**模块:** slippage / engine
`protect_bps(venue, symbol, fallback) = clamp(p90×protect_mult, floor, cap)`；样本 <min_samples 回退 fallback。首周 `protect_cap_bps=30`（=静态值）只许收紧；miss_rate 作为放宽决策依据（webui 展示）。

#### 场景: 某腿 p90=4 bps
- 预期结果: 保护价 = clamp(6,10,30) = 10；样本不足回退静态 30

## API接口
- `SlipModel.observe(venue, symbol, slip_bps, filled_qty, order_qty)` 喂样本（slip None = miss，进 miss 池；内部按时间窗 + window_n×2 修剪 samples）
- `SlipModel.p50/p90(venue, symbol) -> Optional[float]` 滚动分位（200 笔 ∩ 72h）
- `SlipModel.gate_bps(buy_key, sell_key, symbol) -> float` 往返滑点门槛
- `SlipModel.protect_bps(venue, symbol, fallback) -> float` 动态保护价
- `SlipModel.miss_rate(venue, symbol) -> Optional[float]` 24h 滚动 miss 率

## 数据质量约定（review6）
- **miss 归因**：只有"无 err、无 unresolved、到达交易所且 IOC 打空"的腿进 miss 池（`_execute` 过滤）；网络失败/结果未知不污染
- **samples 有界**：observe 内修剪，时间窗外 + 超 window_n×2 即淘汰
- **miss 告警**：engine 每小时每所最多一次（超 miss_threshold → log.warning + 通知）

## 数据模型
- 状态文件 `logs/slip_state.json`，键 `(venue_key|symbol) → {samples:[[ts,bps]], fills:[[ts,full]]}`，20 次 observe + shutdown 写盘，load 容错

## 依赖
- engine（`_eff_threshold`/`_execute` 接线）、config（slippage 节）

## 变更历史
- [202608281100_devgen2-slippage](../../history/2026-08/202608281100_devgen2-slippage/) - 二期③滑点动态管理（dev-gen2 分支）