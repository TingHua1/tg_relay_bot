# Telegram 私聊中继机器人

一个轻量 Telegram 私聊中继机器人：陌生用户向机器人发私聊，机器人将消息转发给管理员；管理员直接回复任意一条对应的转发消息，机器人会将回复发送回原用户。

项目仅使用 Python 标准库，不依赖第三方 Python 包。

## 功能

- 将用户私聊原样转发给指定管理员聊天。
- 管理员回复转发消息或联系人提示消息，即可回复对应用户。
- 转发文本、图片、文件、语音等 Telegram 支持的消息类型；管理员发送非文本内容时使用 Telegram `copyMessage` 回传。
- 用 SQLite 保存管理员消息与用户聊天的映射，服务重启后仍可回复历史转发消息。
- 联系人切换时发送提示：
  - 首次联系：`🆕 新联系人`
  - 历史联系人再次联系：`📨 已联系过的联系人`
- 历史联系人提示会引用该联系人的上一条管理员侧转发消息，便于在 Telegram 内跳转查看上下文。
- 使用 long polling；启动时会删除已配置的 Webhook，但不会丢弃 Telegram 服务器中尚未拉取的更新。

## 文件

```text
tg_relay_bot.py       主程序
tg-relay-bot.service  systemd 服务示例
.env.example          环境变量模板（不含真实凭据）
```

运行后会在指定位置创建 SQLite 数据库，默认路径为：

```text
/var/lib/tg-relay-bot/relay.db
```

数据库包含转发映射和联系人状态。它可能含 Telegram 用户 ID、用户名、显示名和消息映射，属于私有运行数据，**不要提交到 Git 仓库**。

## 前置条件

- Python 3.9 或更高版本。
- 一个通过 [@BotFather](https://t.me/BotFather) 创建的 Telegram Bot。
- 管理员与机器人至少发过一次 `/start`，以便机器人可以向管理员聊天发送消息。
- 服务器可访问 Telegram Bot API。

## 配置

从项目目录复制环境变量模板到 root 专用环境文件：

```bash
cp .env.example /etc/tg-relay-bot.env
chmod 600 /etc/tg-relay-bot.env
```

请将模板中的占位符替换为自己的实际值：

```bash
TG_RELAY_BOT_TOKEN=<bot-token>
TG_RELAY_ADMIN_CHAT_ID=<管理员私聊的数字 chat ID>
TG_RELAY_DB=/var/lib/tg-relay-bot/relay.db
```

权限应限制为 root：

```bash
install -d -m 700 /var/lib/tg-relay-bot
chmod 600 /etc/tg-relay-bot.env
```

> 不要将 Bot Token、管理员 Chat ID、数据库文件、日志或导出的聊天记录上传到 GitHub。

## 手动运行

```bash
install -d -m 700 /opt/tg-relay-bot /var/lib/tg-relay-bot
install -m 700 tg_relay_bot.py /opt/tg-relay-bot/tg_relay_bot.py
chmod 600 /etc/tg-relay-bot.env
/usr/bin/python3 /opt/tg-relay-bot/tg_relay_bot.py
```

启动日志出现类似下方内容，表示机器人已开始轮询：

```text
relay bot @<bot_username> started
```

使用 `Ctrl+C` 停止手动运行。

## systemd 部署

将示例服务文件复制为：

```text
/etc/systemd/system/tg-relay-bot.service
```

然后执行：

```bash
systemctl daemon-reload
systemctl enable --now tg-relay-bot.service
systemctl status tg-relay-bot.service --no-pager
```

查看日志：

```bash
journalctl -u tg-relay-bot -f
```

更新脚本后：

```bash
install -m 700 tg_relay_bot.py /opt/tg-relay-bot/tg_relay_bot.py
systemctl restart tg-relay-bot.service
```

## 使用方式

### 用户侧

1. 用户首次私聊机器人可发送 `/start`。
2. 后续发送文字、图片、文件或其他消息，机器人会转交给管理员。

### 管理员侧

1. 找到机器人转发的用户消息，或对应的联系人提示消息。
2. 使用 Telegram 的“回复”功能回复该消息。
3. 机器人将文本或媒体内容发送给对应用户。

管理员直接发送但**没有回复任何转发消息**的内容不会被转交给用户。

## 数据与安全边界

- 机器人通过 Telegram Bot API 工作，管理员和用户之间的内容会经过机器人进程。
- Bot Token 仅通过环境文件提供，代码与 systemd 单元不应硬编码 Token。
- 数据库应位于 root 专用目录，建议定期做加密备份。
- 管理员 Chat ID 是访问控制边界；不要将环境文件读取权限授予普通用户。
- 脚本不提供群组转发、广播、群发或任意命令执行功能。

## 已知限制

- 使用 long polling，同一 Bot Token 不应同时被多个本实例或其他 polling 程序使用。
- 启动时调用 `deleteWebhook`；如果同一 Bot 原先依赖 Webhook，请先停止或迁移该 Webhook 使用方。
- 媒体回传使用 Telegram `copyMessage`，受 Telegram Bot API 的文件访问与平台限制约束。
- 目前未提供后台 Web UI、用户封禁列表或消息审计界面。
