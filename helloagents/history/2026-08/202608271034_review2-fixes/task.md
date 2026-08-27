# 任务清单: 一期修复复查轮（R1–R7）

目录: `helloagents/plan/202608271034_review2-fixes/`

---

## 1. R1 force 对账双读确认
- [√] 1.1 在 `entropy_arb/engine.py` 中改造 `_reconcile_venue` force 分支：连续两次 `fetch_position(force=True)`（间隔 1 s）一致才采纳并解熔，最多 3 轮，不一致保持熔断，验证 why.md#force-对账双读确认r1-REST-滞后导致单次读数旧

## 2. R2 强平先停机后平仓
- [√] 2.1 在 `entropy_arb/engine.py` 的 `_check_liquidation` 中调整顺序：先 `self.halted = True` 再 `_flatten_all()`，验证 why.md#强平先停机后平仓r2-强平距离触线

## 3. R3 drift 减仓数量钳制
- [√] 3.1 在 `entropy_arb/engine.py` 的 `_scan` 中实现 `plan_cap`：drift-halt 下 `min(max_order_notional, |entropy.position| × 熵侧价)`，headroom replan 统一使用，验证 why.md#drift-减仓数量钳制r3-持仓只剩-5-时漂移停机放行减仓

## 4. R4 漂移恢复日志与自动恢复
- [√] 4.1 在 `entropy_arb/config.py` 中新增 `drift_auto_resume_sec`（默认 0.0）+ schema 校验
- [√] 4.2 在 `entropy_arb/engine.py` 中改造 `_check_drift` 回带内分支：日志明确 "still HALTED, restart to resume"；`drift_auto_resume_sec > 0` 时回带内持续 N 秒自动解除 + 推送，验证 why.md#漂移自动恢复r4默认关
- [√] 4.3 在 `config.example.yaml` 中新增 `drift_auto_resume_sec` 注释项

## 5. R6 shutdown 等待窗口
- [√] 5.1 在 `entropy_arb/engine.py` 的 `_run_inner` 中将等待窗口改为 `settle_timeout_sec + 8.0`

## 6. R7 账户读 TTL 缓存
- [√] 6.1 在 `entropy_arb/venue_lighter.py` 中为 `_account()` 实现 3 s TTL 缓存，`fetch_position(force=False)` 透传（force=True 绕过缓存）
- [√] 6.2 在 `entropy_arb/venue_hl.py` 中为 `fetch_position` 增加 `force=False` 兼容参数

## 7. 安全检查
- [√] 7.1 执行安全检查（G9: 无密钥泄露、缓存不缓存敏感凭据、双读逻辑无死循环）

## 8. 测试
- [√] 8.1 在 `tests/test_engine.py` 中实现 R1 双读确认（一致采纳/不一致保持熔断）、R2 顺序（flatten 时 halted 已置位）、R3 钳制、R4 自动恢复测试
- [√] 8.2 在 `tests/test_notifier.py` 中实现 R7 缓存行为测试（TTL 内复用、force 绕过）
- [√] 8.3 运行 `python3 -m pytest tests/` 全部通过

## 9. 文档更新
- [√] 9.1 同步知识库（engine/venue 模块文档、CHANGELOG.md、history/index.md）
- [√] 9.2 迁移方案包至 `helloagents/history/2026-08/`