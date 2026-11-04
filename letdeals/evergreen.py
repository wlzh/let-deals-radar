"""常青落地页：低价年付总榜 + 按地区选 VPS。

与黑五专题页同一路数：**纯服务端渲染**，表格直接写进 HTML，
不靠 JS 注入数据 —— 搜索引擎与抓取型 AI 拿到的首屏即完整内容。

为什么只有两页、而不按价格档或单个地区拆成十几页：
11 月 1 日快照测算过，剔除已关闭状态后低价年付（年化 30 欧元以内）
只有 28 条、香港未关闭只有 3 条、阿姆斯特丹只有 1 条。
拆薄页会被判成重复薄页；合集页 + 分节才是当前数据量下
既能承接通用词、又不至于凑数的形态。
"""

from __future__ import annotations

import html
import os

from . import bfpage, config
from .updates import display_time

# 复用黑五页的判定口径：已关闭状态不进常青榜。
_CLOSED = bfpage._CLOSED
_annual_eur = bfpage._annual_eur
_purchase_html = bfpage._purchase_html
_av_html = bfpage._av_html
_price_html = bfpage._price_html

# --------------------------------------------------------------------------
# 页面元信息
# --------------------------------------------------------------------------

CHEAP_PATH = "cheap-yearly"
CHEAP_TITLE = "低价年付 VPS 汇总｜年付便宜 VPS 清单，每日更新"
CHEAP_DESC = ("汇总 LowEndTalk / LowEndBox 公开的低价年付 VPS 优惠："
              "年化价格、配置、折扣力度与库存状态，每日自动更新，"
              "已关闭活动已剔除。")
CHEAP_FAQ: list[tuple[str, str]] = [
    ("年化价格是怎么算的？",
     "本站把不同币种与付费周期统一折算成年化欧元近似值：美元乘 0.92、"
     "英镑乘 1.17，月付乘 12、季付乘 4；原始报价与结算页为准，"
     "折算值只用来排序和横向比较，不能当成实际扣款金额。"),
    ("为什么很多条都标「待确认」？",
     "库存优先采信商家在原帖回帖中的表态，没有商家表态时退回帖文关键词；"
     "仍无法判定则标「待确认」。「待确认」不等于有货，下单前请到商家核实。"),
    ("低价年付要重点看什么？",
     "先看续费价而不是首期价，循环折扣比一次性折扣更值钱；"
     "再看流量、机房与退款政策，低价套餐常在这些地方收紧。"),
    ("数据从哪来？",
     "数据来自 LowEndTalk Offers 与 LowEndBox 的公开帖子，自动抓取解析；"
     "每条优惠保留原帖链接，可点开溯源核对。"),
]

REGION_PATH = "by-region"
REGION_TITLE = "按地区选 VPS｜美国 / 欧洲 / 香港 / 新加坡 VPS 优惠汇总"
REGION_DESC = ("按机房地区汇总 LowEndTalk / LowEndBox 的 VPS 优惠："
               "美国、欧洲、新加坡、香港等地区分节清单，"
               "含配置、价格与库存状态，每日自动更新。")
REGION_FAQ: list[tuple[str, str]] = [
    ("地区应该怎么选？",
     "先看你的用户或自己常在哪个地区，再看机房与线路；"
     "只看价格不看地区，容易买到延迟高、体验差的套餐。"),
    ("一个商家为什么出现在多个地区？",
     "说明该商家在多个机房都有活动，本页按机房分节列出，"
     "同一商家在不同地区的套餐与价格可能不同，下单时以结算页的机房为准。"),
    ("为什么有的地区只有几条？",
     "本页只列未关闭的活动，不凑数；香港、阿姆斯特丹等小节当前条数少，"
     "是真实供给少，不是漏抓，新增活动会在每日更新里补上。"),
    ("本站为什么不写具体延迟数字？",
     "延迟因测试地点、时段与线路而异，抓取到的帖文也不含可信的统一测速；"
     "不编数字，下单前请用商家提供的测试 IP 自行验证。"),
    ("数据从哪来？",
     "数据来自 LowEndTalk Offers 与 LowEndBox 的公开帖子，自动抓取解析；"
     "每条优惠保留原帖链接，可点开溯源核对。"),
]

# 地区分组：原始数据里的地区是英文，这里做中英文对照。
# 一个商家在多个机房有活动时，会在多个小节各出现一次（按机房分节是本意）；
# 同一小节内按原帖 URL 去重，避免同一帖子因地区词重复而出现两次。
REGIONS: list[tuple[str, list[str], str]] = [
    ("美国", ["Los Angeles", "Dallas", "New York", "Miami", "Chicago",
             "Phoenix", "Atlanta", "Buffalo", "Salt Lake City", "Texas",
             "Kansas"],
     "美国机房选择多，适合面向北美用户或对价格敏感的场景；"
     "下单前确认具体城市与线路，同一国家不同城市体验可能差很多。"),
    ("欧洲", ["Frankfurt", "Germany", "Amsterdam", "Netherlands", "London",
             "France", "Paris", "Milan", "Romania", "Warsaw", "Finland"],
     "欧洲机房以法兰克福、阿姆斯特丹、伦敦为主；"
     "面向欧洲用户优先选当地机房，面向国内用户则要看具体线路。"),
    ("新加坡", ["Singapore"],
     "新加坡机房面向亚太用户延迟通常较友好，"
     "也是不少低价年付活动的常见机房。"),
    ("香港", ["Hong Kong"],
     "香港机房当前未关闭活动较少，本节不凑数；"
     "新增活动会在每日更新里补上。"),
    ("其他地区", ["Tokyo", "Japan", "Canada", "Montreal", "India",
                 "Australia"],
     "日本、加拿大、印度、澳大利亚等机房的未关闭活动较少，"
     "统一放在本节，表格里仍标注原始机房名。"),
]


# --------------------------------------------------------------------------
# 筛选
# --------------------------------------------------------------------------

def _open_records(records: list[dict]) -> list[dict]:
    """未关闭的记录：已关闭状态一律不进常青榜。"""
    return [r for r in records if r.get("availability") not in _CLOSED]


def cheap_records(records: list[dict]) -> list[tuple[float, dict]]:
    """低价年付：年化 30 欧元以内、未关闭，按年价升序。"""
    out = []
    for r in _open_records(records):
        annual = _annual_eur(r)
        if annual is None or annual > 30:
            continue
        out.append((annual, r))
    out.sort(key=lambda t: t[0])
    return out


def region_records(records: list[dict]) -> list[tuple[str, str, list[dict]]]:
    """按地区分组的未关闭记录；每组内按原帖 URL 去重。"""
    open_recs = _open_records(records)
    groups = []
    for name, locs, note in REGIONS:
        seen: set[str] = set()
        rows = []
        for r in open_recs:
            if not any(loc in (r.get("locations") or []) for loc in locs):
                continue
            key = r.get("url") or r.get("provider") or ""
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
        groups.append((name, note, rows))
    return groups


# --------------------------------------------------------------------------
# 表格片段（全部值经 html.escape）
# --------------------------------------------------------------------------

def _cheap_row(rec: dict, annual: float) -> str:
    esc = html.escape
    cells = [
        ("商家", esc(rec.get("provider") or "") + "<br>" + _purchase_html(rec)),
        ("配置", esc(rec.get("display_spec") or "—")),
        ("报价", _price_html(rec) + "<small>≈ €%.2f/年（近似折算）</small>" % annual),
        ("库存", _av_html(rec)),
    ]
    return '<tr class="row">' + ''.join(
        '<td data-label="%s">%s</td>' % c for c in cells) + '</tr>'


def _region_row(rec: dict) -> str:
    esc = html.escape
    locs = ", ".join(rec.get("locations") or [])
    annual = _annual_eur(rec)
    price = _price_html(rec)
    if annual is not None:
        price += "<small>≈ €%.2f/年（近似折算）</small>" % annual
    cells = [
        ("商家", esc(rec.get("provider") or "") + "<br>" + _purchase_html(rec)),
        ("机房", esc(locs) if locs else "—"),
        ("配置", esc(rec.get("display_spec") or "—")),
        ("报价", price),
        ("库存", _av_html(rec)),
    ]
    return '<tr class="row">' + ''.join(
        '<td data-label="%s">%s</td>' % c for c in cells) + '</tr>'


def _table(headers: list[str], rows: list[str]) -> str:
    if not rows:
        return '<p class="empty">当前暂无未关闭的优惠。</p>'
    return ('<div class="tblwrap"><table><thead><tr>'
            + ''.join('<th>%s</th>' % h for h in headers)
            + '</tr></thead><tbody>%s</tbody></table></div>'
            % "\n".join(rows))


def _cheap_sections(records: list[dict]) -> str:
    cheap = cheap_records(records)
    if not cheap:
        return '<p class="empty">当前暂无可比的低价年付优惠。</p>'
    sections = []
    for title, lo, hi in (("年化 15 欧元以内", 0, 15),
                          ("年化 15–30 欧元", 15, 30)):
        rows = [_cheap_row(r, annual) for annual, r in cheap
                if lo <= annual <= hi] if hi == 15 else \
               [_cheap_row(r, annual) for annual, r in cheap
                if lo < annual <= hi]
        sections.append(
            '<h3>%s（%d 条）</h3>' % (title, len(rows))
            + _table(["商家", "配置", "报价", "库存"], rows))
    return "\n".join(sections)


def _region_sections(records: list[dict]) -> str:
    parts = []
    for name, note, rows in region_records(records):
        parts.append(
            '<h3>%s（%d 条）</h3><p class="note">%s</p>'
            % (html.escape(name), len(rows), html.escape(note))
            + _table(["商家", "机房", "配置", "报价", "库存"],
                     [_region_row(r) for r in rows]))
    return "\n".join(parts)


def _faq_html(faq: list[tuple[str, str]]) -> str:
    return "\n".join(
        '<details><summary>%s</summary><p>%s</p></details>' %
        (html.escape(q), html.escape(a)) for q, a in faq)


def _og_html(title: str, desc: str, path: str) -> str:
    if not config.SITE_URL:
        return ""
    esc = html.escape
    page_url = f"{config.SITE_URL}/{path}/"
    img = f"{config.SITE_URL}/og-image.png"
    t = esc(title, quote=True)
    d = esc(desc, quote=True)
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


def _style_html() -> str:
    """复用黑五页的样式块，避免两套 CSS 各自漂移。"""
    tmpl = bfpage.BF_TEMPLATE
    start = tmpl.index("<style>")
    end = tmpl.index("</style>") + len("</style>")
    return tmpl[start:end]


# --------------------------------------------------------------------------
# 页面模板
# --------------------------------------------------------------------------

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>@@TITLE@@</title>
<meta name="description" content="@@DESC@@">
@@CANONICAL@@
@@OGTAGS@@
<link rel="icon" href="../favicon.ico">
<link rel="alternate" type="application/rss+xml" title="RSS 订阅" href="@@FEED@@">
<meta name="theme-color" content="#75ADFB">
@@STYLE@@
@@JSONLD@@
</head>
<body>
<div class="wrap">

<header>
  <p class="crumb"><a href="@@HOME@@">← 返回 VPS 优惠总览</a></p>
  <h1>@@H1@@</h1>
  <p class="lead">@@LEAD@@</p>
</header>

<section class="card">
@@SECTIONS@@
</section>

<section class="card faq">
  <h2>常见问题</h2>
@@FAQ@@
</section>

<footer>
  <div>价格与库存以商家官网为准，标注「待确认」≠有货。</div>
  <div>下单前请核对首期与续费总价、机房、流量与退款条件。</div>
  <div>部分链接为推广链接，符合商家规则的订单可能为本站带来佣金；本站仅聚合公开信息，不构成购买建议。</div>
  <div>数据更新时间 <time datetime="@@GEN_DT@@">@@GEN_TXT@@</time>（北京时间） · <a href="@@FEED@@">RSS 订阅</a> · <a href="@@HOME@@">返回主站</a></div>
</footer>

</div>
</body>
</html>
"""


def _render_page(payload: dict, path: str, title: str, desc: str,
                 h1: str, lead: str, sections: str,
                 faq: list[tuple[str, str]], jsonld_html: str = "",
                 feed_href: str = "../feed.xml",
                 home_href: str = "../") -> str:
    esc = html.escape
    generated_at = payload.get("generated_at") or ""
    canonical = (f'<link rel="canonical" href="{config.SITE_URL}/{path}/">'
                 if config.SITE_URL else "")
    return (PAGE_TEMPLATE
            .replace("@@TITLE@@", esc(f"{title} - {config.SITE_TITLE}"))
            .replace("@@DESC@@", esc(desc, quote=True))
            .replace("@@CANONICAL@@", canonical)
            .replace("@@OGTAGS@@", _og_html(title, desc, path))
            .replace("@@FEED@@", esc(feed_href, quote=True))
            .replace("@@STYLE@@", _style_html())
            .replace("@@JSONLD@@", jsonld_html or "")
            .replace("@@HOME@@", esc(home_href, quote=True))
            .replace("@@H1@@", esc(h1))
            .replace("@@LEAD@@", lead)
            .replace("@@SECTIONS@@", sections)
            .replace("@@FAQ@@", _faq_html(faq))
            .replace("@@GEN_DT@@", esc(generated_at, quote=True))
            .replace("@@GEN_TXT@@",
                     esc(display_time(generated_at).replace(" 北京时间", ""))))


def render_cheap_page(payload: dict, jsonld_html: str = "", **kwargs) -> str:
    records = payload.get("records") or []
    count = len(cheap_records(records))
    lead = ('低价年付总榜：年化 30 欧元以内、未关闭的优惠，按近似年价升序排列，'
            '共 <b>%d</b> 条。已关闭活动已剔除；多数条目标注「待确认」，'
            '下单前请到商家核实。黑五期间先看'
            ' <a href="../black-friday-2026/">黑五 2026 专题</a>，'
            '按地区挑机房看 <a href="../by-region/">按地区选 VPS</a>。'
            % count)
    return _render_page(payload, CHEAP_PATH, CHEAP_TITLE, CHEAP_DESC,
                        "低价年付 VPS 汇总", lead,
                        _cheap_sections(records), CHEAP_FAQ,
                        jsonld_html=jsonld_html, **kwargs)


def render_region_page(payload: dict, jsonld_html: str = "", **kwargs) -> str:
    lead = ('按机房地区分节的 VPS 优惠清单，只列未关闭活动，不凑数；'
            '同一商家在多个机房有活动时会在多个小节出现。'
            '想按价格挑，先看 <a href="../cheap-yearly/">低价年付总榜</a>，'
            '黑五活动看 <a href="../black-friday-2026/">黑五 2026 专题</a>。')
    return _render_page(payload, REGION_PATH, REGION_TITLE, REGION_DESC,
                        "按地区选 VPS", lead,
                        _region_sections(payload.get("records") or []),
                        REGION_FAQ, jsonld_html=jsonld_html, **kwargs)


def write_evergreen_pages(payload: dict, out_dir, jsonld_map: dict | None = None,
                          **kwargs) -> list[str]:
    """写出两页常青页，返回文件路径列表（幂等、可重复写入）。

    jsonld_map 可按路径给不同的结构化数据（面包屑 + 各页 FAQ），
    不给时两页都不带 JSON-LD —— 调用方应优先传 map，保证可见 FAQ
    与 FAQPage 结构化数据同源。
    """
    paths = []
    for path, render in ((CHEAP_PATH, render_cheap_page),
                         (REGION_PATH, render_region_page)):
        page_dir = os.path.join(str(out_dir), path)
        os.makedirs(page_dir, exist_ok=True)
        file_path = os.path.join(page_dir, "index.html")
        page_kwargs = dict(kwargs)
        if jsonld_map and path in jsonld_map:
            page_kwargs["jsonld_html"] = jsonld_map[path]
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(render(payload, **page_kwargs))
        paths.append(file_path)
    return paths
