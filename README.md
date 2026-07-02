# Zampto 自动续期 & 状态监控

基于 GitHub Actions + Playwright（CloakBrowser）的 Zampto（`dash.zampto.net`）免费主机自动续期脚本。每天定时登录后台，检查服务器状态，快到期就自动续期，状态异常就启动服务器，并通过 WxPusher 推送结果到微信。

## 功能

- ✅ 自动登录（支持 Cookie 免验证码登录，失效自动 fallback 到账号密码 + 邮箱验证码登录）
- ✅ 邮箱验证码通过 IMAP 自动读取（等待新邮件到达，过滤旧邮件干扰）
- ✅ 到期前自动续期，续期后校验新的到期时间
- ✅ 服务器为 stopped/offline 状态时自动尝试启动
- ✅ 登录 Cookie 通过 GitHub Actions Cache 跨 run 持久化，减少验证码触发频率
- ✅ 固定浏览器指纹（fingerprint seed 缓存），让 Zampto 尽量识别为同一台设备
- ✅ 通过 Hysteria2 (HY2) 代理出口，规避 GitHub Actions IP 被限制
- ✅ 执行结果通过 WxPusher 推送到微信
- ✅ 出错时自动截图（可选开启录屏），上传为 Actions Artifact 便于排查
- ✅ 自动清理历史 workflow 运行记录，只保留最新一条

## 目录结构

```
.
├── zampto_auto.py                    # 主脚本
├── requirements.txt                  # Python 依赖
└── .github/workflows/zampto-auto.yml # GitHub Actions workflow
```

## 工作原理

1. **启动代理**：用 `HY2_CONFIG` 拉起 Hysteria2 客户端，走 SOCKS5 代理出口。
2. **恢复缓存**：恢复上次保存的浏览器指纹 seed 和登录 Cookie（`actions/cache`）。
3. **登录**：
   - 优先用缓存的 Cookie 免登录；访问首页确认未被重定向回登录页才算成功。
   - Cookie 失效则走表单登录：填账号密码 → 如触发邮箱验证码，等待 10 秒后从 IMAP 收信箱轮询最新一封验证码邮件（过滤掉登录点击时间之前的旧邮件）→ 填验证码 → 提交。
   - **登录成功后才会把最新 Cookie 写回本地文件**，函数内部会二次确认当前不在登录页，避免把无效状态缓存下来；job 结束时该文件由 Actions Cache 自动保存，供下次运行恢复。
4. **检查 & 续期**：读取服务器状态和到期时间，到期时间不足时自动点击续期，续期后重新读取确认。
5. **状态检查**：如果服务器是 stopped/offline，尝试启动。
6. **推送通知**：把状态、到期时间、续期结果等汇总，通过 WxPusher 推送。
7. **善后**：截图/录屏上传为 Artifact（保留 3 天），清理旧的 workflow 运行记录（只留最新 1 条）。

## 触发方式

- **定时**：每天 UTC 00:00（北京时间 08:00）自动运行一次。
- **手动**（Actions 页面 → Run workflow）：
  - `force_renew`：`true` 时忽略剩余天数，强制续期。
  - `enable_recording`：`true` 时开启屏幕录制，随 Artifact 一起上传，便于调试。

## 需要配置的 GitHub Secrets

在仓库 Settings → Secrets and variables → Actions 中添加：

| Secret 名 | 说明 |
|---|---|
| `HY2_CONFIG` | Hysteria2 客户端配置（JSON），用于代理出口 |
| `ZAMPTO_USERNAME` | Zampto 登录邮箱 |
| `ZAMPTO_PASSWORD` | Zampto 登录密码 |
| `ZAMPTO_SERVER_ID` | 要监控/续期的服务器 ID |
| `ZAMPTO_IMAP_USER` | 收验证码邮件的邮箱账号（不填则默认等于 `ZAMPTO_USERNAME`） |
| `ZAMPTO_IMAP_PASSWORD` | 该邮箱的 IMAP 授权码（不是登录密码） |
| `WXPUSHER_TOKEN` | WxPusher 应用 Token |
| `WXPUSHER_UID` | WxPusher 接收人 UID |

> Cookie **不需要**手动配置 Secret，登录成功后由脚本自动写入本地文件，并通过 GitHub Actions Cache 在各次运行之间持久化，无需 PAT、无需回写 Secret。

## 本地运行（可选，用于调试）

```bash
pip install -r requirements.txt
python -c "from cloakbrowser import ensure_binary; ensure_binary()"

export ZAMPTO_USERNAME=xxx
export ZAMPTO_PASSWORD=xxx
export ZAMPTO_SERVER_ID=xxx
export ZAMPTO_IMAP_PASSWORD=xxx
export ZAMPTO_COOKIE_FILE=/tmp/zampto_cookies.json   # 可选，不填则用默认路径
export WXPUSHER_TOKEN=xxx
export WXPUSHER_UID=xxx

python zampto_auto.py
```

## 常见问题

- **Cookie 缓存多久过期？** GitHub Actions Cache 默认 7 天不被访问会自动清理；只要每天跑一次就不会触发过期，正常情况下不会退化回每次都走验证码。
- **想强制走一次账号密码登录怎么办？** 手动清空 Cache（Actions 页面 → Caches → 删除 `zampto-cookies-*`），下次运行会自动 fallback 到表单登录并重新生成 Cookie。
- **Cookie 一定是登录成功后才保存吗？** 是的，`save_cookies_to_file` 内部会检查当前页面是否仍在登录页，仍在登录页会跳过保存，不会缓存无效状态。
