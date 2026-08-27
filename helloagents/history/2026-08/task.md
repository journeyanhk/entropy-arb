# 任务清单: 一期实盘加固（P0/P1 六项）

目录: `helloagents/plan/202608271001_phase1-risk-hardening/`

---

## 1. P0-1 unresolved 即查即修
- [√] 1.1 在 `entropy_arb/venue_lighter.py` 中实现 `_query_order(coi)`（REST accountOrders + auth token + 防御式解析），验证 why.md#unresolved-即查即修p0-1-Lighter-腿结算超时
- [√] 1.2 在 `entropy_arb/venue_lighter.py` 中改造 `send_taker` 的 TimeoutError 分支：先查单（3 次 × 1s）再决定是否 unresolved，依赖任务 1.1
- [√] 1.3 在 `entropy_arb/engine.py` 中实现 unresolved 熔断（`_venue_unresolved_until`）、`_venue_limited` 合并判断、`_execute` 设置熔断，验证 why.md#unresolved-即查即修p0-1-Lighter-腿结算超时
- [√] 1.4 在 `entropy_arb/engine.py` 中实现 `_reconcile_positions/_reconcile_venue` 的 force 模式（宽限 3s、重试 3 轮）与对账成功清除熔断，验证 why.md#unresolved-即查即修p0-1-对账加速

## 2. P0-2 强平/保证金风控
- [√] 2.1 在 `entropy_arb/venue_hl.py` 中实现 `fetch_risk()`（clearinghouseState markPx/liquidationPx）
- [√] 2.2 在 `entropy_arb/venue_lighter.py` 中实现 `fetch_risk()`（liquidation_price + mark 推导）
- [√] 2.3 在 `entropy_arb/engine.py` 中实现 `_risk_loop`、`_liq_distance`、`_flatten_all`、HALT + 告警，验证 why.md#强平保证金风控p0-2-接近强平价
- [√] 2.4 在 `entropy_arb/engine.py` 的 `_scan` 中实现保证金预检（free < notional × margin_reserve_factor 跳过），验证 why.md#强平保证金风控p0-2-保证金不足

## 3. P1-3 中枢漂移哨兵
- [√] 3.1 在 `entropy_arb/engine.py` 中实现 `_drift_loop`（采样 deque + 30min 均值 + 10min 持续）+ `_drift_halted` 下 `_scan` 只放行减仓方向，验证 why.md#中枢漂移哨兵p1-3-溢价中枢漂移

## 4. P1-4 默认参数
- [√] 4.1 在 `entropy_arb/config.py` 中调整默认值（persist 0.5 / staleness 2.5 / cooldown 1.0）并新增 8 个 execution 配置键（含 schema 校验）
- [√] 4.2 在 `config.example.yaml` 中同步新键与默认值（floor_frac 0.5、persist 0.5、staleness 2.5、cooldown 1.0、leg_slippage 30、hedge_slippage 20）

## 5. P1-5 数据帧级新鲜度
- [√] 5.1 在 `entropy_arb/book.py` 中扩展 `is_fresh` 增加 `data_max_age_sec` 判据
- [√] 5.2 在 `entropy_arb/feeds.py` 中为两个 feed 实现 `_data_watch` 看门狗（超时断连重连重新订阅）
- [√] 5.3 在 `entropy_arb/venue_hl.py` / `entropy_arb/venue_lighter.py` / `entropy_arb/engine.py` / `entropy_arb/recorder.py` 中贯通 `data_staleness_sec` 参数

## 6. Telegram 告警
- [√] 6.1 新建 `entropy_arb/notifier.py`（队列 + worker + 失败重试 + 队列满丢弃）
- [√] 6.2 在 `entropy_arb/engine.py` 中挂接 notifier：HALT、对冲失败、漂移哨兵、强平告警四点
- [√] 6.3 在 `.env.example` 中新增 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID

## 7. 可观测性
- [√] 7.1 在 `entropy_arb/dashboard.py` 与 `entropy_arb/engine.py` 状态行新增 DRIFT 标记与 unresolved 熔断标记

## 8. 安全检查
- [√] 8.1 执行安全检查（G9: 不泄露密钥/token、查单失败保守回退、平仓 reduce-only、无破坏性操作）

## 9. 测试
- [√] 9.1 在 `tests/test_book.py` 中实现 is_fresh 数据陈旧测试
- [√] 9.2 在 `tests/test_engine.py` 中实现 margin 预检、漂移哨兵减仓放行、清算距离测试
- [√] 9.3 在 `tests/test_notifier.py` 中实现 notifier 行为测试
- [√] 9.4 运行 `python3 -m pytest tests/` 全部通过

## 10. 文档更新
- [√] 10.1 同步知识库（wiki 模块文档、overview、project.md、CHANGELOG.md、history/index.md）
- [√] 10.2 迁移方案包至 `helloagents/history/2026-08/`