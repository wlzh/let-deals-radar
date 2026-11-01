"""结构化数据测试：JSON-LD 字段取舍与 <script> 注入防护。"""

from __future__ import annotations

import json

from letdeals import config, structured

TAG_OPEN = '<script type="application/ld+json">'
TAG_CLOSE = "</script>\n"


def _rec(**over):
    """一条 POST-enrich 形状的记录，字段与 presentation.prepare 产物对齐。"""
    rec = {
        "thread_id": "1", "source": "let", "provider": "BERO HOST",
        "title": "10th Anniversary", "url": "https://forum.example.com/t/1",
        "vendor_url": "https://vendor.example.com/?aff=1",
        "availability": "ON_SALE", "event_type": "anniversary",
        "discount": {"strength": "循环/永久 · 码 LET10", "percent_max": 10,
                     "recurring": True, "codes": ["LET10"], "extras": []},
        "display_spec": "2 核 / 6 GB 内存 / 60 GB 存储",
        "display_price": "€39/年",
        "display_price_note": "Anniversary S · 自动解析，需核对",
        "sort_price": {"amount": 39, "currency": "€", "cycle": "year"},
        "flagship_price": None, "confidence": 1.0,
    }
    rec.update(over)
    return rec


# --------------------------------------------------------------------------
# WebSite
# --------------------------------------------------------------------------

def test_website_schema_basic_fields():
    s = structured.website_schema({"records": []})
    assert s["@context"] == "https://schema.org"
    assert s["@type"] == "WebSite"
    assert s["name"] == config.SITE_TITLE
    assert s["description"] == config.SITE_DESC
    assert s["inLanguage"] == "zh-CN"
    assert s["publisher"] == {"@type": "Organization",
                              "name": config.SITE_TITLE}


def test_website_schema_omits_url_when_empty(monkeypatch):
    """未绑定域名时不能输出空 url。"""
    monkeypatch.setattr(config, "SITE_URL", "")
    assert "url" not in structured.website_schema({})
    monkeypatch.setattr(config, "SITE_URL", "https://x.test")
    assert structured.website_schema({})["url"] == "https://x.test"


# --------------------------------------------------------------------------
# ItemList
# --------------------------------------------------------------------------

def test_item_list_prefers_vendor_url():
    s = structured.item_list_schema([_rec()])
    assert s["@type"] == "ItemList"
    li = s["itemListElement"][0]
    assert li["position"] == 1
    assert li["url"] == "https://vendor.example.com/?aff=1"
    assert li["name"] == "BERO HOST €39/年"
    assert li["item"]["@type"] == "Product"
    assert li["item"]["url"] == "https://vendor.example.com/?aff=1"


def test_item_list_falls_back_to_thread_url():
    s = structured.item_list_schema([_rec(vendor_url=None)])
    li = s["itemListElement"][0]
    assert li["url"] == "https://forum.example.com/t/1"
    assert li["item"]["url"] == "https://forum.example.com/t/1"


def test_item_list_offer_price_and_currency():
    rec = _rec(sort_price={"amount": 12, "currency": "$", "cycle": "year"})
    offer = structured.item_list_schema([rec])["itemListElement"][0]["item"]["offers"]
    assert offer == {"@type": "Offer", "price": 12, "priceCurrency": "USD",
                     "availability": "InStock",
                     "url": "https://vendor.example.com/?aff=1"}


def test_item_list_offer_falls_back_to_flagship_price():
    rec = _rec(sort_price=None,
               flagship_price={"amount": 129, "currency": "€", "cycle": "year"})
    offer = structured.item_list_schema([rec])["itemListElement"][0]["item"]["offers"]
    assert offer["price"] == 129
    assert offer["priceCurrency"] == "EUR"


def test_item_list_skips_offer_without_price():
    """没有数字价格就不输出 Offer —— 残缺的 Offer 会被判为无效结构。"""
    rec = _rec(sort_price=None, flagship_price=None, display_price="到商家查价",
               discount={"strength": "", "percent_max": None,
                         "recurring": False, "codes": [], "extras": []})
    product = structured.item_list_schema([rec])["itemListElement"][0]["item"]
    assert "offers" not in product
    assert product["description"].endswith("到商家查价")


def test_item_list_skips_offer_for_unknown_currency():
    rec = _rec(sort_price={"amount": 5, "currency": "¥", "cycle": "month"})
    product = structured.item_list_schema([rec])["itemListElement"][0]["item"]
    assert "offers" not in product


def test_item_list_availability_unverified_omitted():
    """未核实的库存状态不能对搜索引擎声称有货。"""
    rec = _rec(availability="UNVERIFIED")
    offer = structured.item_list_schema([rec])["itemListElement"][0]["item"]["offers"]
    assert "availability" not in offer


def test_item_list_availability_sold_out():
    rec = _rec(availability="SOLD_OUT")
    offer = structured.item_list_schema([rec])["itemListElement"][0]["item"]["offers"]
    assert offer["availability"] == "SoldOut"


def test_item_list_description_includes_codes():
    s = structured.item_list_schema([_rec()])
    desc = s["itemListElement"][0]["item"]["description"]
    assert desc.startswith("2 核 / 6 GB 内存 / 60 GB 存储 · €39/年")
    assert desc.endswith("优惠码 LET10")


def test_item_list_limit_keeps_caller_order():
    recs = [_rec(provider="P%d" % i, thread_id=str(i)) for i in range(5)]
    s = structured.item_list_schema(recs, limit=3)
    assert [e["position"] for e in s["itemListElement"]] == [1, 2, 3]
    assert s["itemListElement"][0]["name"].startswith("P0 ")


def test_item_list_empty():
    assert structured.item_list_schema([])["itemListElement"] == []


def test_item_list_roundtrip():
    recs = [_rec(),
            _rec(provider="godlike.host", availability="UNVERIFIED",
                 sort_price=None, flagship_price=None,
                 display_price="到商家查价")]
    s = structured.item_list_schema(recs)
    assert json.loads(json.dumps(s)) == s


# --------------------------------------------------------------------------
# 注入防护
# --------------------------------------------------------------------------

def test_script_html_cannot_break_out():
    """帖文数据里的 </script> 必须被转义，不能提前闭合标签。"""
    payload = "</script><script>alert(1)</script>"
    rec = _rec(provider=payload, display_spec=payload, title=payload)
    html = structured.script_html(structured.item_list_schema([rec]))
    assert "</script><script>" not in html
    assert payload not in html
    # 数据不能丢：反转义后要能原样取回
    body = html[len(TAG_OPEN):-len(TAG_CLOSE)]
    assert json.loads(body)["itemListElement"][0]["item"]["name"] \
        == payload + " VPS 优惠"


def test_script_html_format():
    html = structured.script_html(structured.website_schema({}))
    assert html.startswith(TAG_OPEN)
    assert html.endswith(TAG_CLOSE)
    body = html[len(TAG_OPEN):-len(TAG_CLOSE)]
    assert json.loads(body)["@type"] == "WebSite"


# --------------------------------------------------------------------------
# FAQ / 面包屑
# --------------------------------------------------------------------------

def test_faq_schema_roundtrip():
    faq = [
        ("什么是循环折扣？",
         '指续费时仍按活动价结算，例如「永久 6 折」，不是只降首期。'),
        ("帖文报价含税吗？",
         '不一定。以商家结账页为准（是否含 "VAT" 需逐一核对）。'),
    ]
    s = structured.faq_schema(faq)
    assert s["@context"] == "https://schema.org"
    assert s["@type"] == "FAQPage"
    q0 = s["mainEntity"][0]
    assert q0["@type"] == "Question"
    assert q0["name"] == faq[0][0]
    assert q0["acceptedAnswer"] == {"@type": "Answer", "text": faq[0][1]}
    # 含引号与中文的问答要能完整序列化并还原
    assert json.loads(json.dumps(s)) == s


def test_breadcrumb_positions():
    items = [("首页", "https://x.test/"), ("黑五汇总", "https://x.test/bf/")]
    s = structured.breadcrumb_schema(items)
    assert s["@context"] == "https://schema.org"
    assert s["@type"] == "BreadcrumbList"
    assert [e["position"] for e in s["itemListElement"]] == [1, 2]
    assert s["itemListElement"][1]["name"] == "黑五汇总"
    assert s["itemListElement"][1]["item"] == "https://x.test/bf/"
