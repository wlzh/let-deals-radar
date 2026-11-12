import pytest

from letdeals import affiliate, bfpage, presentation, render
from tests.test_details import record


@pytest.mark.parametrize("domain", list(affiliate.DEFAULTS))
def test_legacy_checkout_cannot_bypass_owner_affiliate(domain):
    raw = f"https://{domain}/cart.php?pid=12&aff=foreign"
    rec = presentation.prepare(record(vendor_url=raw, order_url=raw, availability="ON_SALE"))
    assert rec["vendor_url"] == affiliate.rewrite(raw)
    assert rec["order_url"] is None
    assert affiliate.rewrite(rec["vendor_url"]) == rec["vendor_url"]


def test_missing_vendor_has_no_external_listing_cta(tmp_path):
    rec = record(vendor_url=None, order_url="https://elsewhere.test/cart")
    render.build_site({"records": [rec], "count": 1}, tmp_path, verbose=False)
    initial = render._initial_rows([rec])
    assert rec["url"] not in initial
    assert rec["detail_path"] in initial
    assert rec["url"] not in bfpage._purchase_html(rec)
    page = (tmp_path / "index.html").read_text()
    assert "查看原帖找购买入口" not in page
    assert "esc(r.order_url)" not in page
    detail = (tmp_path / rec["detail_path"].strip("/") / "index.html").read_text()
    assert 'class="purchase"' not in detail
    assert '<details><summary>信息来源与条款核验</summary>' in detail
    assert rec["url"] in detail
