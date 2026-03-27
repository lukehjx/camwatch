# camwatch - 办公室 AI 巡查系统

基于 AI 视觉分析的办公室智能巡查系统，自动截帧、分析人员在场、灯光状态、设备状态，并通过企业微信推送报告。

## 功能

- 📷 多摄像头截帧（支持 RTSP 流）
- 🤖 AI 视觉分析（人员/灯光/设备状态）
- 📶 WiFi 探针（SSH 路由器 ARP 表，实时在线人数）
- 📊 Web 看板（仪表盘/历史记录/统计）
- 🔔 企业微信 Webhook 推送
- ⏰ 定时自动巡查（crontab）

## 技术栈

- Python + Flask
- AI 后端：OpenAI-compatible API（token.sidex.cn）
- WiFi 探针：H3C 路由器 SSH（display arp）
- 数据库：SQLite

## 部署

```bash
pip install flask requests pillow
python app.py
```

服务默认运行在 8088 端口。

## WiFi 探针

通过 SSH 登录 H3C 路由器获取 ARP 表，直接读取 WiFi 网段在线设备数，辅助 AI 判断人员在场情况。

---
*由旺财 AI 开发于 2026 年 3 月*
