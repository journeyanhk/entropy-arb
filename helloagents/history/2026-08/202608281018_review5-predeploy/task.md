# 任务清单: review5 部署前修复（funding 口径 / signal_age 传值）

目录: `helloagents/plan/202608281018_review5-predeploy/`

---

## 1. funding 口径归一化
- [√] 1.1 在 `entropy_arb/venue_hl.py` 中改 `fetch_funding`：`funding_bps_h = funding × 1e4 / 8`（8h 口径 ÷8 = bps/hour），字段改名 `funding_bps_h`
- [√] 1.2 在 `entropy_arb/venue_lighter.py` 中同样改（rate × 1e4 / 8）
- [√] 1.3 在 `entropy_arb/engine.py` 中改 `_funding_cost_bps`：不利侧 `funding_bps_h` 之和 × `cfg.funding_hold_hours`
- [√] 1.4 在 `entropy_arb/config.py` 中新增 `funding_hold_hours`（默认 4.0，>0 校验）+ schema；`config.example.yaml` 同步
- [√] 1.5 在 `entropy_arb/webui.py` 中更新字段引用 `funding_bps_h`

## 2. signal_age 传值
- [√] 2.1 在 `entropy_arb/engine.py` 中 `_scan` 返回 `(buy, sell, plan, armed_ts)` 四元组；`_evaluate`/`_execute_locked` 解包并传 `armed_ts` 至 `_execute`，`_execute` 用传入值计算 signal_age

## 3. 测试
- [√] 3.1 更新 `tests/test_engine.py`：四元组解包（run_scan 相关测试）；funding 小时口径折算（hold_hours 生效、÷8 正确）；signal_age 传值
- [√] 3.2 在 `tests/test_config.py` 中实现 funding_hold_hours 默认/覆盖/校验
- [√] 3.3 运行 `python3 -m pytest tests/` 全部通过

## 4. 文档与提交
- [√] 4.1 同步知识库（engine/venue 模块文档、CHANGELOG.md、history/index.md）
- [√] 4.2 迁移方案包至 `helloagents/history/2026-08/`
- [√] 4.3 提交并推送 dev 分支到远程