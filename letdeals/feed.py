"""订阅出口：RSS 2.0 与 llms.txt。

RSS 给阅读器聚合用；llms.txt（llmstxt.org 约定）给生成式引擎一个
纯文本的「站点是什么 + 数据在哪」入口。两者都是纯静态产物，
随站点一起发布，不引入任何运行时依赖。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from itertools import islice
from xml.sax.saxutils import escape

from . import config
from .extract import AVAIL_CN, EVENT_CN
from .updates import display_time

# 属性值里也会出现文本（link/guid），引号一并转义才安全
_XML_EXTRA = {'"': "&quot;", "'": "&#39;"}


def _xml_escape(value) -> str:
    """XML 文本/属性转义。数据来自抓取内容，一律不可信。"""
    return escape(str(value if value is not None else ""), _XML_EXTRA)


def _parse_posted(value) -> datetime | None:
    """RFC822 发布时间 → aware datetime；缺失或解析失败返回 None。"""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:                     # 无时区按 UTC 处理，便于比较
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_generated(value) -> datetime | None:
    """ISO generated_at → aware datetime；缺失或解析失败返回 None。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _sorted_records(records: list[dict]) -> list[dict]:
    """最新优先排序。

    优先用 posted 的精确时间；没有的用 age_days 折算成近似时间
    （同一个 now 基准，相对顺序只取决于天数）；两者都缺就垫底、
    保持原序。key 末位带原始下标，保证排序稳定。
    """
    now = datetime.now(timezone.utc)

    def key(indexed: tuple[int, dict]) -> tuple:
        i, rec = indexed
        dt = _parse_posted(rec.get("posted"))
        ts = dt.timestamp() if dt else None
        if ts is None:
            age = rec.get("age_days")
            if isinstance(age, (int, float)) and not isinstance(age, bool):
                ts = (now - timedelta(days=float(age))).timestamp()
        if ts is None:
            return (1, 0.0, i)
        return (0, -ts, i)                    # 取负：时间越新越靠前

    return [rec for _, rec in sorted(enumerate(records), key=key)]


def _description(rec: dict) -> str:
    """单行摘要：规格 · 报价 · 活动性质 · 库存（+ 优惠码）。"""
    parts = [
        str(rec.get("display_spec") or ""),
        "报价 %s" % (rec.get("display_price") or "到商家查价"),
        EVENT_CN.get(rec.get("event_type"), ""),
        "库存 %s" % AVAIL_CN.get(rec.get("availability"), "待确认"),
    ]
    codes = (rec.get("discount") or {}).get("codes") or []
    if codes:
        parts.append("优惠码 %s" % "/".join(str(c) for c in codes))
    # 摘要必须是单行：帖文里的换行会撑破 RSS 阅读器的展示
    return " · ".join(p for p in parts if p).replace("\r", " ").replace("\n", " ")


def _item_xml(rec: dict, url: str) -> str:
    """一条 <item>。posted 无法解析时省略 pubDate，宁缺勿错。"""
    from .details import detail_path
    landing = config.SITE_URL.rstrip("/") + detail_path(rec) if config.SITE_URL else url
    lines = [
        "    <item>",
        "      <title>%s</title>"
        % _xml_escape("%s：%s" % (rec.get("provider") or "", rec.get("title") or "")),
        f"      <link>{_xml_escape(landing)}</link>",
        f'      <guid isPermaLink="true">{_xml_escape(url)}</guid>',
    ]
    dt = _parse_posted(rec.get("posted"))
    if dt:
        lines.append(f"      <pubDate>{format_datetime(dt)}</pubDate>")
    lines += [
        f"      <description>{_xml_escape(_description(rec))}</description>",
        "    </item>",
    ]
    return "\n".join(lines)


def _items_xml(records: list[dict]):
    """按最新优先产出 <item> XML；空 url 的记录直接跳过。"""
    for rec in _sorted_records(records):
        url = str(rec.get("url") or "").strip()
        if not url:
            continue
        yield _item_xml(rec, url)


def rss_feed(payload: dict, limit: int = 50) -> str:
    """把 payload 渲染成 RSS 2.0（不带命名空间，消费方最省心）。"""
    items = list(islice(_items_xml(payload.get("records") or []), max(limit, 0)))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "  <channel>",
        f"    <title>{_xml_escape(config.SITE_TITLE)}</title>",
        f"    <link>{_xml_escape(config.SITE_URL)}</link>",
        f"    <description>{_xml_escape(config.SITE_DESC)}</description>",
        "    <language>zh-CN</language>",
    ]
    generated = _parse_generated(payload.get("generated_at"))
    if generated:
        lines.append(f"    <lastBuildDate>{format_datetime(generated)}</lastBuildDate>")
    lines.extend(items)
    lines += ["  </channel>", "</rss>", ""]
    return "\n".join(lines)


def llms_txt(payload: dict) -> str:
    """按 llmstxt.org 约定生成 llms.txt。

    未配置域名时用相对路径 —— 宁可给相对路径，也不生成无效绝对地址。
    """
    base = (config.SITE_URL or "").rstrip("/")

    def path(rel: str) -> str:
        return f"{base}/{rel}" if base else rel

    by = payload.get("by_source") or {}
    count = payload.get("count") or 0
    lines = [
        f"# {config.SITE_TITLE}",
        "",
        f"> {config.SITE_DESC}",
        "",
        "## 站点说明",
        "",
        "本站自动汇总 LowEndTalk / LowEndBox 公开渠道的 VPS 优惠，"
        "标注折扣力度、优惠码与库存状态。数据每日自动更新"
        "（北京时间 05:00 检查）。",
        f"当前共 {count} 条优惠，其中 LET {by.get('let', 0)} 条 / LEB {by.get('leb', 0)} 条。",
        f"数据生成时间：{display_time(payload.get('generated_at'))}。",
        "",
        "## 数据",
        "",
        "- %s: 全部优惠的结构化数据（字段说明：records[].provider/title/url/display_price/availability/…）"
        % path("data/deals.json"),
        f"- {path('data/updates.json')}: 每日变化记录",
        f"- {path('feed.xml')}: RSS 订阅",
        f"- {path('black-friday-2026/')}: 2026 黑五专题页（低价年付清单与常见问题）",
        f"- {path('sitemap.xml')}: 站点页面与稳定优惠详情地址；详情链接也见 records[].detail_path",
        "",
        "## 使用与免责",
        "",
        "- 信息来自公开帖子，库存与价格以商家为准；页面标注「待确认」表示未自动验证。",
        "- 本站仅聚合信息，不构成购买建议。部分出站链接为推广链接。",
        "- price_bound=false 的报价未与配置绑定；generated_at 是数据整理时间，不是商家确认时间。",
        "",
    ]
    return "\n".join(lines)
