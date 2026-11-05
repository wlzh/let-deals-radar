"""黑五专题落地页：面向搜索与 AI 摘要（SEO/GEO）的静态聚合页。

与主站不同，本页**纯服务端渲染**：两张表格直接写进 HTML，
不靠 JS 注入数据 —— 搜索引擎与抓取型 AI 拿到的首屏即完整内容。
唯一的脚本是一个几行的倒计时，禁用 JS 时由 <noscript> 兜底。
"""

from __future__ import annotations

import html
import os
import re

from . import config
from .extract import AVAIL_CN, EVENT_CN
from .updates import display_time

# --------------------------------------------------------------------------
# 活动窗口与页面元信息
# --------------------------------------------------------------------------

BF_YEAR = 2026
# 北京时间 27 日 13:00 = 美东 00:00：黑五以美东零点为正式起点，
# 页面倒计时也从这一刻起算；对应 UTC 是 05:00（13:00 是北京时间，不是 UTC）。
BF_START_UTC = "2026-11-27T05:00:00+00:00"
BF_END_UTC = "2026-12-01T05:00:00+00:00"
BF_PATH = "black-friday-2026"

# 页面主标题（<title> 再拼上站点名；OG 标签复用同一份）。
# 激进套餐（2026-11-04）：主词前置，贴合 Bing/GSC 已实测的“2026黑五vps / 黑五便宜vps年付”查询词。
_TITLE = "2026 黑五便宜 VPS 年付汇总｜Black Friday 黑五 VPS 活动与低价清单"

# meta description：控制在 120 字内，覆盖核心搜索词与来源站名。
_META_DESC = ("2026 黑五 VPS 活动汇总：Black Friday 黑五便宜 VPS 年付、优惠码与折扣力度，"
              "LowEndTalk / LowEndBox 公开活动每日自动更新，11 月 27 日正式开始。")

# 常见问题：只写不会过期的内容（不点名具体价格、商家或库存）。
BF_FAQ: list[tuple[str, str]] = [
    # 2026-11-05 去重梳理：原 9 条里时间问了两次、选便宜问了两次，
    # “活动和平时优惠的区别”与正文“黑五专属 vs 平时低价”一节重复，
    # 故合并为 6 条，搜索词保留在问题与答案正文里，区别一问只留正文一节。
    ("2026黑五vps活动什么时候开始？",
     "2026 年黑色星期五是 11 月 27 日（星期五），正式开始是北京时间 "
     "11 月 27 日 13:00（美东零点），活动到 12 月 1 日结束；但 VPS 厂商"
     "通常 11 月上旬就陆续开跑，本页每天自动更新已公布的活动。"),
    ("黑五便宜vps年付怎么选？",
     "判断是不是真便宜，先对比同一商家日常价与去年同期，重点看续费价"
     "而不是首期价；循环折扣（recurring）比一次性折扣更值钱。本页低价榜"
     "按近似欧元年价排序，标注「待确认」的要到商家核实库存。"),
    ("库存状态是什么意思？",
     "库存状态优先采信商家在原帖回帖中的表态，无表态时按帖文关键词推断；"
     "标注「待确认」表示未能自动验证，下单前请到商家核实。"),
    ("黑五买 VPS 要注意什么？",
     "首期价与续费价可能不同；留意超售与流量限制、机房与线路，以及退款政策，"
     "下单前把这些和总价一起核对。"),
    ("优惠码怎么用？",
     "复制优惠码后到商家结算页粘贴，确认折扣确实生效、且适用于所选付费周期，"
     "再付款。"),
    ("数据从哪来？",
     "数据来自 LowEndTalk Offers 与 LowEndBox 的公开帖子，自动抓取解析；"
     "每条优惠保留原帖链接，可点开溯源核对。"),
]

# 黑五识别：标题与折扣说明里找关键词。
# \b 与 "black friday" 全词匹配挡住「USB Flash Drive」「BGP」这类
# 只是字母相邻的误伤；BF2\d 覆盖 BF25-BF29 这类年份式优惠码。
_BF_RE = re.compile(r"black\s*friday|黑五|bf\s*20\d{2}|\bBF2\d\b", re.I)

# 「基本买不到」的库存状态：低价年付榜应把这些排出去。
_CLOSED = {"SOLD_OUT", "ENDED", "RESTOCKING", "LIKELY_SOLD_OUT", "NOT_OBSERVED"}


# --------------------------------------------------------------------------
# 判定与排序辅助
# --------------------------------------------------------------------------

def is_black_friday(rec: dict) -> bool:
    """判定一条记录是否黑五相关。

    活动性质标签（event_type）是抓取阶段判定的，直接采信；
    否则只查 title 和 discount.strength 两个字段 —— 不扫正文，
    避免把顺带提到黑五的普通促销也捞进来。
    """
    if rec.get("event_type") == "black_friday":
        return True
    hay = " ".join([
        str(rec.get("title") or ""),
        str((rec.get("discount") or {}).get("strength") or ""),
    ])
    return bool(_BF_RE.search(hay))


def _annual_eur(rec: dict) -> float | None:
    """年化价格（欧元近似），仅用于低价年付榜排序。

    与 render.annual_eur 逻辑一致但不能直接 import（会循环依赖）。
    优先读 enrich 阶段已算好的 annual_eur；原始记录缺该字段时
    再从价格字段现算，币种/周期缺失时退回默认倍率。
    """
    cached = rec.get("annual_eur")
    if cached is not None:
        return cached
    p = (rec.get("sort_price") if "sort_price" in rec
         else rec.get("flagship_price") or ((rec.get("prices") or [None])[0]))
    if not p:
        return None
    mult = config.CYCLE_TO_YEAR.get(p.get("cycle"), 12.0)
    rate = config.FX_TO_EUR.get(p.get("currency"), 1.0)
    try:
        return round(p["amount"] * mult * rate, 2)
    except (KeyError, TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 表格片段（全部值经 html.escape，属性值额外 quote=True）
# --------------------------------------------------------------------------

def _purchase_html(rec: dict) -> str:
    """商家单元格的行动链接。

    有推广/官网链接时用 sponsored 标注（rel 带 nofollow sponsored）；
    没有时退回原帖链接，仅 noopener —— 原帖不是推广位，不该带 sponsored。
    """
    esc = html.escape
    vendor = rec.get("vendor_url")
    if vendor:
        return ('<a class="purchase" href="%s" target="_blank" '
                'rel="noopener nofollow sponsored">%s</a>' %
                (esc(vendor, quote=True),
                 esc(rec.get("purchase_label") or "查看活动与下单")))
    url = rec.get("url")
    if url:
        return ('<a href="%s" target="_blank" rel="noopener">查看原帖</a>'
                % esc(url, quote=True))
    return ""


def _av_html(rec: dict) -> str:
    """库存徽章：内部状态码翻成中文，未知状态退回「待确认」。"""
    esc = html.escape
    av = rec.get("availability")
    return '<span class="av av-%s">%s</span>' % (
        esc(str(av or ""), quote=True), esc(AVAIL_CN.get(av, "待确认")))


def _price_html(rec: dict) -> str:
    """报价单元格：附注必须跟着价格走（自动解析/帖文报价），防止误读。"""
    esc = html.escape
    price = esc(rec.get("display_price") or "—")
    note = esc(rec.get("display_price_note") or "")
    return price + ("<small>%s</small>" % note if note else "")


def _bf_row(rec: dict) -> str:
    """已公布黑五活动表的单行（列名与主站表对齐，保持观感一致）。"""
    esc = html.escape
    event = rec.get("event_type") or "regular"
    posted = rec.get("posted") or ""
    cells = [
        ("商家", esc(rec.get("provider") or "") + "<br>" + _purchase_html(rec)),
        ("活动", '<span class="badge b-%s">%s</span>' %
         (esc(str(event), quote=True), esc(EVENT_CN.get(event, str(event))))),
        ("优惠", esc((rec.get("discount") or {}).get("strength") or "未提及")),
        ("配置", esc(rec.get("display_spec") or "—")),
        ("报价", _price_html(rec)),
        ("库存", _av_html(rec)),
        ("发布", esc(posted[:16]) if posted else "—"),
    ]
    return '<tr class="row">' + ''.join(
        '<td data-label="%s">%s</td>' % c for c in cells) + '</tr>'


def _cheap_row(rec: dict, annual: float) -> str:
    """低价年付榜的单行；把排序用的近似年价亮出来，排序依据可核对。"""
    esc = html.escape
    cells = [
        ("商家", esc(rec.get("provider") or "") + "<br>" + _purchase_html(rec)),
        ("配置", esc(rec.get("display_spec") or "—")),
        ("报价", _price_html(rec) + "<small>≈ €%.2f/年（近似折算）</small>" % annual),
        ("库存", _av_html(rec)),
    ]
    return '<tr class="row">' + ''.join(
        '<td data-label="%s">%s</td>' % c for c in cells) + '</tr>'


def _bf_block(records: list[dict]) -> str:
    """已公布活动表：无数据时输出空态文案而不是空表格。"""
    rows = [r for r in records if is_black_friday(r)]
    # 最新在前；没有 age_days 的沉底，不参与「新帖优先」
    rows.sort(key=lambda r: r.get("age_days")
              if r.get("age_days") is not None else 10**9)
    if not rows:
        return ('<p class="empty">尚未检出黑五活动 —— '
                '厂商通常 11 月上旬陆续公布，本页每日自动更新。</p>')
    body = "\n".join(_bf_row(r) for r in rows)
    return ('<div class="tblwrap"><table><thead><tr>'
            '<th>商家</th><th>活动</th><th>优惠</th><th>配置</th>'
            '<th>报价</th><th>库存</th><th>发布</th></tr></thead>'
            '<tbody>%s</tbody></table></div>' % body)


def _cheap_block(records: list[dict]) -> str:
    """低价年付榜：只留算得出年价且非售罄的，按年价升序取前 10。"""
    candidates = []
    for r in records:
        if r.get("availability") in _CLOSED:
            continue
        annual = _annual_eur(r)
        if annual is None:
            continue
        candidates.append((annual, r))
    candidates.sort(key=lambda t: t[0])
    if not candidates:
        return '<p class="empty">当前暂无可比的低价年付优惠。</p>'
    body = "\n".join(_cheap_row(r, annual)
                     for annual, r in candidates[:10])
    return ('<div class="tblwrap"><table><thead><tr>'
            '<th>商家</th><th>配置</th><th>报价</th><th>库存</th></tr></thead>'
            '<tbody>%s</tbody></table></div>' % body)


def _faq_html() -> str:
    """FAQ 用 details/summary：不靠 JS 也能展开，内容全部转义。"""
    return "\n".join(
        '<details><summary>%s</summary><p>%s</p></details>' %
        (html.escape(q), html.escape(a)) for q, a in BF_FAQ)


def _og_html() -> str:
    """OG/twitter 卡片。

    整块只在配置了域名时输出 —— 没有域名的预览环境里，
    绝对地址只会是无效链接。
    """
    if not config.SITE_URL:
        return ""
    esc = html.escape
    page_url = f"{config.SITE_URL}/{BF_PATH}/"
    img = f"{config.SITE_URL}/og-image.png"
    t = esc(_TITLE, quote=True)
    d = esc(_META_DESC, quote=True)
    return "\n".join([
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="%s">' %
        esc(config.SITE_TITLE, quote=True),
        f'<meta property="og:title" content="{t}">',
        f'<meta property="og:description" content="{d}">',
        f'<meta property="og:url" content="{page_url}">',
        f'<meta property="og:image" content="{img}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{t}">',
        f'<meta name="twitter:description" content="{d}">',
        f'<meta name="twitter:image" content="{img}">',
    ])


def _generated_text(generated_at) -> str:
    """数据更新时间的显示文本。

    display_time 自带「北京时间」后缀；模板会在 <time> 外统一补
    （北京时间），这里剥掉避免重复。解析失败时 display_time 返回「未知」。
    """
    return display_time(generated_at).replace(" 北京时间", "")


# --------------------------------------------------------------------------
# 页面模板
# --------------------------------------------------------------------------

BF_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>@@TITLE@@</title>
<meta name="description" content="@@DESC@@">
@@CANONICAL@@
@@OGTAGS@@
<!-- 图标与订阅沿用主站资源；本页位于子目录，故用 ../ 相对路径 -->
<link rel="icon" href="../favicon.ico">
<link rel="alternate" type="application/rss+xml" title="RSS 订阅" href="@@FEED@@">
<meta name="theme-color" content="#75ADFB">
<style>
:root{
  --bg:#f6f7f9; --card:#fff; --ink:#16181d; --muted:#6b7280; --line:#e5e7eb;
  --accent:#2563eb; --accent-soft:#eff6ff; --good:#059669; --warn:#d97706;
  --bad:#dc2626; --chip:#f3f4f6;
}
@media (prefers-color-scheme:dark){
  :root{ --bg:#0f1115; --card:#171a21; --ink:#e8eaed; --muted:#9aa0a6;
         --line:#272b34; --accent:#60a5fa; --accent-soft:#1e293b;
         --good:#34d399; --warn:#fbbf24; --bad:#f87171; --chip:#1f232b; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1080px;margin:0 auto;padding:24px 18px 64px}
.crumb{margin:0 0 12px;font-size:13px}
header h1{margin:0 0 8px;font-size:26px;letter-spacing:-.3px}
header .lead{margin:0;color:var(--muted);font-size:13px;line-height:1.8}
header .lead b{color:var(--ink)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:16px;margin-top:18px}
.card h2{margin:0 0 10px;font-size:17px}
/* 倒计时：JS 每 30 秒刷新一次；无 JS 时由 noscript 显示固定开场时间 */
.countdown{text-align:center;padding:22px 16px}
.countdown #cd{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums;
  min-height:1.5em}
.countdown noscript p{margin:0;color:var(--muted)}
.tblwrap{overflow-x:auto;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:10px;text-align:left;border-bottom:1px solid var(--line);
  vertical-align:top}
th{background:var(--card);font-weight:600;font-size:12px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.4px;white-space:nowrap}
tr.row:hover{background:var(--accent-soft)}
td{overflow-wrap:anywhere}
.spec{min-width:150px;max-width:240px}
.price{font-weight:600;white-space:nowrap}
.price small,.spec small{display:block;color:var(--muted);font-size:11px;
  white-space:normal;max-width:230px;font-weight:400;margin-top:5px}
.badge{display:inline-block;border-radius:6px;padding:2px 7px;font-size:11.5px;
  font-weight:600;white-space:nowrap}
.b-anniversary,.b-black_friday{background:#fee2e2;color:#b91c1c}
.b-holiday{background:#fef3c7;color:#b45309}
.b-launch{background:#dbeafe;color:#1d4ed8}
.b-restock{background:#d1fae5;color:#047857}
.b-sale{background:#ede9fe;color:#6d28d9}
.b-regular{background:var(--chip);color:var(--muted)}
@media (prefers-color-scheme:dark){
  .b-anniversary,.b-black_friday{background:#450a0a;color:#fca5a5}
  .b-holiday{background:#451a03;color:#fcd34d}
  .b-launch{background:#172554;color:#93c5fd}
  .b-restock{background:#022c22;color:#6ee7b7}
  .b-sale{background:#2e1065;color:#c4b5fd}
}
.av{font-size:12px;white-space:nowrap}
.av-ON_SALE{color:var(--good);font-weight:600}
.av-SOLD_OUT{color:var(--bad);font-weight:600}
.av-ENDED{color:var(--bad)}
.av-RESTOCKING{color:var(--warn);font-weight:600}
.av-PARTIAL{color:var(--warn);font-weight:600}
.av-LIKELY_SOLD_OUT{color:var(--warn)}
.av-UNVERIFIED,.av-UNVERIFIED_STALE,.av-NOT_OBSERVED{color:var(--muted)}
.purchase{display:inline-block;background:var(--accent);color:var(--bg);
  font-weight:700;padding:8px 12px;border-radius:8px;text-decoration:none;
  white-space:normal;line-height:1.5;margin-top:6px}
.purchase:hover{filter:brightness(1.08);text-decoration:none}
.note{background:var(--chip);border-left:3px solid var(--warn);
  border-radius:0 8px 8px 0;padding:10px 14px;font-size:12.5px;
  color:var(--muted);margin:0 0 10px}
.empty{text-align:center;color:var(--muted);padding:32px 0}
/* FAQ：details/summary 原生展开，无需 JS */
.faq details{border-bottom:1px solid var(--line)}
.faq details:last-child{border-bottom:0}
.faq summary{cursor:pointer;font-weight:600;padding:10px 0}
.faq summary:hover{color:var(--accent)}
.faq p{margin:0 0 12px;color:var(--muted);line-height:1.8}
footer{margin-top:28px;color:var(--muted);font-size:12px;line-height:1.9}
footer div{margin:4px 0}
a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);
  outline-offset:3px}
@media(max-width:700px){
 .tblwrap{overflow:visible}.tblwrap>table>thead{display:none}
 .tblwrap>table,.tblwrap>table>tbody,.tblwrap>table>tbody>tr,.tblwrap>table>tbody>tr>td{display:block;width:auto}
 .tblwrap .row{padding:12px 0;border-bottom:1px solid var(--line)}
 .tblwrap .row td{padding:4px 2px;border:0;white-space:normal;max-width:none;min-width:0}
 .tblwrap .row td:before{content:attr(data-label);display:inline-block;color:var(--muted);font-size:11px;margin-right:10px}
 .tblwrap .row td:first-child:before{display:none}
 .price{white-space:normal}
}
</style>
@@JSONLD@@
</head>
<body>
<div class="wrap">

<header>
  <p class="crumb"><a href="@@HOME@@">← 返回 VPS 优惠总览</a></p>
  <h1>2026 黑五便宜 VPS 年付汇总</h1>
  <p class="lead">汇总 LowEndTalk / LowEndBox 公开的 2026 黑五 VPS 活动：黑五便宜年付、优惠码与折扣力度，数据每日北京时间 05:00 自动更新；11 月上旬陆续公布，11 月 27 日正式开始，共 <b>@@COUNT@@</b> 条在跟踪优惠中。</p>
</header>

<div class="card countdown" data-start="@@START@@" data-end="@@END@@">
  <noscript><p>2026 年黑五从北京时间 11 月 27 日 13:00 开始。</p></noscript>
  <div id="cd" aria-live="polite"></div>
</div>

<section class="card">
  <h2>黑五活动时间线</h2>
  <p>11 月上旬：厂商陆续公布黑五 / 大促活动，本页每天更新已公布的活动。11 月 27 日北京时间 13:00（美东零点）：黑五正式开始。12 月 1 日：活动结束，之后本页转为已结束清单与常青低价指引。</p>
</section>

<section class="card">
  <h2>黑五专属 vs 平时低价：怎么分</h2>
  <p>第一张表只收活动性质为黑五 / 大促的优惠，看活动本身；第二张表是全站当前低价年付，不分是不是黑五，看年付价格。同一条优惠可能两边都出现。黑五之外想按全年低价找，看<a href="../cheap-yearly/">低价年付总榜</a>；想按机房找，看<a href="../by-region/">按地区选 VPS</a>。</p>
</section>

<section class="card">
  <h2>已公布的黑五 / 大促活动</h2>
@@BF_BLOCK@@
</section>

<section class="card">
  <h2>现在就能买的低价年付</h2>
  <p class="note">黑五前想先上车的可以看这份：全站当前年付价格最低、非售罄的优惠（按近似欧元年价排序）。</p>
@@CHEAP_BLOCK@@
</section>

<section class="card faq">
  <h2>黑五 VPS 常见问题</h2>
@@FAQ@@
</section>

<footer>
  <div>价格与库存以商家官网为准，标注「待确认」≠有货。</div>
  <div>下单前请核对首期与续费总价、机房、流量与退款条件。</div>
  <div>部分链接为推广链接，符合商家规则的订单可能为本站带来佣金；本站仅聚合公开信息，不构成购买建议。</div>
  <div>数据更新时间 <time datetime="@@GEN_DT@@">@@GEN_TXT@@</time>（北京时间） · <a href="@@FEED@@">RSS 订阅</a> · <a href="@@HOME@@">返回主站</a></div>
</footer>

</div>

<script>
/* 倒计时：只做展示，不改标题、不拉数据；无 JS 时上面的 noscript 兜底。 */
(function(){
  var box=document.querySelector('.countdown'),cd=document.getElementById('cd');
  if(!box||!cd)return;
  var start=Date.parse(box.getAttribute('data-start')),
      end=Date.parse(box.getAttribute('data-end'));
  function tick(){
    if(!Number.isFinite(start)||!Number.isFinite(end))return;
    var now=Date.now();
    if(now<start){
      var t=start-now,
          d=Math.floor(t/864e5),h=Math.floor(t%864e5/36e5),m=Math.floor(t%36e5/6e4);
      cd.textContent='距离黑五开始还有 '+d+' 天 '+h+' 时 '+m+' 分';
    }else if(now<end){
      cd.textContent='黑五进行中';
    }else{
      cd.textContent='黑五已结束（本页保留清单与价格参考）';
    }
  }
  tick();
  setInterval(tick,30000);
})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# 公开入口
# --------------------------------------------------------------------------

def render_bf_page(payload: dict, jsonld_html: str = "",
                   feed_href: str = "../feed.xml",
                   home_href: str = "../") -> str:
    """渲染黑五专题页，返回完整独立的 HTML 文档。

    jsonld_html 是调用方生成的结构化数据标记，**原样**注入 </head> 前；
    本模块不校验也不转义它（那是调用方的契约）。页面其余部分
    不依赖任何外部 CSS/JS。
    """
    records = payload.get("records") or []
    count = payload.get("count")
    if count is None:
        count = len(records)
    esc = html.escape
    generated_at = payload.get("generated_at") or ""
    canonical = (f'<link rel="canonical" href="{config.SITE_URL}/{BF_PATH}/">'
                 if config.SITE_URL else "")
    return (BF_TEMPLATE
            .replace("@@TITLE@@", esc(f"{_TITLE} - {config.SITE_TITLE}"))
            .replace("@@DESC@@", esc(_META_DESC, quote=True))
            .replace("@@CANONICAL@@", canonical)
            .replace("@@OGTAGS@@", _og_html())
            .replace("@@FEED@@", esc(feed_href, quote=True))
            .replace("@@JSONLD@@", jsonld_html or "")
            .replace("@@HOME@@", esc(home_href, quote=True))
            .replace("@@COUNT@@", esc(str(count)))
            .replace("@@START@@", BF_START_UTC)
            .replace("@@END@@", BF_END_UTC)
            .replace("@@BF_BLOCK@@", _bf_block(records))
            .replace("@@CHEAP_BLOCK@@", _cheap_block(records))
            .replace("@@FAQ@@", _faq_html())
            .replace("@@GEN_DT@@", esc(generated_at, quote=True))
            .replace("@@GEN_TXT@@", esc(_generated_text(generated_at))))


def write_bf_page(payload: dict, out_dir, **kwargs) -> str:
    """把黑五页写进 {out_dir}/black-friday-2026/index.html，返回文件路径。

    目录不存在时自动创建（首次建站）；已存在时重复写入幂等。
    其余关键字参数原样转给 render_bf_page。
    """
    page_dir = os.path.join(str(out_dir), BF_PATH)
    os.makedirs(page_dir, exist_ok=True)
    path = os.path.join(page_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_bf_page(payload, **kwargs))
    return path
