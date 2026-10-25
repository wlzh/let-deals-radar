# GitHub Actions 上线清单（方案 A）

按顺序做，约 15 分钟。**每一步都有验证点**，失败时能立刻定位。

## 当前生产调度

独立的非 fork 仓库 [wlzh/let-deals-radar-scheduler](https://github.com/wlzh/let-deals-radar-scheduler)
负责云端等待计时后调用本仓库 `.github/workflows/update.yml` 的 `workflow_call` 入口。
本仓库仍是代码、AFF 配置、数据和 `site/` 的唯一来源，fork 关系与 Cloudflare Pages 项目不变。
为避免双重调度，原 fork 的 `schedule` 已移除，`push` 和 `workflow_dispatch` 保留。

- 调度仓库使用 `LDR_REPO_PUSH_KEY` Secret，内容是只对本仓库有写权限的专用 Deploy Key。
  不使用或保存个人全局 GitHub Token；可以在本仓库 Settings / Deploy keys 单独撤销该密钥。
- 跨仓库调用会显式 checkout 本仓库最新 `main`，而不是把调度仓库当作项目源码。
- Variables 在调用方解析：调度仓库已配置 `LDR_VERIFY_LIVE=true` 和 `LDR_SITE_URL=https://vps.869hr.uk`。
  如要使用 `LDR_SITE_*` / `LDR_AFFILIATES` 覆盖配置，需同时更新调度仓库对应变量。
  源码中的 AFF 默认配置仍随本仓库代码更新，无需复制。
- 成功顺序是测试、抓取、非空与链接审计、推送回原仓库、Cloudflare Pages 线上三文件比对。
  只有全部成功，才会把原仓库 SHA 和运行 URL 写入调度仓库的 `last-success.json`。
  该成功记录也为调度仓库提供持续 Git 活动，避免仅更新原仓库而调度仓库长期无提交。
- 主仓库前进时，旧构建拒绝推送，不强制覆盖新代码。重新运行最新 `main` 即可。
- 紧急停止自动更新时，禁用调度仓库的 `refresh.yml`；原仓库和线上旧版本无需删除。

[跨仓库管线验收 40168787171](https://github.com/wlzh/let-deals-radar-scheduler/actions/runs/40168787171)
已通过测试、真实抓取、专用密钥推送、线上校验和成功记录。该次是手动触发，不算定时验收。
原 fork 的 14:03 探针和新非 fork 仓库的 cron 均未在观察窗口内产生定时事件，
因此不能把原因简单归为 fork，也不能把手动成功视为 cron 已恢复。
上游也观察到小时级调度错位。用户随后选择了不依赖 cron 的 Actions 环境等待计时链。

`refresh-delay-5m` 环境等待 5 分钟，探针完成后自动创建 `mode=auto` 的下一条运行；
`refresh-delay-24h` 环境等待 1440 分钟，每次更新完成再排入下一天。
这是滚动日频，不是每天固定 09:23；等待不占用 runner，也不计入等待计费时间。
环境计时结束后仍可能有 runner 排队，不能保证精确到秒。

调度仓库的控制器仅用本仓库临时 `GITHUB_TOKEN` 的 `actions: write` 自接续，不存个人 Token。
控制器会核对实际经过的时间，计时环境被删除或缩短时拒绝刷新，避免快速循环。
已有接续或活跃链时不重复排队，POST 结果不明确时先查询，不盲目重复创建。
抓取失败也会排入下一天重试；主动取消不自动重启。计时或控制器故障需检查失败运行并重新启动。
`.next-run.json` 记录已创建的下一条运行 ID，不表示下一天的更新已成功。

停止时先禁用 `refresh.yml`，再取消所有等待/运行中的链；仅禁用不会取消已有运行。
重启前确认旧链停止，再启动新的 `mode=probe`。不要通过重跑历史完成记录启动第二条链。

```bash
gh run list -R wlzh/let-deals-radar-scheduler --workflow refresh.yml --limit 10
gh workflow run refresh.yml -R wlzh/let-deals-radar-scheduler -f mode=probe
```

## 原生 fork 定时排查（历史记录）

以下为切换独立调度前的历史排查，不是当前生产定时入口。

`wlzh/let-deals-radar` 是 fork，**同样支持原生 `schedule`，不要求另建仓库**。
GitHub 在 fork 时默认禁用定时工作流，需要在自己的仓库启用，而不是只查看上游的运行记录。

```bash
gh workflow enable update.yml -R wlzh/let-deals-radar
gh api repos/wlzh/let-deals-radar/actions/workflows/update.yml --jq .state
gh run list -R wlzh/let-deals-radar --event schedule --limit 10
```

验收必须出现 `event=schedule` 的实际运行；手动 `workflow_dispatch` 成功不代表定时器已生效。
排查时确认默认分支为 `main`、工作流文件位于默认分支且已启用。
修改实际 cron 表达式可重新注册调度；仅修改注释不等于更改 cron。
临时高频测试结束后应恢复正式频率，不把几分钟未运行直接判为永久故障。
GitHub 的调度可能延迟或丢弃，公共仓库长期无活动也可能被禁用。

当前 `vps.869hr.uk` 由 Cloudflare Pages 跟随主仓库 `main` 部署。
`Deploy to own server` 未配置时跳过正常，不代表没有 Pages 部署。
Variable `LDR_VERIFY_LIVE=true` 开启发布等待与验收，`LDR_SITE_URL` 必须指向该仓库的站点。
工作流比对线上 `index.html`、`data/deals.json` 和 `sitemap.xml` 与本次产物，
仅忽略 Cloudflare 额外注入的官方统计脚本，其余内容严格比较。
超时不一致会失败，避免将“提交成功”误报为“网站更新成功”。

抓取期间 `main` 若被其他提交推进，`deploy/commit_refresh.sh` 拒绝推送过时产物。
此时请重新触发最新 `main` 的工作流，不要强制推送或把旧构建覆盖到新代码之上。

参考：[GitHub 事件与定时调度](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)、
[启用工作流及 fork 默认行为](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)。

### 2026-10-25 验证记录

- 已同步 `origin/main`，确认 fork、默认分支 `main`、仓库 Actions 与工作流均启用。
- 重新启用工作流并实际修改 cron 为每 5 分钟进行短时测试，仍未观察到 `schedule` 运行；
  这不足以判断 fork 不支持定时运行，也不能确定 GitHub 调度服务的具体故障原因。
- [手动运行 40163248884](https://github.com/wlzh/let-deals-radar/actions/runs/40163248884) 成功，
  260 项测试、真实抓取、提交回主仓库、Cloudflare Pages 与线上三文件比对全部通过。
- 已恢复正式每天北京时间 09:23，未创建独立调度仓库、未解除 fork，原生定时触发仍待验收。
- 后续已用仓库所有者 `wlzh` 的 noreply 身份修改真实 cron 并重新启用，
  GitHub API 确认提交作者和提交者均关联 `wlzh`。历史提交曾关联另一个当前查询返回 404 的账号，
  但更早也存在所有者修改 cron 的记录，因此身份问题不能直接认定为原始故障根因。
- 默认分支曾短暂切换到指向同一 SHA 的临时分支再恢复 `main`，以重新关联调度身份；
  已确认临时分支清理、默认分支仍为 `main`、fork 关系不变、工作流 `active`。
  此后观察窗口仍无 `schedule` 记录，已再次恢复每天 09:23，不能宣称定时故障已解决。
- 北京时间 14:03 的单独定时探针也未创建 `schedule` 运行，截至 14:18 仍为 0。
  随后按用户选择建立独立调度仓库，移除了原 fork 的 cron 和临时探针。

### 代码推送自动更新

`main` 的程序、资产、测试、部署脚本、工具或更新工作流发生变化时，`push` 事件也会自动运行更新管线。
`data/`、`site/` 和普通文档不在触发路径中，自动刷新产物的提交不会形成自触发循环。
这与每天定时抓取是两条触发路径；`push` 成功不替代 `schedule` 验收。

---

## 第 0 步：本地确认（已完成 ✅）

```bash
python -m pytest tests -q          # 应显示 124 passed
bash deploy/run_pipeline.sh        # 应生成 site/ 与 data/deals.json
```

---

## 第 1 步：建 GitHub 仓库并推送

在 GitHub 网页上**新建一个空仓库**（不要勾选 README/.gitignore，避免冲突）：

- 名称建议：`let-deals-radar`
- 可见性：**Public**（Actions 分钟数无限免费）或 Private
  （Private 每月 2000 分钟免费，本工作流每次约 5 分钟，够用）

然后本地推送：

```bash
cd <项目目录>
git remote add origin git@github.com:<你的用户名>/let-deals-radar.git
git push -u origin main
```

> 用 HTTPS 的话改成
> `git remote add origin https://github.com/<你>/let-deals-radar.git`

**验证点**：GitHub 页面上能看到 48 个文件和 2 条提交。

---

## 第 2 步：确认工作流被识别

推上去后，打开仓库的 **Actions** 标签页。

**验证点**：左侧应出现 **"Update deals"**。

若没出现，检查 `.github/workflows/update.yml` 是否真的推上去了：
仓库首页文件列表里应能看到 `.github` 目录。

---

## 第 3 步：手动触发一次（用 recent_days=7 快速验证）

Actions → 左侧 **Update deals** → 右侧 **Run workflow** 按钮：

| 输入项 | 填什么 | 原因 |
|---|---|---|
| `with_replies` | ✅ 勾选 | 验证回帖抓取链路 |
| `recent_days` | `7` | **先小范围验证，跑得快** |

点 **Run workflow**。

**验证点**：几十秒内出现一个新的运行记录，点进去能看到步骤逐个变绿：

```
✅ Checkout
✅ Setup Python
✅ Install test dependency
✅ Run tests              ← 124 passed
✅ Fetch, extract and build site
✅ Verify output is non-empty   ← 记录数: N
✅ Upload site artifact
✅ Commit refreshed data
⏭️ Deploy to own server   ← 灰的（未配服务器，正常跳过）
```

**预计耗时**：3–6 分钟（回帖抓取是主要耗时）。

---

## 第 4 步：检查结果

**① 看日志里的记录数**

点开 `Fetch, extract and build site` 步骤，末尾应有：

```
[5/5] 完成：N 条记录 (let=X, leb=Y)
```

> ⚠️ 若显示 `解析到 0 条` 或报 `HTTP 403`：
> 说明 Cloudflare 拦了 Actions 的 IP。这种情况很少见（Actions 出口 IP 池很大），
> 但若发生，可加 `--no-site` 重试，或改用代理。

**② 看是否自动提交**

回到仓库首页，应看到 `github-actions[bot]` 的新提交：

```
chore: refresh deals [skip ci]
```

**③ 下载站点产物**

该次运行的页面底部 **Artifacts** → 下载 `site-N.zip`，
解压后双击 `index.html` 即可预览（数据内嵌，无需服务器）。

**验证点**：页面能打开、有数据、能筛选和展开详情。

---

## 第 5 步：改成正式参数

验证通过后，把定时任务的窗口改大：

**方式一（推荐）**：改 `.github/workflows/update.yml` 里的默认值

```yaml
RECENT_DAYS: ${{ github.event.inputs.recent_days || '90' }}
```

`'90'` 已经就是 90 天，**默认无需修改** —— 手动触发时填 7 只影响那一次。

**方式二**：手动触发时 `recent_days` 留空或填 `90`，定时任务会自动用 `90`。

**验证点**：下次定时运行（每 6 小时，UTC 的 17 分）产出更多记录。

---

## 第 6 步（可选）：绑定自己的域名

### 6.1 设置站点变量

仓库 **Settings → Secrets and variables → Actions → Variables** 标签页，
点 **New repository variable**，逐个添加：

| 名称 | 示例值 |
|---|---|
| `LDR_SITE_TITLE` | `VPS 活动雷达` |
| `LDR_SITE_URL` | `https://deals.example.com` |
| `LDR_SITE_DESC` | `自动汇总 VPS 优惠活动` |
| `LDR_SITE_FOOTER` | `联系我：@yourname` |

> 设置 `LDR_SITE_URL` 后会生成 canonical 链接、绝对 sitemap URL，
> 并在 `robots.txt` 加 Sitemap 行。

### 6.2 设置部署目标

同页继续加（先在服务器上建好目录和 SSH 密钥）：

| 名称 | 示例值 |
|---|---|
| `LDR_DEPLOY_HOST` | `1.2.3.4` |
| `LDR_DEPLOY_USER` | `deploy` |
| `LDR_DEPLOY_PATH` | `/var/www/deals/` |
| `LDR_DEPLOY_PORT` | `22`（可省） |

**Secrets** 标签页加一个：

| 名称 | 内容 |
|---|---|
| `LDR_DEPLOY_SSH_KEY` | 部署私钥全文（含 `-----BEGIN...` 到 `...END-----`） |

**验证点**：下次运行后，`Deploy to own server` 步骤变绿并显示
`已推送到 deploy@1.2.3.4:/var/www/deals/`。

> 服务器端的 Nginx 配置与目录准备见 [DEPLOY.md](DEPLOY.md)。

---

## 常见问题

**Q：Actions 页面没有 "Update deals"？**
确认 `.github/workflows/update.yml` 已推送，且 `on:` 缩进正确
（YAML 对缩进敏感）。可在本地跑：
`python -c "import yaml;print(yaml.safe_load(open('.github/workflows/update.yml')))"`

**Q：`Run tests` 步骤失败？**
本地跑 `python -m pytest tests -q` 复现。注意测试用的是
`tests/fixtures/` 下的精简夹具，不依赖网络。

**Q：`Verify output is non-empty` 失败？**
说明当时抓到的记录数为 0。**这是保护机制在生效** ——
它拒绝把空数据提交上去，线上仍是上次的好数据。
可在 Actions 页面点 **Re-run** 重试。

**Q：`Commit refreshed data` 失败（push 被拒）？**
通常是同时有别的提交。工作流已设 `concurrency` 避免同工作流并发，
但不能阻止开发者推送。请用 **Run workflow** 重新触发最新 `main`，
不要强推，也不要复用旧代码构建的产物。

**Q：想改运行频率？**
编辑 `.github/workflows/update.yml` 的 cron：
`'17 */6 * * *'` = 每 6 小时。改成 `'17 */2 * * *'` = 每 2 小时。
注意 GitHub 的 cron 只支持 UTC，且高负载时可能延后几分钟。

**Q：Private 仓库会超免费额度吗？**
本工作流每次约 5 分钟，每 6 小时一次 = 每天约 20 分钟，
每月约 600 分钟 < 2000 分钟免费额度。**Public 仓库则完全不计费**。
