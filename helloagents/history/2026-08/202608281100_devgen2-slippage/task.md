# 任务清单: 二期③滑点动态管理（dev-gen2）

目录: `helloagents/plan/202608281100_devgen2-slippage/`

---

## 1. SlipModel（先写 + 单测）
- [√] 1.1 新建 `entropy_arb/slippage.py`：`SlipModel(state, window_n=200, window_hours=72, min_samples=30, miss_threshold=0.15, protect_mult=1.5, protect_floor_bps=10, protect_cap_bps=30, gate_weight=1.0)`；`observe(venue,symbol,slip,filled,order)`、`p50/p90`（时间窗∩数量窗）、`gate_bps(buy,sell,symbol)`（开仓往返滑点，负值截断）、`protect_bps(venue,symbol,fallback)`（clamp，cap 只紧不松）、`miss_rate(venue,symbol)`、`save/load`（(venue,symbol) 键控，load 容错）
- [√] 1.2 新建 `tests/test_slippage.py`：分位数、时间窗衰减、负值截断、冷启动回退、miss_rate、持久化往返、键隔离

## 2. config 层
- [√] 2.1 在 `entropy_arb/config.py` 中新增 `slippage` 节（enabled/state_file/min_samples/window_n/window_hours/gate_weight/protect_mult/protect_floor_bps/protect_cap_bps/miss_threshold）+ schema/校验
- [√] 2.2 在 `config.example.yaml` 中新增 `slippage` 节与注释

## 3. engine 集成
- [√] 3.1 在 `entropy_arb/engine.py` 中：`_run_inner` 初始化/加载 SlipModel（enabled 时），shutdown save
- [√] 3.2 `_eff_threshold` 开仓方向加 `slippage.gate_bps(...)`（复用 `_direction_reduces`）
- [√] 3.3 `_execute` 每腿动态保护价（`protect_bps`，回退静态）+ 记录动态保护价为 shadow；settle 后 `observe` 每腿（slip + filled/order）
- [√] 3.4 `CSV_HEADER` 加 `dyn_protect_buy_bps, dyn_protect_sell_bps` 2 列并贯通 `_log_csv`
- [√] 3.5 实现 `_hurdle_breakdown(buy,sell)`（base/inventory/funding/slip_gate 分解），供 webui/dashboard

## 4. webui 可观测性
- [√] 4.1 在 `entropy_arb/webui.py` 中：`status_payload` 加 `hurdle_breakdown`；venues 卡片加 `miss_rate`

## 5. 测试
- [√] 5.1 在 `tests/test_engine.py` 中实现：滑点门槛仅开仓方向、动态保护价回退与 clamp、CSV 新列守卫、observe 接线
- [√] 5.2 在 `tests/test_config.py` 中实现 slippage 节默认/覆盖/校验
- [√] 5.3 运行 `python3 -m pytest tests/` 全部通过

## 6. 文档与提交
- [√] 6.1 同步知识库（slippage 模块文档、wiki api/data、CHANGELOG.md、history/index.md）
- [√] 6.2 迁移方案包至 `helloagents/history/2026-08/`
- [√] 6.3 提交并推送 dev-gen2 分支到远程