# 任务清单: Web 状态面板 + Server酱告警（方案 B）

目录: `helloagents/plan/202608280833_web-dashboard-serverchan/`

---

## 1. 配置层
- [√] 1.1 在 `entropy_arb/config.py` 中新增 `web_dashboard` 节（enabled/host/port，默认 true/127.0.0.1/8787）+ schema
- [√] 1.2 在 `config.example.yaml` 中新增 `web_dashboard` 节与注释
- [√] 1.3 在 `.env.example` 中新增 `SERVERCHAN_SENDKEY`

## 2. Web 状态服务
- [√] 2.1 新建 `entropy_arb/webui.py`：单文件 HTML 页面（深色卡片、canvas premium 折线、fetch 轮询 1s、状态徽章/双所/信号/会话/最近成交）与 `status_payload(eng)` JSON 组装
- [√] 2.2 在 `entropy_arb/engine.py` 中实现 `_web_loop`（aiohttp.web：`/` 与 `/api/status`；绑定失败仅告警不中断交易；shutdown cleanup），接入 `_run_inner` 任务列表，验证 why.md#Web-实时面板-服务模式下随时查看状态

## 3. Server酱告警
- [√] 3.1 在 `entropy_arb/notifier.py` 中重构为多通道：`TelegramChannel` + `ServerChanChannel`（POST `https://sctapi.ftqq.com/{sendkey}.send`，title=首行/desp=正文），`Notifier.from_env()` 聚合，验证 why.md#Server酱告警-配置-serverchan_sendkey

## 4. 安全检查
- [√] 4.1 执行安全检查（G9: key 仅存 .env、页面默认不暴露公网、异常不中断交易）

## 5. 测试
- [√] 5.1 在 `tests/test_config.py` 中实现 web_dashboard 解析/默认/校验
- [√] 5.2 在 `tests/test_notifier.py` 中实现 ServerChan channel 启用/禁用/请求构造
- [√] 5.3 在 `tests/test_engine.py` 中实现 `status_payload` 字段完整性测试
- [√] 5.4 运行 `python3 -m pytest tests/` 全部通过

## 6. 文档更新
- [√] 6.1 同步知识库（wiki api/模块文档、CHANGELOG.md、history/index.md）
- [√] 6.2 迁移方案包至 `helloagents/history/2026-08/`
- [√] 6.3 提交并推送 dev 分支到远程