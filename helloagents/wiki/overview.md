# entropy-arb 项目概述

> 本文件包含项目级别的核心信息。详细的模块文档见 `modules/` 目录。

---

## 1. 项目概述

### 目标与背景
双所永续合约套利机器人：Entropy（Hyperliquid dex "io"）vs Lighter 主网 / Lighter Robinhood / trade.xyz 对冲腿。信号为围绕配置中枢（midline_bps）的固定带宽；taker 双边吃单。一期目标：50U/边 SNDK 实盘验证，流程为 采集数据 → analyze.py 定中枢 → 实盘。

### 范围
- **范围内:** 双所配对套利、库存阶梯、限频熔断、链上对账、数据采集（recorder）、实盘风控（强平/保证金/漂移哨兵）、Telegram 告警
- **范围外:** 二期方向（延迟优化、动态滑点、动态阈值、多所星型拓扑）——规划中，未实现

### 干系人
- **负责人:** 用户（独立项目）

---

## 2. 模块索引

| 模块名称 | 职责 | 状态 | 文档 |
|---------|------|------|------|
| engine | 策略循环、执行、对账、对冲、风控、漂移哨兵 | ✅稳定 | [engine.md](modules/engine.md) |
| venue_hl / venue_lighter | Hyperliquid / zkLighter 适配器（行情/账户/下单/结算） | ✅稳定 | [venue.md](modules/venue.md) |
| book / feeds | 盘口状态与定价；WebSocket 行情源 | ✅稳定 | [book-feeds.md](modules/book-feeds.md) |
| config / recorder / notifier / dashboard | 配置校验、分钟采样、Telegram 告警、仪表盘 | ✅稳定 | [config-recorder-notifier-dashboard.md](modules/config-recorder-notifier-dashboard.md) |

---

## 3. 快速链接
- [技术约定](../project.md)
- [架构设计](arch.md)
- [API 手册](api.md)
- [数据模型](data.md)
- [变更历史](../history/index.md)