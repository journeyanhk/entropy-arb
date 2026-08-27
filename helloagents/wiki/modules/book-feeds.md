# book / feeds 模块

## 目的
盘口状态与定价（book），以及两家交易所的 WebSocket 行情源（feeds）。

## 模块概述
- **职责:** OrderBook（快照+diff 维护、新鲜度判定）；plan_arb 套利定价；LighterBookFeed / HLBookFeed 行情消费与重连
- **状态:** ✅稳定
- **最后更新:** 2026-08-27

## 规范

### 需求: 数据帧级新鲜度（P1-5）
**模块:** book / feeds
`is_fresh(max_age_sec, data_max_age_sec=None)`：连接心跳（alive_ts）+ 数据帧（last_update_ts，仅 apply_lighter/apply_hl 刷新）双判据；数据帧超时判盲。`feeds._data_watch`：每 5s 检查，`last_update_ts` 超过 `data_staleness_sec` → 关闭 ws，由外层重连循环强制重新订阅快照。

#### 场景: 盘口数据长时间不更新
- 前提: 连接正常但无数据帧（股票永续盘外时段）
- 预期结果: 判为盲 + 主动重连；引擎不开单、不对冲、不采样

## API接口
- `OrderBook.is_fresh(max_age_sec, data_max_age_sec=None) -> bool`
- `_data_watch(name, book, ws, data_staleness_sec)` 看门狗协程

## 数据模型
- `OrderBook.last_update_ts` 最近数据帧时间（新判据）
- `OrderBook.alive_ts` 最近任意 ws 帧时间（连接心跳）

## 依赖
- 无内部依赖；被 engine/recorder 调用

## 变更历史
- [202608271001_phase1-risk-hardening](../../history/2026-08/202608271001_phase1-risk-hardening/) - 一期实盘加固