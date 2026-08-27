# 数据模型

## 概述
无数据库；运行时内存状态 + CSV 落盘。

---

## CSV 文件

### logs/minutes.csv（recorder）
| 字段 | 说明 |
|------|------|
| minute_ts / time_utc | 分钟时间戳 |
| entropy_bid/ask, hedge_bid/ask | 当分钟最后新鲜采样盘口 |
| premium_open/high/low/close/mean/std_bps | 中间价溢价统计（不含费） |
| sell_edge_mean/max_bps | 卖出 entropy 可成交溢价 |
| buy_edge_mean/max_bps | 买入 entropy 可成交溢价 |
| samples | 有效采样数 |

### logs/trades.csv（engine）
| 字段 | 说明 |
|------|------|
| ts, direction, buy_venue, sell_venue, qty | 执行基本信息 |
| buy_limit, sell_limit | 计划保护价 |
| buy_notional, sell_notional | 双边名义 |
| exp_edge_usd, gross_edge_usd | 预期/毛边缘 |
| marginal_premium_bps, midline_bps, inv_add_bps | 边际溢价/中枢/库存附加 |
| ok, buy_fill, sell_fill, buy_status, sell_status, fill_edge_usd | 成交结果 |

---

## 运行时状态

### OrderBook
| 字段 | 说明 |
|------|------|
| bids / asks | Dict[price, size] |
| ready | 已收到快照 |
| last_update_ts | 最近一次数据帧（快照/diff）时间 |
| alive_ts | 最近一次任意 ws 帧时间 |

### Venue（HLVenue / LighterVenue）
| 字段 | 说明 |
|------|------|
| position / cash / volume_usd | 本地持仓/现金/累计成交额 |
| equity / free / start_equity | 账户权益/可用/初始权益 |
| cap_usd / fee_bps / orders_per_min | 持仓帽/费率/每分钟预算 |
| last_traded_ts | 最近成交时间（对账宽限依据） |

### Engine 风控状态
| 字段 | 说明 |
|------|------|
| _venue_limited_until | 限频熔断至（秒级自动恢复） |
| _venue_unresolved_until | unresolved 熔断至（对账成功才恢复） |
| _drift_halted | 漂移哨兵停机（人工重启恢复） |
| halted | 全局停机（连续错误/强平触发） |