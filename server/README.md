# 课表同步与 Web Push API

该目录是静态课表的独立后端，不参与前端构建。服务使用 SQLite 保存每个用户的课表快照、推送订阅和已发送提醒记录。

## 配置

复制 `.env.example` 为 `.env`，填写随机的 `CLASS_TABLE_BOOTSTRAP_KEY`。启用 Web Push 时还需要配置 VAPID 公钥、私钥和 subject；未配置 VAPID 时，课表同步仍然可用。

## 部署

```powershell
docker compose up -d --build
```

服务默认只监听 `127.0.0.1:18110`，由 Caddy 代理到 `/class-table/`。用户通过管理员生成的同步密钥访问，不使用用户 ID 单独鉴权。

## 用户初始化

调用 `POST /v1/users/provision`，请求头带 `X-Bootstrap-Key`，请求体为 `{ "userId": "Admin" }`。响应中的 `syncKey` 只显示一次，应交给对应用户保存。
