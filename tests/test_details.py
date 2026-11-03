import json
import re
import xml.etree.ElementTree as ET

from letdeals import config, details, feed, presentation, render


def record(**over):
    r = {"url": "https://lowendtalk.com/discussion/1", "thread_id": "1",
         "source": "let", "provider": "test", "title": "VPS $12/year",
         "vendor_url": "https://racknerd.com/", "availability": "UNVERIFIED",
         "specs": {}, "specs_all": {}, "discount": {}, "locations": []}
    r.update(over)
    return r


def test_detail_identity_stable_and_safe():
    r = record()
    assert details.detail_path(r) == details.detail_path(record(title="new title"))
    assert re.fullmatch(r"/deals/[a-f0-9]{24}/", details.detail_path(r))
    assert details.detail_path(record(url="javascript:alert(1)")) == ""


def test_build_details_preserves_aff_and_does_not_invent_product(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://vps.test")
    r = record(title='VPS </script><script>alert(1)</script>')
    payload = {"records": [r], "count": 1, "generated_at": "2026-11-02T21:02:07Z"}
    render.build_site(payload, tmp_path, verbose=False)
    page = (tmp_path / r["detail_path"].strip("/") / "index.html").read_text()
    assert '<script>alert(1)</script>' not in page
    assert '"@type": "Product"' not in page
    assert r["vendor_url"].replace("&", "&amp;") in page
    assert 'nofollow sponsored' in page
    assert '2026-11-03 05:02:07' in page
    assert r["detail_path"] in (tmp_path / "sitemap.xml").read_text()
    assert r["detail_path"] in (tmp_path / "index.html").read_text()
    item = ET.fromstring(feed.rss_feed(payload)).find("channel/item")
    assert item.findtext("link") == "https://vps.test" + r["detail_path"]
    assert item.findtext("guid") == r["url"]
    assert json.loads((tmp_path / "data/deals.json").read_text())["records"][0]["detail_path"] == r["detail_path"]


def test_service_and_placeholder_provider():
    r = presentation.prepare(record(title="HARDWARE SALE Supermicro", provider="BLACK FRIDAY", vendor_url=None))
    assert r["service_kind"] == "hardware"
    assert r["provider"] == "商家待核实"
    assert r["vendor_url"] is None
    r = presentation.prepare(record(title="Mango Mail email hosting"))
    assert r["service_kind"] == "email"


def test_removed_records_prune_only_generated_pages(tmp_path):
    r = presentation.prepare(record())
    details.write_details({"records": [r]}, tmp_path, "")
    owned = tmp_path / details.detail_path(r).strip("/") / "index.html"
    unrelated = tmp_path / "deals/manual/index.html"
    unrelated.parent.mkdir()
    unrelated.write_text("manual")
    details.write_details({"records": []}, tmp_path, "")
    assert not owned.exists()
    assert unrelated.read_text() == "manual"


def test_untrusted_detail_path_is_not_used(tmp_path):
    r = record(url="javascript:alert(1)", detail_path="../../secret")
    assert details.write_details({"records": [r]}, tmp_path, "") == []
    assert "detail_path" not in r
