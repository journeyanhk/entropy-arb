# 变更提案: Web 状态面板 + Server酱告警（方案 B）

## 需求背景

systemd 服务模式（`--no-dashboard`）下无终端看不了 Rich dashboard。方案 B：引擎内嵌轻量 HTTP 端点输出 JSON 状态，前端单文件页面实时渲染（盘口、溢价折线、仓位、权益、最近成交、状态徽章），1s 轮询。同时接入 Server酱告警（只需 `SERVERCHAN_SENDKEY` 配置），与现有 Telegram 并存，配了哪个用哪个。

## 变更内容

1. 新增配置节 `web_dashboard`（enabled/host/port，默认 127.0.0.1:8787）
2. 引擎内嵌 aiohttp web server：
   - `GET /` 单文件 HTML 页面（深色卡片布局，canvas 手绘 premium 折线，无外部依赖）
   - `GET /api/status` JSON（venues/信号/会话/最近成交/premium 历史）
3. notifier 重构为多通道：TelegramChannel + ServerChanChannel（`https://sctapi.ftqq.com/{sendkey}.send`），行为不变（队列/重试/满丢弃）
4. `.env.example` 加 `SERVERCHAN_SENDKEY`

## 影响范围

- **文件:** `entropy_arb/webui.py`（新增）、`entropy_arb/engine.py`、`entropy_arb/notifier.py`、`entropy_arb/config.py`、`config.example.yaml`、`.env.example`、`tests/test_config.py`、`tests/test_engine.py`、`tests/test_notifier.py`

## 核心场景

### 需求: Web 实时面板
**模块:** engine / webui

#### 场景: 服务模式下随时查看状态
- 浏览器访问 http://127.0.0.1:8787
- 预期结果: 状态徽章（RUNNING/DRIFT/HALTED/STALE/DOWN）、双所 bid/ask/仓位/权益/free、premium vs 带宽折线、最近成交表实时刷新

#### 场景: 页面不可用时不影响交易
- 端口被占用 / 绑定失败
- 预期结果: log warning，交易循环不受影响

### 需求: Server酱告警
**模块:** notifier

#### 场景: 配置 SERVERCHAN_SENDKEY
- 引擎停机/强平/漂移/对冲失败
- 预期结果: 推送 `https://sctapi.ftqq.com/{sendkey}.send`；失败重试一次；无凭据静默

## 风险评估

- **风险:** 页面暴露账户数据——默认绑 127.0.0.1，文档提示 nginx 反代 + basic auth
- **风险:** web server 异常影响交易——独立 task，启动失败仅告警不中断交易循环
- **风险:** Server酱 key 泄露——仅存 .env（gitignore），不打印