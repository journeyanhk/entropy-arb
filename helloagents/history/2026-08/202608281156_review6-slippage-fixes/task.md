# 任务清单: review6 修复（miss 归因过滤 / samples 修剪 / miss 告警）

目录: `helloagents/plan/202608281156_review6-slippage-fixes/`

---

## 1. A: miss 归因过滤
- [√] 1.1 在 `entropy_arb/engine.py` 的 `_execute` 中，observe 前过滤：`info.get("unresolved") or info.get("err") is not None` 的腿跳过，验证 why.md#miss-池只含市场性失败a

## 2. B: samples 修剪
- [√] 2.1 在 `entropy_arb/slippage.py` 的 `observe` 中，append 后按时间窗（window_hours）与数量上限（window_n×2）修剪 samples，验证 why.md#samples-有界b

## 3. C: miss 告警
- [√] 3.1 在 `entropy_arb/engine.py` 中实现 `_check_miss_alert`（status loop 每轮检查，miss_rate > miss_threshold → log.warning + notify，每小时限频，文案含行动建议），验证 why.md#miss-超阈值告警c

## 4. 测试
- [√] 4.1 在 `tests/test_slippage.py` 中实现 samples 时间窗修剪、数量上限修剪测试
- [√] 4.2 在 `tests/test_engine.py` 中实现 observe 过滤（err/unresolved 不计数、filled 计数）、miss 告警触发与限频测试
- [√] 4.3 运行 `python3 -m pytest tests/` 全部通过

## 5. 文档与提交
- [√] 5.1 同步知识库（CHANGELOG.md、slippage/engine 模块文档、history/index.md）
- [√] 5.2 迁移方案包至 `helloagents/history/2026-08/`
- [√] 5.3 提交并推送 dev-gen2 分支到远程