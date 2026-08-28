# API 手册

## 概述
机器人对外部交易所的调用面。内部模块间为 Python 方法调用（见各模块文档）。引擎内嵌 Web 状态面板（`web_dashboard` 配置节，默认 127.0.0.1:8787）。

## 认证方式
- Hyperliquid: agent 钱包签名（eth_account + hyperliquid-sdk）
- Lighter: api_private_key 签名；REST 查询（accountOrders）需 authorization 头 = `signer.create_auth_token_with_expiry()`
- 告警通道: Telegram（bot token）/ Server酱（SendKey），任一配置即启用

---

## 接口列表

### Web 状态面板（engine 内嵌 aiohttp）

#### [GET] /
**描述:** 单文件 HTML 状态页（无外部依赖，1s 轮询 /api/status）
**注意:** 默认绑 127.0.0.1；公网暴露需反代 + 认证（页面含账户资金）

#### [GET] /api/status
**描述:** 实时状态 JSON（`webui.status_payload` 组装，无网络）
**响应:** ts/symbol/halted/drift_halted/stale/venue_down/premium_bps/midline_bps/band_low/band_high/premium_history/venues[](name,bid,ask,spread_bps,data_age,position,equity,free,stale,down,limited,unresolved)/directions[](label,premium,hurdle,armed)/net_delta/mtm_pnl/account_delta/total_equity/exp_edge/fill_edge/trades/hedges/consec_errors/recorder_rows/last_trade_ago/uptime_sec/recent_trades[]

### Hyperliquid（venue_hl.py）

#### [POST] /info
**描述:** 行情/账户查询（perpDexs/meta/clearinghouseState/orderStatus 等）
**请求参数:** 见 hyperliquid 官方 API；`dex` 参数区分 Entropy("io") 与 trade.xyz("xyz")

#### [POST] /exchange
**描述:** 下单（IOC limit，settle 同步 + cloid 轮询兜底）

### zkLighter（venue_lighter.py）

#### [GET] /api/v1/orderBooks
**描述:** 市场元数据（market_id、精度、最小下单量、费率）

#### [GET] /api/v1/account
**描述:** 账户状态（equity/available_balance/positions）；positions 行含 `liquidation_price`、`position_value`、`sign`、`position`

#### [GET] /api/v1/accountOrders
**描述:** 按 client_order_indexes 查询订单终态（unresolved 兜底查单）
**请求参数:** `client_order_indexes`（逗号分隔 int64，≤20）、`account_index`；header `authorization`
**响应:**
```json
{"code": 0, "orders": [{"client_order_index": 123, "status": "filled", "filled_base_amount": "5", "filled_quote_amount": "500", ...}]}
```

### Telegram / Server酱（notifier.py）

#### [POST] https://api.telegram.org/bot{token}/sendMessage
**描述:** Telegram 告警（HALT/对冲失败/漂移哨兵/强平告警）
**请求参数:** `chat_id`、`text`（队列化，失败重试 1 次，不阻塞引擎）

#### [POST] https://sctapi.ftqq.com/{sendkey}.send
**描述:** Server酱告警（仅需 SendKey，推送到微信）
**请求参数:** `title`（正文首行）、`desp`（完整消息）；响应 `code=0` 为成功

---

## 错误码
| 来源 | 错误 | 处理 |
|------|------|------|
| 任一 | HTTP 429 | RATE_LIMITED 标记 + rate_limit_pause_sec 暂停 |
| Lighter | 结算超时 | REST accountOrders 查单 3 次；仍 unresolved → 熔断该所 |
| HL | 5xx/超时 | cloid 轮询 orderStatus 直至 settle_timeout |