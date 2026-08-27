# API 手册

## 概述
机器人对外部交易所的调用面。内部模块间为 Python 方法调用（见各模块文档）。

## 认证方式
- Hyperliquid: agent 钱包签名（eth_account + hyperliquid-sdk）
- Lighter: api_private_key 签名；REST 查询（accountOrders）需 authorization 头 = `signer.create_auth_token_with_expiry()`

---

## 接口列表

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

### Telegram（notifier.py）

#### [POST] https://api.telegram.org/bot{token}/sendMessage
**描述:** 告警推送（HALT/对冲失败/漂移哨兵/强平告警）
**请求参数:** `chat_id`、`text`（队列化，失败重试 1 次，不阻塞引擎）

---

## 错误码
| 来源 | 错误 | 处理 |
|------|------|------|
| 任一 | HTTP 429 | RATE_LIMITED 标记 + rate_limit_pause_sec 暂停 |
| Lighter | 结算超时 | REST accountOrders 查单 3 次；仍 unresolved → 熔断该所 |
| HL | 5xx/超时 | cloid 轮询 orderStatus 直至 settle_timeout |