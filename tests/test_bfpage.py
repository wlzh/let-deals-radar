"""黑五专题页测试：识别规则、页面产物、转义与链接安全。"""

from __future__ import annotations

import html
import os

import pytest

from letdeals import bfpage, config


def _rec(**kw) -> dict:
    """一条 POST-enrich 形态的最小记录（字段按需覆盖）。"""
    base = {
        "thread_id": "bf-1", "source": "let", "provider": "ExampleHost",
        "title": "Black Friday 2026 Deals", "url": "https://example.com/t/1",
        "vendor_url": "https://example-vendor.com/", "order_url": None,
        "author": "examplehost", "posted": "Thu, 29 Oct 2026 10:00:00 +0000",
        "age_days": 2, "availability": "ON_SALE",
        "event_type": "black_friday",
        "discount": {"strength": "循环 55% OFF · 码 BF26", "percent_max": 55,
                     "recurring": True, "codes": ["BF26"], "extras": []},
        "display_spec": "2 核 / 4 GB 内存 / 60 GB NVMe",
        "display_price": "$21.99/年",
        "display_price_note": "表格套餐 · 自动解析，需核对",
        "purchase_label": "查看活动与下单",
        "annual_eur": 20.23, "score": 88,
    }
    base.update(kw)
    return base


def _payload(records: list[dict]) -> dict:
    return {"count": len(records),
            "generated_at": "2026-11-01T00:00:00Z",
            "records": records}


# --------------------------------------------------------------------------
# is_black_friday 识别
# --------------------------------------------------------------------------

def test_is_black_friday_by_event_type():
    assert bfpage.is_black_friday(
        _rec(event_type="black_friday", title="Anything Goes"))


def test_is_black_friday_by_title_text():
    assert bfpage.is_black_friday(
        _rec(event_type="sale", title="RackNerd Black Friday Sale"))
    assert bfpage.is_black_friday(_rec(event_type="sale", title="年度黑五大促"))
    assert bfpage.is_black_friday(_rec(event_type="sale", title="BF 2026 deals"))


def test_is_black_friday_by_discount_strength():
    assert bfpage.is_black_friday(
        _rec(event_type="sale", title="October promo",
             discount={"strength": "码 BF26"}))


def test_is_black_friday_no_false_positives():
    # 注意 fixture 的 discount.strength 默认含「码 BF26」，
    # 会正确命中黑五正则 —— 这里必须一并覆盖成普通文案。
    assert not bfpage.is_black_friday(
        _rec(event_type="sale", title="USB Flash Drive Sale",
             discount={"strength": "30% off"}))
    assert not bfpage.is_black_friday(
        _rec(event_type="sale", title="BGP VPS in Tokyo",
             discount={"strength": "30% off"}))
    assert not bfpage.is_black_friday(
        _rec(event_type="sale", title="Autumn Sale 30% off",
             discount={"strength": "30% off"}))


# --------------------------------------------------------------------------
# 年化价格排序辅助
# --------------------------------------------------------------------------

def test_annual_eur_prefers_cached_field():
    assert bfpage._annual_eur({"annual_eur": 7.5,
                               "sort_price": {"amount": 99}}) == 7.5


def test_annual_eur_computes_fallback():
    rec = {"sort_price": {"amount": 10, "currency": "$", "cycle": "month"}}
    # $10/月 × 12 × 0.92 = 110.4
    assert bfpage._annual_eur(rec) == pytest.approx(110.4)


def test_annual_eur_missing_price_is_none():
    assert bfpage._annual_eur({}) is None


# --------------------------------------------------------------------------
# 写盘与页面结构
# --------------------------------------------------------------------------

def test_write_bf_page_creates_subdir(tmp_path):
    out = tmp_path / "site"                     # 目录本身也不存在
    path = bfpage.write_bf_page(_payload([_rec()]), out)
    assert path == str(out / bfpage.BF_PATH / "index.html")
    assert os.path.exists(path)
    out_html = open(path, encoding="utf-8").read()
    assert "<h1>2026 黑五便宜 VPS 年付汇总</h1>" in out_html
    assert 'data-start="%s"' % bfpage.BF_START_UTC in out_html
    assert 'data-end="%s"' % bfpage.BF_END_UTC in out_html
    assert "2026 年黑五从北京时间 11 月 27 日 13:00 开始" in out_html
    assert "尚未检出黑五活动" not in out_html
    # 目录已存在时再次写入应幂等
    assert bfpage.write_bf_page(_payload([_rec()]), out) == path


def test_title_and_meta_description():
    out = bfpage.render_bf_page(_payload([]))
    assert ("<title>2026 黑五便宜 VPS 年付汇总｜Black Friday 黑五 VPS "
            "活动与低价清单 - %s</title>" % config.SITE_TITLE) in out
    for kw in ("黑五 VPS", "Black Friday", "低价年付", "优惠码", "LowEndTalk"):
        assert kw in out


def test_faq_questions_rendered():
    out = bfpage.render_bf_page(_payload([]))
    for question, answer in bfpage.BF_FAQ[:2]:
        assert html.escape(question) in out
        assert html.escape(answer) in out


def test_empty_payload_renders_empty_state():
    out = bfpage.render_bf_page(
        {"count": 0, "generated_at": "2026-11-01T00:00:00Z", "records": []})
    assert "尚未检出黑五活动" in out
    assert "共 <b>0</b> 条在跟踪优惠中" in out
    assert out.lstrip().startswith("<!DOCTYPE html>")
    assert out.rstrip().endswith("</html>")


def test_no_unrendered_placeholders():
    out = bfpage.render_bf_page(_payload([_rec()]))
    assert "@@" not in out


def test_jsonld_injected_verbatim():
    marker = ('<script type="application/ld+json">'
              '{"@type":"FAQPage"}</script>')
    out = bfpage.render_bf_page(_payload([_rec()]), jsonld_html=marker)
    assert marker in out
    assert out.index(marker) < out.index("</head>")


# --------------------------------------------------------------------------
# 转义与链接安全
# --------------------------------------------------------------------------

def test_record_fields_are_escaped():
    rec = _rec(title="</script><script>alert(1)</script>",
               provider="<b>x</b>")
    out = bfpage.render_bf_page(_payload([rec]))
    # 注入内容不得以可执行形态出现在页面任何位置
    assert "<script>alert" not in out
    assert "<b>x</b>" not in out
    assert "&lt;b&gt;" in out


def test_purchase_links_carry_sponsored_rel():
    rec = _rec(vendor_url="https://example-vendor.com/?aff=1")
    out = bfpage.render_bf_page(_payload([rec]))
    assert ('<a class="purchase" href="https://example-vendor.com/?aff=1" '
            'target="_blank" rel="noopener nofollow sponsored">'in out)


def test_thread_fallback_link_rel_noopener_only():
    rec = _rec(vendor_url=None)
    out = bfpage.render_bf_page(_payload([rec]))
    assert 'target="_blank" rel="noopener">查看原帖</a>' in out
    # 原帖不是推广位，不应出现 sponsored 标注
    assert 'rel="noopener nofollow sponsored"' not in out


# --------------------------------------------------------------------------
# 低价年付榜
# --------------------------------------------------------------------------

def test_cheap_section_sorts_and_excludes_sold_out():
    plain = {"strength": "20% off", "percent_max": 20,
             "recurring": False, "codes": [], "extras": []}
    cheap = _rec(thread_id="a", provider="CheapHost", event_type="sale",
                 title="Budget KVM Sale", discount=dict(plain),
                 annual_eur=12.0)
    mid = _rec(thread_id="b", provider="MidHost", event_type="sale",
               title="Cloud Sale", discount=dict(plain), annual_eur=30.5)
    sold = _rec(thread_id="c", provider="SoldHost", event_type="sale",
                title="Flash Sale", discount=dict(plain),
                annual_eur=5.0, availability="SOLD_OUT")
    out = bfpage.render_bf_page(_payload([mid, sold, cheap]))
    # 便宜的在前；售罄的整条不出现在低价榜
    assert out.index("CheapHost") < out.index("MidHost")
    assert "SoldHost" not in out


# --------------------------------------------------------------------------
# 域名配置联动（canonical / OG）
# --------------------------------------------------------------------------

def test_canonical_and_og_follow_site_url(monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://deals.example.com")
    out = bfpage.render_bf_page(_payload([]))
    assert ('<link rel="canonical" '
            'href="https://deals.example.com/black-friday-2026/">') in out
    assert ('<meta property="og:url" '
            'content="https://deals.example.com/black-friday-2026/">') in out
    assert '<meta name="twitter:card" content="summary_large_image">' in out


def test_canonical_and_og_omitted_without_site_url(monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "")
    out = bfpage.render_bf_page(_payload([]))
    assert "canonical" not in out
    assert "og:url" not in out
