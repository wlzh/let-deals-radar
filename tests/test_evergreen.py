"""常青页测试：筛选口径、SSR 内容、接线（sitemap 与首页内链）。"""

from __future__ import annotations

from letdeals import config, evergreen, render


def _rec(provider, annual=None, availability="ON_SALE", locations=None,
         url=None):
    rec = {
        "provider": provider, "url": url or f"https://example.com/{provider}",
        "title": provider, "availability": availability,
        "event_type": "sale", "discount": {"strength": ""},
        "display_spec": "1核 / 1GB", "display_price": "$10/年",
        "display_price_note": "", "locations": locations or [],
    }
    if annual is not None:
        rec["annual_eur"] = annual
    return rec


def _payload(records):
    return {"source": "test", "count": len(records),
            "generated_at": "2026-11-01T00:00:00Z", "records": records}


def test_cheap_excludes_closed_and_over_30():
    recs = [
        _rec("cheap", annual=12),
        _rec("mid", annual=25),
        _rec("expensive", annual=40),
        _rec("sold", annual=9, availability="SOLD_OUT"),
        _rec("no-price"),
    ]
    cheap = evergreen.cheap_records(recs)
    assert [r["provider"] for _, r in cheap] == ["cheap", "mid"]


def test_region_groups_dedup_and_thin_sections_not_split():
    recs = [
        _rec("us", annual=20, locations=["Los Angeles", "Dallas"]),
        _rec("hk", annual=20, locations=["Hong Kong"]),
        _rec("sold-hk", annual=20, availability="SOLD_OUT",
             locations=["Hong Kong"]),
    ]
    groups = dict((name, rows) for name, _, rows in
                  evergreen.region_records(recs))
    assert [r["provider"] for r in groups["美国"]] == ["us"]
    assert [r["provider"] for r in groups["香港"]] == ["hk"]
    # 香港不单独成页：它是按地区合集页里的一节，不是独立 URL。
    assert evergreen.REGION_PATH == "by-region"


def test_render_pages_carry_titles_canonical_and_faq(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://deals.example.com")
    monkeypatch.setattr(config, "SITE_TITLE", "大坝的VPS优惠大超市")
    payload = _payload([
        _rec("cheap", annual=12, locations=["Frankfurt"]),
        _rec("hk", annual=20, locations=["Hong Kong"]),
    ])
    cheap = evergreen.render_cheap_page(payload)
    assert "低价年付 VPS 汇总" in cheap
    assert 'href="https://deals.example.com/cheap-yearly/"' in cheap
    assert "年化 15 欧元以内" in cheap
    assert "待确认" in cheap or "ON_SALE" in cheap
    region = evergreen.render_region_page(payload)
    assert "按地区选 VPS" in region
    assert "香港" in region
    assert "黑五 2026 专题" in region


def test_build_site_writes_evergreen_and_sitemap(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://deals.example.com")
    payload = _payload([_rec("cheap", annual=12, locations=["Dallas"])])
    render.build_site(payload, str(tmp_path), verbose=False)
    for path in ("cheap-yearly", "by-region"):
        assert (tmp_path / path / "index.html").exists()
    sitemap = (tmp_path / "sitemap.xml").read_text(encoding="utf-8")
    assert "cheap-yearly/" in sitemap
    assert "by-region/" in sitemap
    assert "black-friday-2026/" in sitemap
    home = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'href="cheap-yearly/"' in home
    assert 'href="by-region/"' in home
    assert 'href="black-friday-2026/"' in home
