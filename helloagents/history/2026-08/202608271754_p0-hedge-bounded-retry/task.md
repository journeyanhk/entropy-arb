# 任务清单: P0-1 裸露敞口有界快速重试（review3）

目录: `helloagents/plan/202608271754_p0-hedge-bounded-retry/`

---

## 1. 配置层
- [√] 1.1 在 `entropy_arb/config.py` 中扩展 schema 支持 list 类型（元素须为数字），新增 `hedge_retry_slips_bps`（默认 [20,50,100]，校验非空且 >0）、`hedge_retry_interval_sec`（0.5）、`hedge_force_close_timeout_sec`（5.0），验证 why.md#对冲失败快速重试-一腿成交另一腿落空首次-hedge-失败
- [√] 1.2 在 `config.example.yaml` 中新增三个键与注释

## 2. engine 对冲重试
- [√] 2.1 在 `entropy_arb/engine.py` 中将 `_hedge` 拆为 `_hedge`（重试循环：递增滑点、deadline、对价最后一搏、有界 halt）+ `_hedge_once`（单次尝试，内部重算净敞口），验证 why.md#对冲失败快速重试-超时仍未削平
- [√] 2.2 实现 `_min_hedgeable`：残留敞口低于可对冲最小量 → carry 不 halt，验证 why.md#小敞口低于可对冲最小量-残留敞口低于可对冲最小量

## 3. 安全检查
- [√] 3.1 执行安全检查（G9: 重试次数有界、每尝试独立持锁、无死循环风险）

## 4. 测试
- [√] 4.1 在 `tests/test_engine.py` 中实现：首次失败二次成功（滑点放宽、halted=False）、连续失败超时（halted=True）、小敞口 carry（不 halt）
- [√] 4.2 在 `tests/test_config.py` 中实现：list 默认值/覆盖/校验（空 list、负值、非数字元素报错）
- [√] 4.3 运行 `python3 -m pytest tests/` 全部通过

## 5. 文档更新
- [√] 5.1 同步知识库（engine 模块文档、CHANGELOG.md、history/index.md）
- [√] 5.2 迁移方案包至 `helloagents/history/2026-08/`