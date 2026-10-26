# 每日更新与 Telegram 通知

## 发布与数据保护

原 fork 是代码、AFF 配置和站点数据的唯一来源；独立调度仓库负责 Actions 环境等待计时。
生产更新必须通过测试、抓取、非空检查、链接审计、提交以及线上四文件比对
（HTML、优惠 JSON、变化 JSON、sitemap），才算发布成功。

- 来源为空或失败时保留上次站点；任何单一来源较上次检出数减少超过 40%，且基数至少 10 条时，拒绝发布。
- 临时网络错误沿用抓取层有限重试。抓取失败后，调度器仍安排下一天；计时器或接续控制器故障会失败并尝试告警。
- RSS 是有限窗口。旧活动未出现时保留并标注“本轮未检出”，撤销当前在售暗示，保留上次检出时间，不推断已停售。
- 连续未检出 30 天，或超出设置的活动时间窗口（生产 90 天）后移出列表；仍可通过 Git 历史追溯。
- 新出现、重新出现、报价/配置/库存/优惠规则变化分别记录；抓取时间、评分和排序变化不算活动更新。
- 首期优惠与循环价格描述冲突时，不宣称永久折扣。所有价格仍需要在商家结算页核对。

网页“今日更新”按北京时间汇总，明细展示最近 5 次，完整 `data/updates.json` 保留最近 30 次更新。
首次建立基线不把全部活动误报为新增；解析规则升级可能造成信息修正，并不代表商家实际调整了价格。

## 安全配置 Telegram

不要在聊天、代码、命令参数、`.env` 或工单里粘贴 Token。推荐新建本站专用机器人，限制其频道权限。
私聊先向机器人发送 `/start`；频道需要给机器人发布消息的权限。明确选择收件目标，不自动给陌生聊天发消息。

在本机交互终端运行：

```bash
cd /Users/m/document/QNSZ/project/let-deals-radar
python3 -m tools.configure_telegram
```

脚本隐藏输入 Token 和 Chat ID，先验证机器人身份及消息权限，再通过 stdin 调用 GitHub CLI，
分别写入 `wlzh/let-deals-radar` 和 `wlzh/let-deals-radar-scheduler` 的：

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

不会生成本地凭据文件，也不会把 Token 放进进程参数或输出。设置中断时可能只写入部分 Secret，重新执行即可。
Secrets 仅授权 Actions 运行时读取；不要给不可信协作者修改工作流的权限。GitHub 的遮罩不是绝对防泄漏机制。
如果 Token 曾经公开或发给不可信对象，先到 BotFather 撤销并重新生成，再重新运行配置脚本。

配置后验证云端送达（不抓取、不发布虚假的更新成功消息）：

```bash
gh workflow run telegram-test.yml -R wlzh/let-deals-radar
```

必须在目标会话看到连接测试消息，才算通知端到端验收完成。仅 Secret 名称存在不能证明可用。

## 通知内容与边界

- 每轮经过线上验收后发送变化数量和代表性活动，并附网页与 Actions 链接；没有变化也报告成功。
- 测试、抓取、审计、提交或网页校验失败时，发送异常阶段与运行链接，不把旧数据说成新发布。
- Telegram 调用有限重试，消息长度受控；网络超时后重试极少数情况下可能重复，不保证 exactly-once。
- Telegram 故障不回滚已上线网页，工作流会出现独立 Warning 和明确的通知状态。
- 未配置时明确显示 `not-configured`，不会把未发送说成已送达；只配置一个 Secret 会报告错误。
- 调度等待期间的工作流定义固定。修改 Secrets 转发/接续告警逻辑后，需取消旧等待链并从最新定义重新启动一次探针。
- GitHub 整体不可用、工作流被禁用、主动取消或通知平台同时故障时，同一系统无法保证告警送达。
  建议同时开启 GitHub Actions 邮件通知；本方案不伪称具备独立于 GitHub 的故障监控。

参考：[Telegram Bot API](https://core.telegram.org/bots/api#sendmessage)、
[GitHub Actions Secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)。
