# 变更提案: P0-1 裸露敞口有界快速重试（review3）

## 需求背景

review3 流程审查第 1 条（胜率/稳定性头号问题）：单腿失败后 `_hedge` 只做**一次** reduce-only，保护价固定 `hedge_slippage_bps`（20 bps）。市场快速移动时对腿落空、20 bps 追不上 → hedge 失败 → 只能等 `reconcile_sec`（10 s）下一轮，期间单边裸奔且只有一行 warning。最坏损失"未知"。

修复：**递增滑点快速重试 + 有界止损**——失败后在 1–2 秒窗口内以 20→50→100 bps 递增滑点连续重试；超时仍未削平 → 对价（无保护）最后一搏；仍失败 → HALT + 告警，把最坏损失变成"有界"。

## 变更内容

1. `_hedge` 重构为 `_hedge`（重试循环）+ `_hedge_once`（单次尝试）
2. 新增配置：`hedge_retry_slips_bps: [20,50,100]`（list 类型，schema 扩展）、`hedge_retry_interval_sec: 0.5`、`hedge_force_close_timeout_sec: 5.0`
3. 超时最终判定：净敞口小于"可对冲最小量"（min_base / min_notional / min_quote）→ carry 不 halt（保留原行为）；否则 halt + critical + Telegram

## 影响范围

- **模块:** engine, config
- **文件:** `entropy_arb/engine.py`、`entropy_arb/config.py`、`config.example.yaml`、`tests/test_engine.py`、`tests/test_config.py`

## 核心场景

### 需求: 对冲失败快速重试
**模块:** engine

#### 场景: 一腿成交另一腿落空，首次 hedge 失败
- 前提: 首次 reduce-only 返回 err/unresolved
- 预期结果: 0.5 s 后以更宽滑点（50 bps）重试；仍失败再试 100 bps；削平即返回

#### 场景: 超时仍未削平
- 前提: 5 s 窗口内净敞口无法削平
- 预期结果: 对价最后一搏；仍残留 → HALT + critical + Telegram

#### 场景: 小敞口低于可对冲最小量
- 前提: 残留敞口 < min_base/min_notional 折算量
- 预期结果: carry（原行为），不误 halt

## 风险评估

- **风险:** 重试窗口内重复发单消耗限频预算——上限 3–4 次/5 s，远低于 24/min 预算；每次尝试独立持锁
- **风险:** venue down 时最终判定 halt——裸奔中 halt + 告警优于静默 carry；Telegram 无凭据时静默（提醒用户配置）
- **风险:** 部分成交残留——每次重试重算净敞口，只补剩余量