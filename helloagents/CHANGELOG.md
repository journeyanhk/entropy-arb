# Changelog

本文件记录项目所有重要变更。
格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/),
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增
- Web 面板前端重构（dev-gen2）：极简扁平化双主题（暗/亮，跟随系统 + 手动切换持久化 localStorage）、表格卡内横向滚动（数据不再截断/顶破边框）、hurdle_breakdown 分解展示、金额千分位、H5 自适应（≤640px 单列 + 触控友好）
- review6 修复（dev-gen2）：
  - miss 归因过滤：`_execute` observe 前剔除 `err`/`unresolved` 腿——miss 池只含"订单到达交易所、IOC 被保护价打空"的市场性事件，网络抖动不再劫持 miss_rate
  - samples 修剪：`observe` 内按时间窗 + `window_n×2` 数量上限裁剪（内存与状态文件有界）
  - miss 告警：status loop 每轮检查，24h miss 率超 `miss_threshold` → log.warning + Server酱通知（每所每小时限频），文案含"上调 protect_cap_bps"行动建议
- 二期③ 滑点动态管理（dev-gen2 分支）：
  - 新增 `entropy_arb/slippage.py` `SlipModel`：按 (venue,symbol) 维护实测滑点，滚动分位（200 笔 ∩ 72h 时间窗）、冷启动回退（<min_samples）、负滑点门槛侧截断、miss-rate 幸存者偏差统计、状态持久化（20 次落盘 + shutdown）
  - 开仓门槛：`_eff_threshold` 对开仓方向加整个往返预期滑点 `(p50_buy+p50_sell)×2×gate_weight`（减仓永不卡）
  - 自适应保护价：每腿 `clamp(p90×protect_mult, floor, cap)`，首周 `protect_cap_bps:30` 只许收紧；trades.csv 加 `dyn_protect_buy/sell_bps` shadow 对照列
  - webui：方向门槛 `hurdle_breakdown`（base/inventory/funding/slip_gate 分解）+ venues miss_rate
  - 新增 config 节 `slippage`（enabled/state_file/min_samples/window_n/window_hours/gate_weight/protect_mult/protect_floor_bps/protect_cap_bps/miss_threshold）
  - funding 口径归一化：两所 funding 均为 8h 口径（HL 每小时支付其 1/8，查证 HL/Lighter 官方文档），统一归一化为 bps/hour（÷8）；`_funding_cost_bps` 成本 = 不利侧小时费率 × `funding_hold_hours`（新增配置，默认 4.0）——修复原实现隐含"持仓满 8h"导致短期成本高估 8 倍
  - signal_age 传值：`_scan` 返回 `(buy, sell, plan, armed_ts)` 四元组，`_execute` 用本轮武装时间戳计算 age，修复连续边际下 age 跨笔累积
- P1 三项（dev 分支）：
  - P1-1：`_scan` 数据不可信守卫（stale/未就绪/down/限频熔断）清除 armed 状态，恢复后须重新走满持续性闸门；锁占用与预算耗尽保留武装
  - P1-2：执行遥测打点——trades.csv 新增 5 列（buy/sell_lat_ms、slip_buy/sell_bps、signal_age_sec；avg_px 缺失留空不回填）；`tools/analyze.py --trades` 输出延迟/滑点/信号年龄/滑点税分布
  - P1-3：funding 方向过滤——venue 增 `funding_bps_8h`（HL metaAndAssetCtxs / Lighter funding-rates，×1e4 转 bps，30s 随余额轮询）；`_eff_threshold` 仅对开仓方向加 `min(不利funding×0.5, funding_cap_bps)`（默认 5），减仓永不 gate
- 修复：webui/dashboard 方向门槛与引擎口径对齐（`_eff_threshold` 已含库存+funding，去重复 inv_add）；webui venues 卡片新增 funding 列
- Web 状态面板（方案 B，dev 分支）：引擎内嵌 aiohttp HTTP 服务，`GET /` 单文件 HTML（深色卡片、premium canvas 折线、1s 轮询、状态徽章/双所/信号/会话/最近成交），`GET /api/status` JSON（`webui.status_payload` 无网络可测）；配置节 `web_dashboard`（enabled/host/port，默认 127.0.0.1:8787）；绑定失败仅告警不中断交易
- Server酱告警通道：notifier 重构为多通道（TelegramChannel + ServerChanChannel），`SERVERCHAN_SENDKEY` 一行配置即用，与 Telegram 并存，队列/重试/丢弃行为不变
- 兜底强平单使用最宽滑点（review4 P0）：新增 `hedge_force_close_slip_bps`（默认 200）；此前 `_hedge_once(0.0)` 零滑点保护价钉死盘口最优价，恰是全流程最不易成交的一单
- 裸露敞口有界快速重试（review3 P0-1）：`_hedge` 递增滑点重试（20→50→100 bps、0.5 s 间隔）＋`hedge_force_close_timeout_sec`（5 s）超时后对价最后一搏；残留低于可对冲最小量 carry，否则 HALT+告警——单腿失败尾部损失从"未知"变"有界"
- 新增执行配置键 3 个（hedge_retry_slips_bps 列表类型 / hedge_retry_interval_sec / hedge_force_close_timeout_sec）；schema 新增 list 类型校验
- 一期修复复查轮（review2 R1–R7，2026-08-27）：
  - R1：force 对账双读确认（间隔 1 s、两次一致才采纳解熔，最多 3 轮；不一致不计 venue-down 惩罚）
  - R2：强平路径先置 `halted` 再 flatten，杜绝平仓与停机之间信号回加仓位
  - R3：drift 停机下减仓单名义钳制到 `|entropy.position| × buy腿ask`，防穿零反向开仓
  - R4：漂移回带内日志明确 "still DRIFT-HALTED"；新增 `drift_auto_resume_sec` 配置（默认 0 = 仅人工重启）
  - R6：shutdown 等待窗口 `settle_timeout_sec + 8`（覆盖最坏腿时长）
  - R7：Lighter `_account()` 3 s TTL 缓存（force 对账绕过）；`fetch_position(force=)` 接口统一
- 新增执行配置键 1 个（drift_auto_resume_sec，默认 0.0）

### 变更
- `fetch_position` 签名统一增加 `force: bool = False`（HL 侧为兼容参数）

### 修复
- 强平竞态（先平仓后停机）、force 解熔竞态（单次读数）、drift 减仓穿零、误导性漂移日志
- `_status_loop` 状态行日志占位符与参数不匹配（DRIFT 标记缺 `%s`，运行时报 `not all arguments converted`）；新增静态守卫测试防同类回归
- 保证金预检跳过日志补充诊断信息（venue 名 + free/need 数值），便于定位可用余额不足的根因
- 保证金预检按实际杠杆计算所需保证金（`notional / margin_leverage × margin_reserve_factor`，默认 1x）；新增 `margin_leverage` 配置键（默认 1.0，>0 校验）——此前按 1x 假设会把 10x 账户的可用余额过度拦截 12 倍

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