# 任务清单: P1 三项（armed 重置 / 延迟滑点打点 / funding 过滤）

目录: `helloagents/plan/202608280929_p1-triad/`

---

## 1. P1-1 armed 重置
- [√] 1.1 在 `entropy_arb/engine.py` 的 `_scan` 中对四个守卫分支（不新鲜/未就绪/venue down/限频熔断）清除 `self._armed[dkey]`；锁占用与预算耗尽保留，验证 why.md#armed-在数据不可信时重置p1-1-盘口断流后恢复

## 2. P1-2 延迟/滑点打点
- [√] 2.1 在 `entropy_arb/engine.py` 的 `_execute` 中实现 `_timed` 包装（send_taker 返回 dict 加 latency_ms）、slip_buy_bps/slip_sell_bps（avg_px 缺失为 None）、signal_age_sec；`CSV_HEADER` 增加 5 列并贯通 `_log_csv`
- [√] 2.2 在 `tools/analyze.py` 中实现 `--trades` 分析（延迟/滑点/exp-fill 差值/age 的 p50/p90/p99，防御式解析）
- [√] 2.3 在 `tests/test_engine.py` 中实现 CSV 行宽与 header 一致守卫测试

## 3. P1-3 funding 过滤
- [√] 3.1 在 `entropy_arb/venue_hl.py` 中实现 `fetch_funding`（metaAndAssetCtxs + dex，×1e4 bps）与 `funding_bps_8h` 字段
- [√] 3.2 在 `entropy_arb/venue_lighter.py` 中实现 `fetch_funding`（/api/v1/funding-rates 按 market_id）与 `funding_bps_8h` 字段
- [√] 3.3 在 `entropy_arb/engine.py` 中实现 `_funding_cost_bps`、`_direction_reduces`，`_eff_threshold` 对加仓方向加 `min(cost×0.5, funding_cap_bps)`；`_balance_loop` 轮询 funding
- [√] 3.4 在 `entropy_arb/config.py` 中新增 `funding_cap_bps`（默认 5.0）+ schema/校验；`config.example.yaml` 同步
- [√] 3.5 修复 `entropy_arb/webui.py` 方向门槛重复加 inv_add；`entropy_arb/dashboard.py` 门槛与引擎口径对齐（含 funding）；webui venues 卡片加 funding 字段

## 4. 测试
- [√] 4.1 在 `tests/test_engine.py` 中实现 armed 重置测试、funding 门槛测试（含减仓不受影响、funding 缺失不加门槛）
- [√] 4.2 在 `tests/test_config.py` 中实现 funding_cap_bps 默认/覆盖/校验
- [√] 4.3 运行 `python3 -m pytest tests/` 全部通过

## 5. 文档与提交
- [√] 5.1 同步知识库（engine/venue/webui 模块文档、api/data、CHANGELOG.md、history/index.md）
- [√] 5.2 迁移方案包至 `helloagents/history/2026-08/`
- [√] 5.3 提交并推送 dev 分支到远程