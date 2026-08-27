# 任务清单: P0 强平单滑点修复（review4）

目录: `helloagents/plan/202608271932_p0-forceclose-slip/`

---

## 1. 配置层
- [√] 1.1 在 `entropy_arb/config.py` 中新增 `hedge_force_close_slip_bps`（默认 200.0，>0 校验）+ schema
- [√] 1.2 在 `config.example.yaml` 中新增该键与注释

## 2. engine 修复
- [√] 2.1 在 `entropy_arb/engine.py` 的 `_hedge()` 中将 `_hedge_once(0.0)` 改为 `_hedge_once(cfg.hedge_force_close_slip_bps / 1e4)`，验证 why.md#强平单必须最宽滑点-对冲超时触发兜底强平

## 3. 测试
- [√] 3.1 在 `tests/test_config.py` 中实现默认值/覆盖/校验测试
- [√] 3.2 在 `tests/test_engine.py` 中断言强平最后一搏使用最宽滑点（200 bps）
- [√] 3.3 运行 `python3 -m pytest tests/` 全部通过

## 4. 文档更新
- [√] 4.1 同步知识库（engine 模块文档、CHANGELOG.md、history/index.md）
- [√] 4.2 迁移方案包至 `helloagents/history/2026-08/`