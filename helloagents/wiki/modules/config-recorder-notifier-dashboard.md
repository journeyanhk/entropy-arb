# config / recorder / notifier / dashboard 模块

## 目的
配置加载（config）、分钟数据采集（recorder）、Telegram 告警（notifier）、终端仪表盘（dashboard）。

## 模块概述
- **职责:** config.yaml 校验加载 + .env 密钥；1 分钟盘口聚合采样；告警队列推送；Rich 状态展示
- **状态:** ✅稳定
- **最后更新:** 2026-08-27

## 规范

### 需求: 一期风险配置（P0-2/P1-3/P1-5）
**模块:** config
execution 段新增 8 键（均带默认值）：`data_staleness_sec: 60`、`drift_window_sec: 1800`、`drift_check_sec: 60`、`drift_halt_sec: 600`、`drift_band_factor: 1.0`、`risk_loop_sec: 30`、`liquidation_distance_pct: 10`、`margin_reserve_factor: 1.2`。默认值收紧：`premium_persist_sec: 0.5`、`staleness_sec: 2.5`、`cooldown_sec: 1.0`、`leg_slippage_bps: 30`。校验：`liquidation_distance_pct ∈ (0,100)`、`margin_reserve_factor >= 1.0`。

#### 场景: 加载一期配置
- 预期结果: 新键生效；非法值启动报错

### 需求: Telegram / Server酱 告警（⑥ + review4）
**模块:** notifier
多通道：`TelegramChannel`（bot token + chat id）、`ServerChanChannel`（`SERVERCHAN_SENDKEY` 一行配置）。`Notifier.from_env()` 聚合启用的通道；队列化、失败重试 1 次、队列满（64）丢弃；无任何凭据时静默 no-op。engine 在 HALT、对冲失败、漂移哨兵、强平告警、未削平停机等点 `send()`。

#### 场景: 停机或风险事件
- 预期结果: 配置的通道全部推送；发送失败只记日志不阻塞引擎

### 需求: 数据帧新鲜度采样（P1-5）
**模块:** recorder
`MinuteRecorder` 新增 `data_staleness_sec` 参数，`sample()` 用双判据判断两本盘口新鲜度，数据盲时段不采样。

### 需求: 可观测性
**模块:** dashboard
状态栏新增 DRIFT（漂移停机）标记；venues 面板 STALE 判定并入数据帧判据；unresolved 熔断显示为 RATE-LTD。

## 依赖
- config → dotenv/yaml；recorder → book；notifier → aiohttp；dashboard → engine + rich

## 变更历史
- [202608271001_phase1-risk-hardening](../../history/2026-08/202608271001_phase1-risk-hardening/) - 一期实盘加固