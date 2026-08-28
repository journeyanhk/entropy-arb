# webui 模块

## 目的
引擎内嵌的 Web 状态面板：单文件 HTML 页面 + JSON 状态端点。

## 模块概述
- **职责:** `status_payload(eng)` 从引擎对象组装状态 JSON（无网络、可单测）；`PAGE_HTML` 单文件页面（深色卡片、canvas premium 折线、1s fetch 轮询、无外部依赖）
- **状态:** ✅稳定
- **最后更新:** 2026-08-28

## 规范

### 需求: Web 实时面板（方案 B）
**模块:** engine / webui
`_web_loop` 用 aiohttp.web 起内嵌服务：`GET /`（PAGE_HTML）、`GET /api/status`（status_payload JSON）。配置节 `web_dashboard`（enabled/host/port，默认 127.0.0.1:8787）。绑定失败仅 log warning，不中断交易循环。

#### 场景: 服务模式下随时查看状态
- 前提: systemd 服务运行（--no-dashboard）
- 预期结果: 浏览器访问 http://127.0.0.1:8787 实时看到状态徽章、双所、信号 vs 带宽折线、会话数字、最近成交

## API接口
- `status_payload(eng) -> dict` 状态 JSON 组装
- `PAGE_HTML` 页面常量

## 依赖
- engine（读取状态）、aiohttp.web（engine 内嵌）

## 变更历史
- [202608280833_web-dashboard-serverchan](../../history/2026-08/202608280833_web-dashboard-serverchan/) - Web 面板 + Server酱（dev 分支）