"""渲染层测试：评分、格式化、静态站产物与域名配置。"""

from __future__ import annotations

import json
import os
import re

import pytest

from letdeals import config, render


def _sample_payload():
    return {
        "source": "test", "count": 2, "generated_at": "2026-10-15T00:00:00Z",
        "records": [
            {
                "thread_id": "1", "source": "let", "provider": "BERO HOST",
                "title": "10th Anniversary", "url": "https://example.com/1",
                "author": "berohost", "posted": "", "age_days": 3,
                "age_status": "NEW", "availability": "ENDED",
                "event_type": "anniversary",
                "discount": {"strength": "循环/永久", "percent_max": 10,
                             "recurring": True, "codes": ["LET10"],
                             "extras": []},
                "specs": {"vcpu": 8, "ram_gb": 32, "storage_gb": 300,
                          "storage_type": "unspecified", "bandwidth_tb": 25},
                "specs_all": {}, "plans": [], "cpu_models": ["EPYC 9275F"],
                "flagship_price": {"amount": 129, "currency": "€",
                                   "cycle": "year"},
                "prices": [], "locations": ["Frankfurt"],
                "stock_hints": [], "replies": None, "confidence": 1.0,
            },
            {
                "thread_id": "leb-x", "source": "leb", "provider": "godlike.host",
                "title": "Cheap VPS", "url": "https://example.com/2",
                "author": "editor", "posted": "", "age_days": 40,
                "age_status": "STALE", "availability": "UNVERIFIED",
                "event_type": "sale",
                "discount": {"strength": "", "percent_max": None,
                             "recurring": False, "codes": [], "extras": []},
                "specs": {"vcpu": 1, "ram_gb": 2, "storage_gb": 30,
                          "storage_type": "NVMe", "bandwidth_tb": None},
                "specs_all": {}, "plans": [],
                "cpu_models": ["EPYC 75F3"],
                "flagship_price": {"amount": 21.99, "currency": "$",
                                   "cycle": "year"},
                "prices": [], "locations": [], "stock_hints": [],
                "replies": None, "confidence": 0.83,
            },
        ],
    }


# --------------------------------------------------------------------------
# 评分
# --------------------------------------------------------------------------

def test_deal_score_recurring_is_valuable():
    rec = _sample_payload()["records"][0]
    assert render.deal_score(rec) > 60


def test_deal_score_plain_is_low():
    rec = _sample_payload()["records"][1]
    assert render.deal_score(rec) < 40


def test_deal_score_anniversary_bonus():
    base = {"discount": {}, "confidence": 0.5}
    a = dict(base, event_type="regular")
    b = dict(base, event_type="anniversary")
    assert render.deal_score(b) > render.deal_score(a)


def test_deal_score_empty_record():
    assert render.deal_score({}) >= 0


# --------------------------------------------------------------------------
# 格式化
# --------------------------------------------------------------------------

def test_price_text():
    rec = _sample_payload()["records"][0]
    assert render.price_text(rec) == "€129/年"


def test_price_text_missing():
    assert render.price_text({}) == "—"


def test_spec_text_hides_unspecified_storage():
    rec = _sample_payload()["records"][0]
    out = render.spec_text(rec)
    assert "8C" in out and "32G" in out
    assert "unspecified" not in out


def test_spec_text_shows_typed_storage():
    rec = _sample_payload()["records"][1]
    assert "NVMe" in render.spec_text(rec)


def test_spec_text_cpu_only_has_no_dangling_separator():
    """只有 CPU、没有规格时，不能输出「— | Xeon」这种残缺拼接。"""
    out = render.spec_text({"specs": {}, "cpu_models": ["Xeon E5-2699"]})
    assert out == "Xeon E5-2699"
    assert "—" not in out and "|" not in out


def test_spec_text_specs_only_has_no_dangling_separator():
    """只有规格、没有 CPU 时同理。"""
    out = render.spec_text(
        {"specs": {"vcpu": 4, "ram_gb": 8}, "cpu_models": []})
    assert out == "4C/8G"
    assert "|" not in out


def test_spec_text_both_missing_uses_placeholder():
    assert render.spec_text({"specs": {}, "cpu_models": []}) == "—"


def test_spec_text_both_present_joined():
    out = render.spec_text(
        {"specs": {"vcpu": 8, "ram_gb": 32}, "cpu_models": ["Ryzen 9 9950X"]})
    assert out == "8C/32G | Ryzen 9 9950X"


def test_annual_eur_conversion():
    rec = _sample_payload()["records"][1]     # $21.99/year
    v = render.annual_eur(rec)
    assert v == pytest.approx(21.99 * 0.92, rel=0.01)


def test_annual_eur_monthly_multiplies():
    rec = {"flagship_price": {"amount": 10, "currency": "€", "cycle": "month"}}
    assert render.annual_eur(rec) == 120.0


def test_annual_eur_missing():
    assert render.annual_eur({}) is None


def test_to_markdown_has_header_and_rows():
    md = render.to_markdown(_sample_payload()["records"])
    lines = md.strip().split("\n")
    assert lines[0].startswith("|")
    assert len(lines) == 4                    # 表头 + 分隔 + 2 行
    assert "BERO HOST" in md


# --------------------------------------------------------------------------
# 静态站
# --------------------------------------------------------------------------

def test_affiliate_shortcuts_use_selected_merchants_in_order(monkeypatch):
    monkeypatch.delenv("LDR_AFFILIATES", raising=False)
    output = render._affiliate_shortcuts_html()
    expected = [
        ("丽萨主机", "https://lisahost.com/aff.php?aff=9424"),
        ("DMIT 套餐", "https://www.dmit.io/aff.php?aff=21728"),
        ("VIRCS 落地机", "https://www.vircs.com/welcome?vcd=61a4aae4"),
    ]
    assert output.count('<a class="purchase"') == 3
    positions = []
    for name, url in expected:
        assert f'href="{url}"' in output
        positions.append(output.index(f'>{name}</a>'))
    assert positions == sorted(positions)
    assert "RackNerd" not in output
    assert "DeluxHost" not in output


def test_header_update_time_is_beijing_time(tmp_path):
    payload = _sample_payload()
    payload["generated_at"] = "2026-10-29T21:00:00Z"
    render.build_site(payload, str(tmp_path), verbose=False)
    output = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "数据更新 <b>2026-10-30 05:00:00 北京时间</b>" in output
    assert "每日北京时间 05:00 自动检查" in output
    assert payload["generated_at"] == "2026-10-29T21:00:00Z"


def test_build_site_creates_files(tmp_path):
    out = render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    for name in ("index.html", "robots.txt", ".nojekyll", "sitemap.xml",
                 "data/deals.json"):
        assert os.path.exists(os.path.join(out, name)), f"缺少 {name}"


def test_build_site_embeds_data(tmp_path):
    render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()
    m = re.search(
        r'<script type="application/json" id="deals-data">(.*?)</script>',
        html, re.S)
    assert m, "未找到内嵌数据块"
    data = json.loads(m.group(1))
    assert len(data["records"]) == 2


def test_build_site_adds_score(tmp_path):
    payload = _sample_payload()
    render.build_site(payload, str(tmp_path), verbose=False)
    assert all("score" in r for r in payload["records"])


def test_build_site_no_placeholder_left(tmp_path):
    render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()
    assert not re.search(r"@@[A-Z_]+@@", html), "存在未替换的占位符"


def test_build_site_escapes_script_close(tmp_path):
    """记录里若含 </script> 不能逃逸出数据块。"""
    payload = _sample_payload()
    payload["records"][0]["title"] = "evil </script><script>alert(1)</script>"
    render.build_site(payload, str(tmp_path), verbose=False)
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()
    m = re.search(
        r'<script type="application/json" id="deals-data">(.*?)</script>',
        html, re.S)
    assert m and "evil" in json.loads(m.group(1))["records"][0]["title"]


def test_site_url_in_sitemap(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://deals.example.com")
    render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    sitemap = open(os.path.join(tmp_path, "sitemap.xml"),
                   encoding="utf-8").read()
    assert "https://deals.example.com/" in sitemap
    robots = open(os.path.join(tmp_path, "robots.txt"), encoding="utf-8").read()
    assert "deals.example.com/sitemap.xml" in robots


def test_site_url_absent_uses_relative(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "")
    render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    sitemap = open(os.path.join(tmp_path, "sitemap.xml"),
                   encoding="utf-8").read()
    assert "<loc>/</loc>" in sitemap


def test_custom_title_injected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_TITLE", "我的 VPS 雷达")
    render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()
    assert "我的 VPS 雷达" in html


def test_source_info_available_without_badge(tmp_path):
    """厂商名前的来源徽章已移除，但来源信息必须仍可获取。

    来源不能丢：顶部有来源筛选下拉，展开明细里也单独列出「来源」。
    """
    render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()
    # 数据里必须带 source
    m = re.search(
        r'<script type="application/json" id="deals-data">(.*?)</script>',
        html, re.S)
    data = json.loads(m.group(1))
    assert {r["source"] for r in data["records"]} == {"let", "leb"}
    # 来源筛选下拉仍在
    assert 'id="src"' in html
    # 展开明细里仍展示来源
    assert "来源 <b>" in html, "展开明细缺少来源字段"
    # 徽章已按要求移除
    assert 'class="src"' not in html, "来源徽章应已移除"


def test_forum_url_injected_when_posted(tmp_path):
    """已发布到论坛的记录应带上 forum_url，供渲染主链接。"""
    payload = _sample_payload()
    tid = str(payload["records"][0]["thread_id"])
    log = tmp_path / "posted.json"
    log.write_text(json.dumps({
        tid: {"discussion_id": "123", "url": "https://forum.test/d/123",
              "title": "x", "posted_at": "2026-01-01T00:00:00Z"}
    }), encoding="utf-8")

    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(render.config, "FORUM_POSTED_LOG", log)
    try:
        render.enrich(payload["records"])
    finally:
        monkeypatch.undo()

    assert payload["records"][0]["forum_url"] is None
    # 未发布的记录应保持 None，渲染时回退到原帖
    assert payload["records"][1]["forum_url"] is None


def test_forum_links_missing_file_is_safe():
    """记录文件不存在时返回空 dict，不能抛异常。"""
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(render.config, "FORUM_POSTED_LOG", "/nonexistent/x.json")
    try:
        assert render._load_forum_links() == {}
    finally:
        mp.undo()


def test_forum_links_corrupt_file_is_safe(tmp_path):
    """记录文件损坏时返回空 dict，全部回退原帖而不是死链。"""
    import pytest as _pytest
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    mp = _pytest.MonkeyPatch()
    mp.setattr(render.config, "FORUM_POSTED_LOG", bad)
    try:
        assert render._load_forum_links() == {}
    finally:
        mp.undo()


def test_header_links_rendered(tmp_path):
    """页头应渲染配置的附加链接（GitHub / Telegram）。"""
    render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()

    links = render.config.HEADER_LINKS
    assert links, "默认应配置页头链接"
    for name, url in links:
        assert name in html, "缺少链接名：%s" % name
        assert url in html, "缺少链接地址：%s" % url
    assert 'rel="noopener"' in html


def test_header_telegram_channel_precedes_blog():
    links = render.config.HEADER_LINKS
    assert links[0] == ("📢 TG频道", "https://t.me/tgmShareAI")
    assert links[1] == ("📖 博客", "https://869hr.uk/")
    output = render._header_links_html()
    assert output.index("TG频道") < output.index("博客")


def test_header_links_empty_is_safe(tmp_path):
    """未配置页头链接时不报错、不留占位符。"""
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(render.config, "HEADER_LINKS", [])
    try:
        render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    finally:
        mp.undo()
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()
    assert "@@HEADER_LINKS@@" not in html


def test_parse_header_links():
    """解析 "名称|地址,..."，非法项跳过。"""
    from letdeals.config import _parse_header_links
    got = _parse_header_links(
        "GitHub|https://github.com/x, Bad|not-a-url, ,"
        " TG|https://t.me/y")
    assert got == [("GitHub", "https://github.com/x"),
                   ("TG", "https://t.me/y")]


def test_parse_header_links_empty():
    from letdeals.config import _parse_header_links
    assert _parse_header_links("") == []
    assert _parse_header_links("没有分隔符") == []


def test_link_rendering_both_cases(tmp_path):
    """链接区两种形态：有论坛帖=主+次两个；无=只有原帖。"""
    render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()
    # 主链接（论坛）与次链接（原帖）的样式必须存在
    assert ".lk-main" in html and ".lk-sub" in html
    assert "中文详情" not in html
    assert "查看原帖" in html
    # 渲染时按 forum_url 分支
    assert "if(r.vendor_url)" in html


def test_icons_declared_and_copied(tmp_path):
    """站点图标：HTML 声明 + 文件复制 + manifest 生成。"""
    render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()

    assert 'rel="icon"' in html, "缺少 favicon 声明"
    assert "apple-touch-icon" in html, "缺少 iOS 主屏图标声明"
    assert 'rel="manifest"' in html, "缺少 webmanifest 声明"

    # 图标文件应随站点发布
    for name in ("favicon.ico", "manifest.webmanifest"):
        assert os.path.exists(os.path.join(tmp_path, name)), f"缺少 {name}"

    # manifest 内容应可解析且含图标
    m = json.load(open(os.path.join(tmp_path, "manifest.webmanifest"),
                       encoding="utf-8"))
    assert m["name"]
    assert isinstance(m["icons"], list)


def test_missing_assets_does_not_break_build(tmp_path, monkeypatch):
    """图标缺失时只告警，不应导致建站失败。"""
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(render.config, "ASSETS_DIR", "/nonexistent-assets")
    try:
        out = render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    finally:
        mp.undo()
    assert os.path.exists(os.path.join(out, "index.html"))
    # manifest 仍应生成（只是没有图标条目）
    assert os.path.exists(os.path.join(out, "manifest.webmanifest"))


def test_vendor_signal_status_is_translated(tmp_path):
    """回帖证据里的状态码要翻成中文。

    之前直接输出 ON_SALE 这类内部码；但同一行后面的日期与引文
    必须保留原文 —— 那是商家回帖的原始证据，翻译会失去证据效力。
    """
    payload = _sample_payload()
    payload["records"][0]["replies"] = {
        "reply_count": 10, "vendor_reply_count": 2,
        "status": "ON_SALE", "source": "vendor",
        "evidence": {"author": "vendor1", "is_vendor": True,
                     "datetime": "2026-10-13T00:00:00+00:00",
                     "snippet": "We currently have stock available!"},
        "vendor_signals": [
            {"status": "ON_SALE", "datetime": "2026-10-13T00:00:00+00:00",
             "snippet": "We currently have stock available for both!"},
            {"status": "RESTOCKING", "datetime": "2026-10-14T00:00:00+00:00",
             "snippet": "Working on a restock now"},
        ],
    }
    render.build_site(payload, str(tmp_path), verbose=False)
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()
    # 走 AVAIL_CN 映射，而不是直出内部码
    assert "AVAIL_CN[s.status]||s.status" in html, "状态码未经过翻译映射"
    # 引文原文必须保留（英文原样）
    assert "We currently have stock available for both!" in html


def test_age_and_confidence_columns_do_not_wrap(tmp_path):
    """「发布于」与「置信度」列必须禁止折行。

    - 「20 天前」会在数字与中文之间的空格处断开，变成两行；
    - 置信度是「进度条 + 百分比」，列变窄时会被拆开。

    这两处依赖 CSS 的 white-space:nowrap，容易在调样式时被误删，
    因此在此固化。
    """
    render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()
    assert ".hidesm,td.cf{white-space:nowrap}" in html, "缺少禁止折行规则"
    # 置信度单元格需带 cf 类，否则上面的选择器匹配不到
    assert 'class="cf"' in html, "置信度单元格缺少 cf 类"
    # 「发布于」列仍须在小屏下隐藏
    assert ".hidesm{display:none}" in html


def test_deal_title_line_removed(tmp_path):
    """厂商名下方不再显示原帖标题（改为最紧凑布局）。

    标题行曾用于补足上下文，但长标题会把行高撑得不整齐，
    已移除；样式与 DOM 都不应再出现 .tt。
    """
    render.build_site(_sample_payload(), str(tmp_path), verbose=False)
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()
    assert ".tt{" not in html, "标题行样式应已移除"
    assert 'class="tt"' not in html, "标题行 DOM 应已移除"


def test_provider_link_rendered_when_vendor_url_present(tmp_path):
    """有 vendor_url 时厂商名渲染为外链；没有则保持纯文本。"""
    payload = _sample_payload()
    payload["records"][0]["vendor_url"] = "https://example-vendor.com"
    payload["records"][1]["vendor_url"] = None
    render.build_site(payload, str(tmp_path), verbose=False)
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()

    assert 'class="pv-link"' in html, "缺少厂商官网链接样式"
    assert "example-vendor.com" in html, "链接地址未被渲染"
    assert "noopener nofollow" in html, "外链缺少 rel 保护"
    assert 'target="_blank"' in html, "外链应新窗口打开"
    # 点链接时不能同时触发行展开
    assert "if(e.target.closest('a')) return;" in html, \
        "点击链接会误触发行展开"


def test_vendor_url_is_escaped(tmp_path):
    """vendor_url 来自抓取内容，必须转义，避免注入。"""
    payload = _sample_payload()
    payload["records"][0]["vendor_url"] = 'https://x.test/"><script>bad()</script>'
    render.build_site(payload, str(tmp_path), verbose=False)
    html = open(os.path.join(tmp_path, "index.html"), encoding="utf-8").read()
    assert "<script>bad()</script>" not in html, "存在未转义的注入"
