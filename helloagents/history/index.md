# 变更历史索引

本文件记录所有已完成变更的索引，便于追溯和查询。

---

## 索引

| 时间戳 | 功能名称 | 类型 | 状态 | 方案包路径 |
|--------|----------|------|------|------------|
| 202608271001 | phase1-risk-hardening | 修复/加固 | ✅已完成 | [链接](2026-08/202608271001_phase1-risk-hardening/) |
| 202608271034 | review2-fixes | 修复/加固 | ✅已完成 | [链接](2026-08/202608271034_review2-fixes/) |
| 202608271754 | p0-hedge-bounded-retry | 修复/加固 | ✅已完成 | [链接](2026-08/202608271754_p0-hedge-bounded-retry/) |
| 202608271932 | p0-forceclose-slip | 修复/加固 | ✅已完成 | [链接](2026-08/202608271932_p0-forceclose-slip/) |
| 202608280833 | web-dashboard-serverchan | 功能 | ✅已完成 | [链接](2026-08/202608280833_web-dashboard-serverchan/) |
| 202608280929 | p1-triad | 修复/功能 | ✅已完成 | [链接](2026-08/202608280929_p1-triad/) |
| 202608281018 | review5-predeploy | 修复/加固 | ✅已完成 | [链接](2026-08/202608281018_review5-predeploy/) |
| 202608281100 | devgen2-slippage | 功能 | ✅已完成 | [链接](2026-08/202608281100_devgen2-slippage/) |

---

## 按月归档

### 2026-08

- [202608271001_phase1-risk-hardening](2026-08/202608271001_phase1-risk-hardening/) - 一期实盘加固：unresolved 即查即修、强平/保证金风控、漂移哨兵、数据帧新鲜度、Telegram 告警
- [202608271034_review2-fixes](2026-08/202608271034_review2-fixes/) - 复查修复轮：force 双读确认、强平先停机、drift 减仓钳制、漂移自动恢复、shutdown 窗口、账户缓存
- [202608271754_p0-hedge-bounded-retry](2026-08/202608271754_p0-hedge-bounded-retry/) - P0-1 裸露敞口有界快速重试：递增滑点、超时对价强平、残留 carry/停机分级
- [202608271932_p0-forceclose-slip](2026-08/202608271932_p0-forceclose-slip/) - P0 强平单滑点修复：兜底单用最宽保护价（200 bps），零滑点钉死盘口价反而最不易成交
- [202608280833_web-dashboard-serverchan](2026-08/202608280833_web-dashboard-serverchan/) - Web 状态面板（引擎内嵌 HTML+JSON）+ Server酱告警通道（dev 分支）
- [202608280929_p1-triad](2026-08/202608280929_p1-triad/) - P1 三项：armed 重置、延迟/滑点打点（trades.csv 5 新列 + analyze --trades）、funding 方向过滤（dev 分支）
- [202608281018_review5-predeploy](2026-08/202608281018_review5-predeploy/) - review5 部署前修复：funding 归一化为 bps/hour × hold_hours、signal_age 传值（dev 分支）
- [202608281100_devgen2-slippage](2026-08/202608281100_devgen2-slippage/) - 二期③滑点动态管理：SlipModel 开仓往返门槛 + 自适应保护价 + miss率 + 状态持久化（dev-gen2 分支）