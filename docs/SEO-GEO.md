# SEO/GEO 与佣金入口（0.4.0）

## 页面与来源

- 每条具有有效原帖 URL 的优惠生成 `/deals/<URL 的 SHA-256 前 24 位>/`，标题修改不改变地址。
- 首页静态链接、展开详情、RSS 和 sitemap 引导至该页；RSS GUID 继续使用原帖 URL，不强迫阅读器重新发送历史条目。
- 当前数据保留窗口之外的详情由生成器移除，不保留假装仍有效的历史购买页面。
- 详情提供原帖、整理时间、最后检出时间、地区、优惠码、报价限制和商家入口。缺失信息显示待核对，不编造续费价格或退款承诺。

## 结构化数据边界

首页 ItemList 是导航列表，不把数十个未核实报价标成商品。只有 `price_bound=true` 的套餐详情输出 Product/Offer；不捏造商品图片、评分或评论。套餐绑定只是自动抽取，不代表人工核验。

llms.txt 提供数据与来源说明，不是排名或 AI 引用保证。SEO/GEO 仍依赖真实有用内容、可访问页面及搜索引擎收录。参考：

- https://developers.google.com/search/docs/appearance/ai-features
- https://developers.google.com/search/docs/appearance/structured-data/product-snippet

## AFF 与安全

不改动 `affiliate.py` 的个人映射。详情使用经原有 `prepare()` 校验和改写后的商家 URL，保留 sponsored/nofollow 标记；无商家入口时只提供来源，不猜测 AFF。用户投稿内容均转义，路径只使用固定长度哈希，不使用原帖标题作为文件路径。Token 仍只存在 Actions Secrets。

商家名称只保守修正明显误识别项，无法核实的显示待核实。服务类型是标题粗分类，不冒充完整产品审查；不删除其他主机类优惠。

## 验收与运行

```sh
python -m pytest -q
python -m tools.audit_site
python -m tools.verify_live --url https://vps.869hr.uk
```

发布检查包含原有七项和全部详情页，最多 8 个并发请求，任一内容不一致仍拒绝确认发布成功。保留 Cloudflare 指定分析脚本的唯一内容归一化规则。

论坛测试模拟底层请求而非跳过业务分支，避免联网或实际发布。正常运行时的网络、删除核对与限流不受影响。

代码版本与数据时间分开：仅重建页面不得更改 `generated_at`。每次真实抓取仍由原调度产生时间和变化记录，通知仍使用持久化投递账本去重和补发。未接入转化统计或后台。
