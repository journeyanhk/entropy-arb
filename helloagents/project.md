# 项目技术约定

---

## 技术栈
- **核心:** Python 3.14 / asyncio / aiohttp / websockets
- **配置:** YAML（config.yaml，即策略）+ .env（仅密钥）
- **SDK（仅实盘）:** hyperliquid-python-sdk、lighter-sdk（延迟导入）

---

## 开发约定
- **代码规范:** PEP 8 风格；`from __future__ import annotations`；类型注解完整
- **命名约定:** 下划线命名；venue 键 `entropy`/`hedge`；方向键 `sell_entropy`/`buy_entropy`
- **注释规范:** 不添加多余注释；docstring 中英双语（英文为主）

---

## 错误与日志
- **策略:** 引擎主循环均 try/except 包裹不退出；启动失败（凭据/市场缺失）抛 RuntimeError 由 main.py 输出
- **日志:** logging 按模块命名（engine/hl/lighter/feeds/recorder/notifier）；CRITICAL 用于停机级事件

---

## 测试与流程
- **测试:** `python3 -m pytest tests/`；每个模块可独立运行 `python3 tests/test_xxx.py`
- **提交:** 不自动提交；由用户显式要求