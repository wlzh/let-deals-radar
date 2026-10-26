"""数据源与去重测试。"""

from __future__ import annotations

import os

from letdeals.pipeline import dedupe_key, dedupe_records, run
from letdeals.sources import fetch_source, parse_leb_feed, parse_let_feed


# --------------------------------------------------------------------------
# LET 解析
# --------------------------------------------------------------------------

LET_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>Offers</title>
<item>
  <title>TestHost | Deals</title>
  <link>https://lowendtalk.com/discussion/221713/testhost-deals</link>
  <guid isPermaLink="false">221713@/discussions</guid>
  <pubDate>Fri, 03 Oct 2026 12:00:00 +0000</pubDate>
  <dc:creator>someuser</dc:creator>
  <description><![CDATA[<p>8 vCores, 32 GB RAM</p>]]></description>
</item>
</channel></rss>"""


def test_parse_let_feed():
    items = parse_let_feed(LET_XML)
    assert len(items) == 1
    it = items[0]
    assert it["thread_id"] == "221713"
    assert it["source"] == "let"
    assert it["author"] == "someuser"
    assert it["slug"] == "testhost-deals"
    assert "32 GB" in it["description"]


# --------------------------------------------------------------------------
# LEB 解析与分类过滤
# --------------------------------------------------------------------------

def _leb_xml(cats: str, body: str, title: str = "Test Deal") -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>LowEndBox</title>
<item>
  <title>{title}</title>
  <link>https://lowendbox.com/blog/test-deal/</link>
  <pubDate>Mon, 13 Oct 2026 11:00:04 +0000</pubDate>
  <dc:creator>raindog308</dc:creator>
  {cats}
  <content:encoded><![CDATA[{body}]]></content:encoded>
</item>
</channel></rss>"""


def test_parse_leb_deal_kept():
    xml = _leb_xml(
        '<category><![CDATA[Low End Virtual]]></category>',
        "4GB RAM, 25GB disk, 1TB bandwidth for 2.60 EUR/month")
    items = parse_leb_feed(xml)
    assert len(items) == 1
    assert items[0]["source"] == "leb"
    assert items[0]["thread_id"].startswith("leb-")


def test_parse_leb_giveaway_excluded():
    """抽奖类文章不是优惠，应被硬排除。"""
    xml = _leb_xml('<category><![CDATA[Giveaways]]></category>',
                   "Win a free VPS! Enter now.")
    assert parse_leb_feed(xml) == []


def test_parse_leb_news_excluded_without_specs():
    """新闻类文章若无规格描述，应被排除（如「微软砍存储」）。"""
    xml = _leb_xml(
        '<category><![CDATA[Editorial & News]]></category>',
        "Microsoft is reducing your storage by 60%. Plans start at $99/month.")
    assert parse_leb_feed(xml) == []


def test_parse_leb_news_kept_with_specs():
    """新闻分类但正文有真实报价时，应保留。"""
    xml = _leb_xml(
        '<category><![CDATA[Editorial & News]]></category>',
        "4GB RAM, 25GB SSD, 1TB bandwidth, 8 vCPU for $2.60/month")
    items = parse_leb_feed(xml)
    assert len(items) == 1


def test_parse_leb_no_filter_when_disabled():
    xml = _leb_xml('<category><![CDATA[Giveaways]]></category>', "Win!")
    assert len(parse_leb_feed(xml, deals_only=False)) == 1


# --------------------------------------------------------------------------
# 去重
# --------------------------------------------------------------------------

def _rec(provider, amount, vcpu, ram, storage, source="let"):
    return {
        "provider": provider, "source": source,
        "specs": {"vcpu": vcpu, "ram_gb": ram, "storage_gb": storage},
        "flagship_price": ({"amount": amount, "currency": "€", "cycle": "year"}
                           if amount else None),
        "prices": ([{"amount": amount, "currency": "€", "cycle": "year"}]
                   if amount else []),
        "confidence": 0.8, "plans": [], "replies": None,
        "title": "t", "url": "u",
    }


def test_dedupe_key_merges_same_deal():
    a = _rec("Velohost", 26, 1, 4, 25)
    b = _rec("Velohost", 26, 1, 4, 25, source="leb")
    assert dedupe_key(a) == dedupe_key(b)
    assert dedupe_key(a) is not None


def test_dedupe_key_normalizes_provider_case():
    assert dedupe_key(_rec("Velohost", 26, 1, 4, 25)) == \
           dedupe_key(_rec("velohost", 26, 1, 4, 25))


def test_dedupe_key_none_when_no_identity():
    """无价格无规格时不可去重 —— 否则新闻帖会互判重复。"""
    r = _rec("raindog308", None, None, None, None)
    assert dedupe_key(r) is None


def test_dedupe_key_none_without_provider():
    r = _rec("", 26, 1, 4, 25)
    assert dedupe_key(r) is None


def test_dedupe_records_keeps_best():
    a = _rec("Velohost", 26, 1, 4, 25, source="let")
    a["confidence"] = 0.9
    b = _rec("Velohost", 26, 1, 4, 25, source="leb")
    b["confidence"] = 0.5
    out, merged = dedupe_records([a, b])
    assert len(out) == 1
    assert merged == 1
    assert out[0]["source"] == "let"      # 信息更全的胜出


def test_dedupe_records_keeps_unpairable():
    """数据不足的记录应原样保留，不被合并。"""
    a = _rec("Raindog308", None, None, None, None)
    b = _rec("Raindog308", None, None, None, None)
    b["title"] = "different article"
    out, merged = dedupe_records([a, b])
    assert len(out) == 2
    assert merged == 0


def test_dedupe_records_no_false_merge_across_different_deals():
    a = _rec("HostA", 26, 1, 4, 25)
    b = _rec("HostA", 39, 2, 8, 50)
    out, merged = dedupe_records([a, b])
    assert len(out) == 2
    assert merged == 0


# --------------------------------------------------------------------------
# 端到端（离线）
# --------------------------------------------------------------------------

def test_run_offline_let(let_feed):
    payload = run(sources=("let",), offline={"let": let_feed},
                  with_replies=False, limit=10, verbose=False)
    assert payload["count"] > 0
    assert all(r["source"] == "let" for r in payload["records"])
    assert payload["by_source"]["let"] == payload["count"]


def test_run_offline_leb(leb_feed):
    payload = run(sources=("leb",), offline={"leb": leb_feed},
                  with_replies=False, verbose=False)
    assert payload["count"] > 0
    assert all(r["source"] == "leb" for r in payload["records"])


def test_run_both_sources(let_feed, leb_feed):
    payload = run(sources=("let", "leb"),
                  offline={"let": let_feed, "leb": leb_feed},
                  with_replies=False, limit=30, verbose=False)
    assert payload["count"] > 0
    assert set(payload["by_source"]) <= {"let", "leb"}


def test_run_missing_offline_file_does_not_crash():
    """源文件缺失时应优雅降级而非抛异常。"""
    payload = run(sources=("let",), offline={"let": "/nonexistent/feed.xml"},
                  verbose=False)
    assert payload["count"] == 0
    assert payload["records"] == []


def test_run_respects_limit(let_feed):
    payload = run(sources=("let",), offline={"let": let_feed},
                  limit=5, verbose=False)
    assert payload["count"] <= 5


def test_run_applies_recent_days(let_feed):
    payload = run(sources=("let",), offline={"let": let_feed},
                  recent_days=7, verbose=False)
    for r in payload["records"]:
        assert r["age_days"] is not None and r["age_days"] <= 7


# --------------------------------------------------------------------------
# 黄金样本：用真实夹具端到端验证 BERO 十周年帖
# --------------------------------------------------------------------------

def test_bero_golden_sample_from_fixture(let_feed):
    """BERO 十周年帖是所有抽取能力的关键样本，必须端到端稳定。

    验证：周活动识别、表格重建（15 个套餐）、旗舰规格与价格、
    以及厂商名走域名回退（而非标题片段）。
    """
    items = parse_let_feed(open(let_feed, encoding="utf-8").read())
    bero = [it for it in items if "BERO" in it["title"].upper()]
    assert bero, "夹具中缺少 BERO 样本"
    it = bero[0]

    from letdeals.extract import build_record
    r = build_record(it)

    # 活动性质
    assert r["event_type"] == "anniversary"
    # 厂商名：应走域名回退，而不是被裁的标题片段
    assert r["provider"] == "bero-host.de", r["provider"]
    # 旗舰规格（取表格里最大套餐 Anniversary XXL）
    assert r["specs"]["vcpu"] == 8
    assert r["specs"]["ram_gb"] == 32
    assert r["specs"]["storage_gb"] == 300
    assert r["specs"]["bandwidth_tb"] == 25
    # 价格：€129/年
    assert r["flagship_price"]["amount"] == 129
    assert r["flagship_price"]["currency"] == "€"
    # 套餐表完整（该帖有 15 个套餐：三条产品线）
    assert len(r["plans"]) >= 15, f"套餐数 {len(r['plans'])} 少于预期"
    # 表中有 XXL 档
    names = [p["name"] for p in r["plans"]]
    assert "Anniversary XXL" in names
    # 折扣识别
    # The table contains recurring base prices AND a first-year coupon.
    # That does not prove the coupon itself is recurring.
    assert r["discount"]["recurring"] is False
    assert r["discount"]["introductory"] is True
    assert r["discount"]["rule_conflict"] is True


def test_leb_fixture_excludes_news(leb_feed):
    """LEB 夹具里的新闻类文章不应出现在结果中。"""
    items = parse_leb_feed(open(leb_feed, encoding="utf-8").read())
    titles = " ".join(it["title"] for it in items).lower()
    assert "microsoft slashes" not in titles, "新闻类文章未被过滤"
    assert "giveaway" not in titles, "抽奖类文章未被过滤"
    # 真优惠应保留
    assert "velohost" in titles
