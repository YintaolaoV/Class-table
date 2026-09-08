# 课表同步与 Web Push API

该目录是静态课表的独立后端，不参与前端构建。服务使用 SQLite 保存每个用户的课表快照、推送订阅、Apple 日历订阅状态和已发送提醒记录。

## 配置

复制 `.env.example` 为 `.env`，填写随机的 `CLASS_TABLE_BOOTSTRAP_KEY`。启用 Web Push 时还需要配置 VAPID 公钥、私钥和 subject；未配置 VAPID 时，课表同步仍然可用。

## 部署

```powershell
docker compose up -d --build
```

服务默认只监听 `127.0.0.1:18110`，由 Caddy 代理到 `/class-table/`。用户通过管理员生成的同步密钥访问，不使用用户 ID 单独鉴权。

Caddy 的 `api.vincentlee.asia` 站点需要加入以下路由，并放在最终 `respond "Not Found"` 之前：

```caddyfile
handle_path /class-table/* {
    reverse_proxy 127.0.0.1:18110
}
```

## 用户初始化

用户无需管理员预先创建账号。在网页“课表设置”中填写自己的用户 ID 和密码，点击“登录 / 注册”即可：首次使用会创建账号，之后使用相同的用户 ID 和密码登录。服务器仅按 user-id 隔离保存课表，密码以不可逆哈希保存，网页本地只保存登录令牌，不保存明文密码。旧版通过 `provision_user.py` 创建的账号，首次用密码登录时会完成密码迁移。

登录后可在“日历与提醒”中开通 Apple 日历订阅。服务器为用户生成一个只读 `.ics` 地址，Apple 日历通过该地址定期读取最新课表；订阅地址使用独立随机令牌，不包含用户密码。重新生成地址会使旧地址失效，关闭订阅不会删除用户课表。
