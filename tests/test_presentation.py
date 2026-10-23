from copy import deepcopy

from letdeals import affiliate, render
from letdeals.extract import build_record, extract_discount, extract_provider
from letdeals.presentation import prepare, safe_url
from letdeals.textutil import parse_flat_tables


def test_unbound_maxima_never_advertised_as_one_plan():
    r = prepare({"title": "FlipperHost 2GB VPS $29.99/year", "specs": {"ram_gb": 256},
                 "specs_all": {"ram_gb": [2, 256]}, "prices": [{"amount": 29.99, "currency": "$", "cycle": "year"}]})
    assert not r["price_bound"]
    assert "2–256" in r["display_spec"]
    assert "未与配置绑定" in r["display_price_note"]
    assert r["display_price"] == "$29.99/年"


def test_bound_plan_price_and_specs_are_selected_together():
    r = prepare({"plans": [{"name": "Large", "vcpu": 4, "ram_gb": 8,
        "price": {"amount": 20, "currency": "$", "cycle": "month"}}]})
    assert r["price_bound"]
    assert r["display_price"] == "$20/月"
    assert "8 GB" in r["display_spec"]


def test_old_plan_does_not_invent_yearly_cycle():
    r = prepare({"plans": [{"name": "X", "price": {"amount": 20, "currency": "$"}}],
                 "flagship_price": {"amount": 20, "currency": "$", "cycle": "year"}})
    assert not r["price_bound"]
    assert r["display_price"] == "到商家查价"


def test_table_cycle_is_not_always_year():
    for cycle in ("year", "month", "quarter"):
        plans = parse_flat_tables(f"Price/{cycle}\nSmall\n2 Cores\n4 GB\n40 GB\n$12")
        assert plans[0]["price"]["cycle"] == cycle
    assert parse_flat_tables("Small\n2 Cores\n4 GB\n40 GB\n$12")[0]["price"]["cycle"] is None


def test_false_coupons_filtered():
    for word in ("HTTPS", "WORKS", "APPLIES", "AUTOMATICALLY", "DOES", "NOT", "SIGNED", "VALIDITY"):
        assert not extract_discount("code " + word)["codes"]
    assert "LET2026" in extract_discount("coupon LET2026")["codes"]


def test_brand_aliases_and_affiliate_fallback():
    for title, brand, value in (("[LayerOne] More Capacity", "LayerOne", "KMA9RZRD"),
                                ("[MY/SG/US] SVR4U KVM VPS", "SVR4U", "aff=56")):
        assert extract_provider(title, "", "") == brand
        r = prepare({"title": title, "vendor_url": None})
        assert value in r["vendor_url"]
        assert r["affiliate"]


def test_unique_purchase_domain_recovers_missing_vendor():
    r = build_record({"title": "More Capacity + New Features", "description":
                      '<a href="https://vendor-example.com/store/vps">Buy</a>'})
    assert r["vendor_url"] == "https://vendor-example.com/store/vps"


def test_two_purchase_domains_are_not_guessed():
    r = build_record({"title": "More Capacity + New Features", "description":
        '<a href="https://a.example/store">A</a><a href="https://b.example/store">B</a>'})
    assert r["vendor_url"] is None


def test_prepare_is_idempotent_and_drops_forum_link():
    r = prepare({"title": "[LayerOne] New plans", "forum_url": "https://lowseek.ai/d/1",
                 "discount": {"codes": ["WORKS", "LET25"], "strength": "码 WORKS/LET25"}})
    assert prepare(deepcopy(r)) == r
    assert r["forum_url"] is None
    assert r["discount"]["codes"] == ["LET25"]


def test_untrusted_urls_rejected():
    for url in ("javascript:alert(1)", "https://racknerd.com@evil.test/", "https://[bad"):
        assert safe_url(url) is None
        assert affiliate._domain_of(url) == ""


def test_query_duplicates_preserved_and_affiliate_replaced():
    result = affiliate.append_param("https://a.test/?x=1&x=2&REF=old", "ref", "new")
    assert "x=1&x=2" in result and "old" not in result and result.count("ref=") == 1


def test_closed_offer_has_non_urgent_call_to_action():
    assert "补货" in prepare({"availability": "SOLD_OUT"})["purchase_label"]
    assert "核对" in prepare({"availability": "UNVERIFIED"})["purchase_label"]


def test_page_conversion_controls_and_single_affiliate_source(tmp_path):
    render.build_site({"records": [], "count": 0}, tmp_path, verbose=False)
    page = (tmp_path / "index.html").read_text()
    for text in ("id=\"budget\"", "id=\"location\"", "data-preset=\"budget\"", "核对套餐与优惠码", "可能为本站带来佣金"):
        assert text in page
    assert "长期在用" not in page and "常年有货" not in page
    assert "lowseek.ai/d/" not in page
    assert "@@MERCHANTS@@" not in page


def test_missing_source_does_not_overwrite_site(monkeypatch):
    from letdeals import cli
    monkeypatch.setattr(cli.pipeline, "run", lambda **kw: {"records": [{}], "source_failures": ["let"]})
    def unexpected(*args, **kwargs):
        raise AssertionError("Partial feed must not overwrite existing data")
    monkeypatch.setattr(cli.pipeline, "save", unexpected)
    assert cli.main(["--quiet"]) == 1


def test_affiliate_audit_preserves_all_fourteen_merchants(tmp_path, monkeypatch):
    from tools.audit_site import audit
    monkeypatch.setattr(render.config, "SITE_URL", "https://vps.869hr.uk")
    render.build_site({"records": [{"title": "[SVR4U] VPS", "url": "https://lowendtalk.com/1"}], "count": 1}, tmp_path, verbose=False)
    assert audit(tmp_path)["affiliate_merchants"] == 14


def test_coupon_case_is_not_changed():
    assert "Summer2026" in extract_discount("promo code Summer2026")["codes"]
    assert extract_discount("promo code applies automatically")["codes"] == []
