"""订阅出口测试：RSS 2.0 结构、XML 转义、排序与 llms.txt 内容。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from letdeals import config, feed


def _rec(url="https://example.com/t", posted="", age_days=1, **over):
    rec = {
        "thread_id": "1", "source": "let", "provider": "ExampleHost",
        "title": "Cheap VPS", "url": url, "author": "op",
        "posted": posted, "age_days": age_days,
        "availability": "UNVERIFIED", "event_type": "sale",
        "discount": {"strength": "30% off", "codes": ["CODE1"]},
        "display_spec": "1C/1G", "display_price": "€12/年",
        "display_price_note": "帖文报价", "confidence": 0.9,
    }
    rec.update(over)
    return rec


def _payload(records):
    return {"source": "test", "generated_at": "2026-10-30T21:00:00Z",
            "count": len(records),
            "by_source": {"let": len(records), "leb": 0},
            "records": records}


# --------------------------------------------------------------------------
# RSS 2.0
# --------------------------------------------------------------------------

def test_rss_parses_and_respects_limit(monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://deals.example.com")
    payload = _payload([_rec(url=f"https://example.com/{i}") for i in range(3)])
    root = ET.fromstring(feed.rss_feed(payload, limit=2))
    assert root.tag == "rss"
    assert root.get("version") == "2.0"
    ch = root.find("channel")
    assert ch.findtext("title") == config.SITE_TITLE
    assert ch.findtext("link") == "https://deals.example.com"
    assert ch.findtext("description") == config.SITE_DESC
    assert ch.findtext("language") == "zh-CN"
    assert len(ch.findall("item")) == 2


def test_rss_escapes_hostile_text():
    """标题/厂商里的标签、引号、& 必须转义，产出仍是合法 XML。"""
    payload = _payload([_rec(title="evil </title><script>x</script>",
                             provider='A "&" B')])
    out = feed.rss_feed(payload)
    assert "<script>x</script>" not in out
    assert '<title>A "&" B' not in out
    root = ET.fromstring(out)
    item = root.find("channel/item")
    assert "evil" in item.findtext("title")
    assert 'A "&" B' in item.findtext("title")


def test_pubdate_present_for_valid_posted():
    payload = _payload([_rec(posted="Fri, 30 Oct 2026 06:45:44 +0000")])
    root = ET.fromstring(feed.rss_feed(payload))
    pd = root.findtext("channel/item/pubDate")
    assert pd
    assert parsedate_to_datetime(pd) == datetime(
        2026, 10, 30, 6, 45, 44, tzinfo=timezone.utc)


def test_pubdate_omitted_when_posted_empty():
    payload = _payload([_rec(posted="")])
    root = ET.fromstring(feed.rss_feed(payload))
    assert root.find("channel/item/pubDate") is None


def test_newest_first_ordering():
    payload = _payload([
        _rec(url="https://example.com/old",
             posted="Mon, 26 Oct 2026 00:00:00 +0000"),
        _rec(url="https://example.com/new",
             posted="Fri, 30 Oct 2026 00:00:00 +0000"),
    ])
    root = ET.fromstring(feed.rss_feed(payload))
    links = [i.findtext("link") for i in root.findall("channel/item")]
    assert links == ["https://example.com/new", "https://example.com/old"]


def test_ordering_falls_back_to_age_days():
    """没有 posted 时按 age_days 升序（越小越新）。"""
    payload = _payload([
        _rec(url="https://example.com/older", age_days=9),
        _rec(url="https://example.com/newer", age_days=2),
    ])
    root = ET.fromstring(feed.rss_feed(payload))
    links = [i.findtext("link") for i in root.findall("channel/item")]
    assert links == ["https://example.com/newer", "https://example.com/older"]


def test_empty_url_record_skipped():
    payload = _payload([_rec(url=""), _rec(url="https://example.com/ok")])
    root = ET.fromstring(feed.rss_feed(payload))
    items = root.findall("channel/item")
    assert len(items) == 1
    assert items[0].findtext("link") == "https://example.com/ok"


def test_lastbuilddate_reflects_generated_at():
    out = feed.rss_feed(_payload([_rec()]))
    root = ET.fromstring(out)
    lbd = root.findtext("channel/lastBuildDate")
    assert parsedate_to_datetime(lbd) == datetime(
        2026, 10, 30, 21, 0, tzinfo=timezone.utc)


def test_item_description_joins_fields():
    payload = _payload([_rec()])
    root = ET.fromstring(feed.rss_feed(payload))
    desc = root.findtext("channel/item/description")
    assert "1C/1G" in desc
    assert "报价 €12/年" in desc
    assert "库存 待确认" in desc
    assert "优惠码 CODE1" in desc


# --------------------------------------------------------------------------
# llms.txt
# --------------------------------------------------------------------------

def test_llms_txt_relative_paths_without_site_url(monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "")
    out = feed.llms_txt(_payload([_rec()]))
    assert out.startswith("# ")
    assert "data/deals.json" in out
    assert "data/updates.json" in out
    assert "feed.xml" in out
    assert "black-friday-2026/" in out
    assert "共 1 条优惠" in out
    # 未配置域名时不得出现绝对地址
    assert "https://" not in out


def test_llms_txt_absolute_paths_with_site_url(monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://x.test")
    out = feed.llms_txt(_payload([_rec()]))
    assert "https://x.test/data/deals.json" in out
    assert "https://x.test/data/updates.json" in out
    assert "https://x.test/feed.xml" in out
    assert "https://x.test/black-friday-2026/" in out
