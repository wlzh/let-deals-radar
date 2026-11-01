"""SEO/GEO 集成测试：build_site 一并产出 RSS、llms.txt、黑五专题页与结构化数据。

单模块的行为分别由 test_feed / test_bfpage / test_structured 覆盖；
这里验证的是**接线**：首页头部的 JSON-LD/RSS 声明、sitemap 双 URL、
可见 FAQ 与结构化数据同源、?q= 深链与 <time> 时间标注。
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from letdeals import config, render


def _seo_payload():
    """一条黑五 + 一条低价普通记录，覆盖专题页两个列表与 RSS。"""
    return {
        "source": "test", "count": 2, "generated_at": "2026-11-01T00:00:00Z",
        "records": [
            {
                "thread_id": "bf1", "source": "let", "provider": "racknerd.com",
                "title": "RackNerd Black Friday 2026 Sale",
                "url": "https://lowendtalk.com/discussion/1",
                "author": "RackNerd", "posted": "Fri, 30 Oct 2026 06:45:44 +0000",
                "age_days": 1, "age_status": "NEW", "availability": "ON_SALE",
                "event_type": "black_friday",
                "discount": {"strength": "码 BF26", "percent_max": 60,
                             "recurring": False, "codes": ["BF26"], "extras": []},
                "specs": {"vcpu": 4, "ram_gb": 8, "storage_gb": 128,
                          "storage_type": "NVMe", "bandwidth_tb": 8},
                "specs_all": {}, "plans": [], "cpu_models": ["Ryzen 5"],
                "flagship_price": {"amount": 21, "currency": "$", "cycle": "year"},
                "prices": [], "locations": ["Dallas"], "stock_hints": [],
                "replies": None, "confidence": 0.9,
            },
            {
                "thread_id": "n1", "source": "leb", "provider": "cheap.example",
                "title": "Cheap yearly VPS", "url": "https://lowendbox.com/x",
                "author": "editor", "posted": "Thu, 29 Oct 2026 08:00:00 +0000",
                "age_days": 2, "age_status": "NEW", "availability": "UNVERIFIED",
                "event_type": "sale",
                "discount": {"strength": "", "percent_max": None,
                             "recurring": False, "codes": [], "extras": []},
                "specs": {"vcpu": 1, "ram_gb": 1, "storage_gb": 15,
                          "storage_type": "NVMe", "bandwidth_tb": None},
                "specs_all": {}, "cpu_models": [],
                "plans": [{"name": "Tiny", "vcpu": 1, "ram_gb": 1,
                           "storage_gb": 15, "storage_type": "NVMe",
                           "bandwidth_tb": 1,
                           "price": {"amount": 9.9, "currency": "$",
                                     "cycle": "year"}}],
                "flagship_price": {"amount": 9.9, "currency": "$", "cycle": "year"},
                "prices": [], "locations": [], "stock_hints": [],
                "replies": None, "confidence": 0.7,
            },
        ],
    }


def _build(tmp_path, monkeypatch=None, **cfg):
    if monkeypatch is not None:
        for key, value in cfg.items():
            monkeypatch.setattr(config, key, value)
    render.build_site(_seo_payload(), str(tmp_path), verbose=False)
    return (tmp_path / "index.html").read_text(encoding="utf-8")


def test_build_site_writes_seo_artifacts(tmp_path):
    render.build_site(_seo_payload(), str(tmp_path), verbose=False)
    feed = tmp_path / "feed.xml"
    assert feed.exists()
    channel = ET.fromstring(feed.read_text(encoding="utf-8")).find("channel")
    assert channel is not None
    assert channel.findtext("title")
    items = channel.findall("item")
    assert len(items) >= 1

    llms = (tmp_path / "llms.txt").read_text(encoding="utf-8")
    for needle in ("data/deals.json", "feed.xml", "black-friday-2026"):
        assert needle in llms

    bf = tmp_path / "black-friday-2026" / "index.html"
    assert bf.exists() and bf.stat().st_size > 1000


def test_sitemap_lists_home_and_bf_page(tmp_path):
    render.build_site(_seo_payload(), str(tmp_path), verbose=False)
    sitemap = (tmp_path / "sitemap.xml").read_text(encoding="utf-8")
    assert "<loc>/</loc>" in sitemap
    assert "<loc>/black-friday-2026/</loc>" in sitemap


def test_index_jsonld_blocks_parse(tmp_path):
    html = _build(tmp_path)
    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    types = [json.loads(b).get("@type") for b in blocks]
    for need in ("WebSite", "ItemList", "FAQPage"):
        assert need in types, f"缺少 {need} 结构化数据"


def test_faq_visible_text_matches_schema(tmp_path):
    """FAQPage 的答案必须与页面可见文本同源（搜索引擎硬要求）。"""
    html = _build(tmp_path)
    for q, a in render.INDEX_FAQ:
        assert q in html, f"FAQ 问题未在页面展示：{q}"
    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    faq = next(json.loads(b) for b in blocks
               if json.loads(b).get("@type") == "FAQPage")
    questions = [e["name"] for e in faq["mainEntity"]]
    assert questions == [q for q, _ in render.INDEX_FAQ]


def test_title_carries_keywords(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://deals.example.com")
    html = _build(tmp_path, monkeypatch, SITE_TITLE="我的VPS雷达",
                  TITLE_KEYWORDS="VPS 优惠 · 黑五 2026")
    m = re.search(r"<title>(.*?)</title>", html)
    assert m and "我的VPS雷达｜VPS 优惠 · 黑五 2026" in m.group(1)
    assert 'property="og:title"' in html and "VPS 优惠 · 黑五 2026" in html


def test_index_links_feed_and_bf_page(tmp_path):
    html = _build(tmp_path)
    assert 'rel="alternate" type="application/rss+xml"' in html
    assert 'href="feed.xml"' in html
    assert 'href="black-friday-2026/"' in html


def test_index_has_machine_readable_times(tmp_path):
    html = _build(tmp_path)
    # 头部数据更新时间 + 无 JS 表格里的发布时间都要带 ISO datetime
    assert '<time datetime="2026-11-01T00:00:00Z">' in html
    assert '<time datetime="2026-10-30T06:45:44+00:00">' in html


def test_bf_page_lists_bf_deals_and_lowprice(tmp_path):
    render.build_site(_seo_payload(), str(tmp_path), verbose=False)
    bf = (tmp_path / "black-friday-2026" / "index.html").read_text(encoding="utf-8")
    # 黑五记录进「已公布的黑五」表；低价记录进「低价年付」表
    assert "racknerd.com" in bf
    assert "cheap.example" in bf
    assert "BF26" in bf


def test_rss_items_link_original_threads(tmp_path):
    render.build_site(_seo_payload(), str(tmp_path), verbose=False)
    feed = (tmp_path / "feed.xml").read_text(encoding="utf-8")
    assert "https://lowendtalk.com/discussion/1" in feed
    assert "https://lowendbox.com/x" in feed


def test_q_deep_link_support(tmp_path):
    html = _build(tmp_path)
    assert "URLSearchParams" in html and "location.search" in html
