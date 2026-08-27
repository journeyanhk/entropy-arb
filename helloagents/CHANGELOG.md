# Changelog

本文件记录项目所有重要变更。
格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/),
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增
- 一期实盘加固（2026-08-27）：
  - P0-1：Lighter 腿结算超时后 REST accountOrders 按 client_order_index 查单终态（3 次 × 1s）；unresolved 对该所熔断禁开单，对账采纳链上仓位后解除；强制对账宽限 5s→3s + 重试 3 轮，裸露窗口压到 ~5s
  - P0-2：30s 强平风控循环（mark vs liquidationPx，距离 <10% → 双边 reduce-only 平仓 + HALT + 告警）；_scan 保证金预检（free < notional × 1.2 不发单）
  - P1-3：中枢漂移哨兵（30 分钟均值偏离 (upper+lower)/2 持续 10 分钟 → 停开仓仅放行减仓方向，不自动改 midline）
  - P1-4：默认参数收紧（premium_persist_sec 0.5 / staleness_sec 2.5 / cooldown_sec 1.0 / leg_slippage_bps 30）；config.example.yaml 更新为 50U 参考配置
  - P1-5：is_fresh 数据帧级新鲜度（last_update_ts 判据，默认 60s）；feeds 看门狗超时断连重连强制重新订阅
  - ⑥ Telegram 告警（notifier.py）：HALT、对冲失败、漂移哨兵、强平告警四点推送
- 新模块：entropy_arb/notifier.py、tests/test_notifier.py
- 新增执行配置键 8 个（data_staleness_sec / drift_window_sec / drift_check_sec / drift_halt_sec / drift_band_factor / risk_loop_sec / liquidation_distance_pct / margin_reserve_factor），均带默认值与校验

### 变更
- book.is_fresh 签名增加可选 data_max_age_sec 参数（向后兼容）
- venue start_tasks 签名增加 data_staleness_sec 参数（默认 60.0）
- MinuteRecorder 构造增加 data_staleness_sec 参数（默认 60.0）
- dashboard 状态栏新增 DRIFT 标记；STALE 判定并入数据帧判据

### 修复
- unresolved 腿不再裸奔：Lighter 超时先查单、引擎熔断该所、对账加速确认