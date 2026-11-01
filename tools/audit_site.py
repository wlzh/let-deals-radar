"""Release gate: python -m tools.audit_site [site-directory]."""

import json
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from letdeals import affiliate
from letdeals.presentation import safe_url


def audit(root):
    root = Path(root)
    payload = json.loads((root / "data/deals.json").read_text())
    page = (root / "index.html").read_text()
    rows = payload["records"]
    assert rows and payload["count"] == len(rows), "Empty or inconsistent records"
    assert "@@MERCHANTS@@" not in page and "@@ROWS@@" not in page, "Unrendered template"
    assert "lowseek.ai/d/" not in page, "Legacy forum traffic leakage"
    assert 'rel="canonical"' in page, "Missing canonical URL"
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.urls = []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.urls.append(dict(attrs).get("href", ""))

    parser = Links()
    parser.feed(page)
    mapping = affiliate._load()
    for domain in affiliate.DEFAULTS:
        cfg = mapping.get(domain)
        # 整体替换型推广链接可能落在与配置键不同的域名上（如 royaldata.ro），
        # 页面上出现该链接本身即视为该商家的推广入口已就位。
        matches = [url for url in parser.urls
                   if affiliate._match(url, mapping)[1] == domain
                   or (isinstance(cfg, str) and url == cfg)]
        assert matches, f"Missing merchant affiliate entry: {domain}"
        assert all(affiliate.rewrite(url) == url for url in matches), f"Wrong merchant ID: {domain}"
    covered = 0
    for r in rows:
        assert not r.get("forum_url"), "Legacy forum URL"
        assert r.get("display_price_note") and r.get("display_spec"), "Missing price context"
        url = r.get("vendor_url")
        if url:
            assert safe_url(url), "Unsafe vendor URL"
            assert affiliate.rewrite(url) == url, "Wrong affiliate identifier"
            covered += int(r["affiliate"])
        if not r["price_bound"] and r.get("sort_price"):
            assert "未与配置绑定" in r["display_price_note"], "Unbound price lacks warning"
        if r.get("availability") in ("SOLD_OUT", "ENDED", "RESTOCKING", "LIKELY_SOLD_OUT"):
            assert "下单" not in r["purchase_label"], "Closed offer has purchase CTA"
    result = {"records": len(rows), "affiliate_merchants": len(affiliate.DEFAULTS), "affiliate_records": covered,
              "missing_vendor": sum(not r.get("vendor_url") for r in rows),
              "bound_prices": sum(r["price_bound"] for r in rows), "legacy_forum_links": 0}
    seo_geo_audit(root, payload, page, result)
    print(json.dumps(result, ensure_ascii=False))
    return result


def seo_geo_audit(root: Path, payload: dict, page: str, result: dict) -> None:
    """SEO/GEO 附属产物的发布门禁：RSS、llms.txt、黑五页与结构化数据。"""
    feed = (root / "feed.xml").read_text(encoding="utf-8")
    channel = ET.fromstring(feed).find("channel")
    assert channel is not None and channel.find("title") is not None, "RSS missing channel title"
    assert channel.findtext("link") is not None, "RSS missing channel link"
    result["rss_items"] = len(channel.findall("item"))

    llms = (root / "llms.txt").read_text(encoding="utf-8")
    for needle in ("data/deals.json", "feed.xml", "black-friday-2026"):
        assert needle in llms, f"llms.txt missing {needle}"
    result["llms_bytes"] = len(llms.encode("utf-8"))

    bf = (root / "black-friday-2026/index.html").read_text(encoding="utf-8")
    assert "@@" not in bf, "BF page has unrendered placeholders"
    assert 'type="application/ld+json"' in bf, "BF page missing JSON-LD"
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', bf, re.S):
        json.loads(block)

    # 首页结构化数据：WebSite + ItemList + FAQPage，全部可解析且不逃逸
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    assert len(blocks) >= 3, "index missing JSON-LD blocks"
    types = []
    for block in blocks:
        schema = json.loads(block)
        types.append(schema.get("@type"))
    for need in ("WebSite", "ItemList", "FAQPage"):
        assert need in types, f"index JSON-LD missing {need}"
    assert 'rel="alternate" type="application/rss+xml"' in page, "index missing RSS link"
    # 生产 payload 必有 generated_at；极简 fixture（无时间字段）不强制 <time>
    if payload.get("generated_at"):
        assert "<time datetime=" in page, "index missing machine-readable timestamps"
    assert "black-friday-2026/" in (root / "sitemap.xml").read_text(encoding="utf-8"), \
        "sitemap missing BF page"
    result["jsonld_types"] = types


if __name__ == "__main__":
    audit(sys.argv[1] if len(sys.argv) > 1 else "site")
