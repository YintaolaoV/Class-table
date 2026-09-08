# V课表

个人静态课表 PWA：<https://class.vincentlee.asia/>

前端仍可完全离线运行，课程和设置默认保存在浏览器 `localStorage`。在“课表设置”中填写服务器分配的用户 ID 与同步密钥后，可将同一用户的课表保存到服务器并在多台设备间拉取。

## 服务器同步与课前推送

`server/` 是独立的 Flask + SQLite API。它保存用户课表快照、推送订阅和已发送提醒记录；VAPID 配置完成后，会按课程实际作息在上课前 20 分钟发送 Web Push。iPhone/iPad 需要先将 PWA 添加到主屏幕并允许通知。

后端部署文件包括 `server/app.py`、`server/requirements.txt`、`server/class-table-api.service` 和 `server/compose.yaml`。用户 ID 不能单独作为密码，必须配合同步密钥。

## 数据安全

Service Worker 缓存只保存程序文件，用户数据仍由 localStorage 和服务器课表快照分别保存。更新前端或 Service Worker 不会清除本地课表；JSON 导出仍可作为最终备份。
