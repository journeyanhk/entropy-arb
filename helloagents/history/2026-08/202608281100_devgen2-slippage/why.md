# 变更提案: 二期③滑点动态管理（dev-gen2）

## 需求背景

review5 二期③ + 设计评审（entorpy-desgin1.md）确认的组合：
- **门槛**：改良版 B——只在**开仓方向**加**整个往返四条腿**的预期滑点（复用 `_direction_reduces`，与 funding 闸门同构）；`slip_gate = one_way × 2 × gate_weight`（one_way = max(0,p50_buy)+max(0,p50_sell)）
- **保护价**：本腿用自身 p90 动态化 `clamp(p90×protect_mult, floor, cap)`，第一周 `protect_cap_bps: 30` 钉住静态值（只许收紧不许放松）；样本不足回退静态
- **take_fraction**：本轮不做（等滑点样本数据定回归形状）
- **默认开**，冷启动 <min_samples 回退静态
- 四项统计正确性：窗口时间衰减（200 笔 ∩ 72h）、负滑点门槛侧截断、**miss-rate 幸存者偏差反馈**、(venue_key, symbol) 键控
- 可观测性：webui 加 `hurdle_breakdown`；trades.csv 加保护价 shadow 对照列

## 变更内容

1. 新建 `entropy_arb/slippage.py`：`SlipModel`（滚动分位 + 时间窗 + miss 统计 + 状态持久化 + 键控）
2. engine：`_eff_threshold` 开仓方向加 `slip_gate`；`_execute` 每腿动态保护价 + settle 后 observe（slip + fill/miss）；shutdown 持久化
3. config 新节 `slippage`；`trades.csv` 加 `dyn_protect_buy/sell_bps` 2 列
4. webui：`hurdle_breakdown`（base/inventory/funding/slip_gate）+ venues 卡片 miss_rate

## 影响范围

- **文件:** `entropy_arb/slippage.py`（新增）、`entropy_arb/engine.py`、`entropy_arb/config.py`、`entropy_arb/book.py`（无）、`entropy_arb/webui.py`、`config.example.yaml`、`tests/test_slippage.py`（新增）、`tests/test_engine.py`、`tests/test_config.py`

## 核心场景

### 需求: 开仓方向滑点门槛
**模块:** engine / slippage

#### 场景: 开 SLA 仓，p50 双腿各 2 bps
- 预期结果: 门槛 += (2+2)×2×1 = 8 bps；减仓方向不加

### 需求: 自适应保护价
**模块:** engine / slippage

#### 场景: 某腿 p90=4 bps
- 预期结果: 保护价 = clamp(4×1.5=6, 10, 30) = 10；样本不足回退静态 30

### 需求: miss-rate 反馈
**模块:** slippage

#### 场景: 打空增多
- 预期结果: miss_rate 暴露（webui/日志），供 ";宽保护价 cap" 数据决策

## 风险评估

- **风险:** 门槛过高阻断交易——冷启动回退 + gate_weight 可调 + 单向收紧 cap
- **风险:** 幸存者偏差——miss 只统计不擅自放宽（由 protect_cap_bps 配置控制，数据驱动）
- **风险:** 状态文件损坏——load 容错降级为空模型