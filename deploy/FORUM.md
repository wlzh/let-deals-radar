# 论坛发帖（搬运到 LowSeek）

把抓到的优惠帖**自动搬运**到自己的 Flarum 论坛，方便中文用户阅读。

> **默认是关闭的，而且默认只演练不发布。** 需要显式配置才会真发。

---

## 为什么这么设计

搬运是**旁路功能**：站点更新是主链路，论坛发帖失败不应影响它。因此：

- 独立模块 `letdeals/forum.py`，不进入 `pipeline.run()`
- CLI 里用 `--post-forum` 单独触发
- 任何异常都被捕获，只打印不中断

**安全四原则**：

| 原则 | 实现 |
|---|---|
| 默认关闭 | `LDR_FORUM_ENABLED=false` 时直接返回 |
| 默认演练 | `LDR_FORUM_DRY_RUN=true` 时只打印不提交 |
| **幂等** | 已发过的 `thread_id` 记在 `data/forum_posted.json`，**永不重复** |
| 限流 | 每次最多 `LDR_FORUM_MAX_PER_RUN` 条 |

---

## 站点链接：先论坛、后原帖

以下为旧版论坛集成行为；当前生产站点已取消论坛跳转，购买与来源规则见 [0.4.2 收益入口复盘](../docs/PURCHASE-ROUTING.md)。

旧版站点每行的「查看原帖」改为**优先引导到论坛**：

| 情况 | 渲染 |
|---|---|
| 有对应论坛帖 | **「中文详情 →」**（主，实心）+ **「原帖 →」**（次，描边） |
| 没有论坛帖 | **「查看原帖 →」**（指向 LowEndTalk） |

**为什么保留原帖链接**：

1. 站点记录数（约 80）远多于论坛帖子数（约 40）—— 大半记录没有论坛入口，去掉就无从点击
2. 用户需要能核对原始信息
3. 只引流不回链容易引起商家不满

### 实现方式（方案 A：读上一轮结果）

建站发生在发帖**之前**，所以 `enrich()` 读的是**上一轮**留下的
`data/forum_posted.json`：

```
每 6 小时：采样 → 建站(读上一轮记录) → 发帖 → 提交
                          ↑
                  新发的帖延迟一个周期才出现在站点上
```

对每 6 小时更新一次的站点，**延迟一个周期用户感知不到**。
好处是不用改工作流顺序，发帖失败也不会影响建站。

> 如果想做到同轮零延迟，可把顺序改成「采样 → 发帖 → 建站 → 提交」，
> 代价是发帖失败会波及建站。当前选择更稳的方案。

### 死链防护

- 记录文件**不存在或损坏** → 返回空 dict，**全部回退原帖**，不会死链
- 论坛帖子被删 → `verify_posted` 清理记录 → 下次建站**自动回退原帖**

---

## 配置

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `LDR_FORUM_ENABLED` | `false` | **总开关**。想停发帖改这一个值即可 |
| `LDR_FORUM_DRY_RUN` | `true` | 演练模式，只打印不提交 |
| `LDR_FORUM_API_KEY` | 空 | Flarum API Key（必填才能真发） |
| `LDR_FORUM_URL` | `https://lowseek.ai` | 论坛地址 |
| `LDR_FORUM_TAG_ID` | `21` | 目标标签（「内容转载」） |
| `LDR_FORUM_BOT_ID` | `678` | 机器人账号 ID（仅用于日志） |
| `LDR_FORUM_MAX_PER_RUN` | `3` | 每次最多**新发**几条 |
| `LDR_FORUM_MAX_AGE_DAYS` | `3` | 只发最近 N 天的帖子（0=不限） |
| `LDR_FORUM_MIN_PERCENT` | `30` | 折扣达到此值才算合格 |

### 用法

```bash
# 演练（默认行为，不发任何东西）
python -m letdeals --post-forum

# 实际发布
LDR_FORUM_ENABLED=true LDR_FORUM_DRY_RUN=false \
LDR_FORUM_API_KEY=xxx \
python -m letdeals --post-forum

# 临时只发 1 条
python -m letdeals --post-forum --forum-limit 1
```

---

## 限流语义（重要）

`LDR_FORUM_MAX_PER_RUN` 限制的是**本次新发几条**，不是「遍历多少条」：

- 已发过的 `thread_id` **跳过且不占配额**
- 因此连续运行会把积压的合格帖**分批**发完，而不是一次灌满
- 配合 `LDR_FORUM_MAX_AGE_DAYS`（只发最近 N 天）**自然收敛**

实测（3 天时效，每次上限 3 条）：

```
第 1 次：新发 3 / 已发过 0
第 2 次：新发 3 / 已发过 3
第 3 次：新发 2 / 已发过 6
第 4 次：新发 0 / 已发过 8   ← 发完自然停止
第 5 次：新发 0 / 已发过 8
```

---

## 筛选规则

**两个门槛都要过**：

1. **时效**：`age_days <= LDR_FORUM_MAX_AGE_DAYS`
2. **价值**（满足任一）：
   - 属于重点活动：`anniversary` / `black_friday` / `holiday` / `restock`
   - 折扣百分比 `>= LDR_FORUM_MIN_PERCENT`
   - 循环折扣（续费同价）
   - 有优惠描述且价格已知

**目的**：避免刷屏。实测 105 条记录里，只有 8 条能同时通过。

---

## 帖子格式

**价格、规格、优惠码一律原样保留，不翻译** —— 数字翻错会误导用户下单，商家也会来投诉。

```markdown
> MailBig Halloween Sale - Email Hosting From £3.37/yr   ← 原帖标题（英文原文）

**服务类型**：邮箱托管（非 VPS）      ← 仅非 VPS 才显示
**优惠**：循环/永久 · double storage
**循环折扣**：是（续费同价）
**起价 / 旗舰价**：£3.37/年
**配置**：15G 存储
**优惠码**（请以商家页面为准）：`XXX`

---
**原始出处**：LowEndTalk
**原帖地址**：https://lowendtalk.com/discussion/...
**商家官网**：https://namebig.io
**原帖发布**：今天

> 本帖由机器人自动搬运并整理，便于中文用户阅读；
> **价格与库存以商家页面为准**，下单前请自行核实。
```

### 服务类型识别

避免把**邮箱托管**当成 VPS 发出去（实测遇到过）。识别到非 VPS 会在标题加 `[类型]` 标注：

| 类型 | 识别关键词 |
|---|---|
| `email` → 邮箱托管 | email hosting / mail hosting / mailbig |
| `storage` → 存储型 | storage vps / object storage / s3 |
| `dedicated` → 独立服务器 | dedicated server / bare metal |
| `cdn` / `domain` | 同理 |
| `vps` | 默认（未识别到特殊类型） |

---

## 论坛侧配置

已在论坛上创建（**2026-10-16**）：

| 项目 | 值 |
|---|---|
| 机器人账号 | `DealsBot`（id=678），昵称「优惠搬运工」 |
| 专用用户组 | 「机器人」（id=16） |
| 标签 | 「内容转载」（id=21，slug `content-repost`） |
| 标签属性 | `is_hidden=1`（不进全部讨论）、`is_restricted=1` |

**权限**（仅机器人 + 管理员可发帖）：

```
管理员(1)     tag21.startDiscussion + tag21.viewForum
机器人(16)    tag21.startDiscussion + tag21.viewForum
              tag21.discussion.startWithoutApproval   ← 免审核
              tag21.discussion.replyWithoutApproval
              lowseekai-content-risk-fee.bypass       ← 免扣积分
Member(3)     tag21.viewForum                          ← 只能看
```

**扣费豁免**（双保险）：

- 用户组权限：`lowseekai-content-risk-fee.bypass`
- 标签白名单：`content-risk-fee.whitelist_tags = ["19","21"]`

> ⚠️ **受限标签的权限必须带 `tagN.` 前缀**。`flarum/tags` 的 `TagPolicy`
> 对 `is_restricted=1` 的标签只认 `tagN.xxx`，全局权限会被忽略 ——
> 这是实测踩到的坑。

---

## 如何停止

| 场景 | 做法 |
|---|---|
| **日常暂停** | 设 `LDR_FORUM_ENABLED=false`，或停掉定时任务 |
| **彻底禁用** | 后台权限页取消机器人组的「发起讨论」勾选 |
| 怀疑 Key 泄露 | 删除 `api_keys` 表对应记录 |

**不要删账号** —— 可能连带删除已发的帖子。

后台取消权限的路径：**管理 → 权限 → 「内容转载」列 → 「发起讨论」行 → 点掉机器人组的勾**。

---

## 已发布记录

`data/forum_posted.json`：

```json
{
 "221711": {
  "discussion_id": "1317",
  "posted_at": "2026-10-16T11:29:46Z",
  "title": "【常规促销】 Linceris.com 2 核 / 75G NVMe 存储",
  "url": "https://lowseek.ai/d/1317"
 }
}
```

**这个文件必须提交到仓库**（或至少持久保存），否则换台机器运行会重复发帖。

---

## 测试

`tests/test_forum.py`（22 个用例）覆盖：

- 总开关关闭时零调用
- 缺 API Key 时中止（**即使注入了测试 client 也要拦住**）
- 演练模式不需要凭据、且不写记录文件
- **幂等**：同一 `thread_id` 永不重复
- **限流**：上限管的是新发条数；分批发完无重复
- 筛选：时效、价值门槛
- 正文生成：价格/规格不被翻译、含出处与免责声明
- 服务类型识别
- 容错：单条失败不影响其余；记录文件损坏不崩

```bash
python -m pytest tests/test_forum.py -q
```
