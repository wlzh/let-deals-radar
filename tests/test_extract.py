"""抽取层测试：规格/价格/折扣/活动性质/库存判定。"""

from __future__ import annotations

from letdeals.extract import (
    age_status,
    availability,
    build_record,
    extract_cpu_models,
    extract_discount,
    extract_event,
    extract_locations,
    extract_prices,
    extract_provider,
    extract_specs,
    extract_vendor_url,
)


# --------------------------------------------------------------------------
# 规格
# --------------------------------------------------------------------------

def test_extract_specs_full():
    text = "8 vCores, 32 GB DDR5 RAM, 300 GB NVMe, 25 TB Bandwidth"
    s = extract_specs(text)
    assert s["vcpu"] == 8
    assert s["ram_gb"] == 32
    assert s["storage_gb"] == 300
    assert s["storage_type"] == "NVMe"
    assert s["bandwidth_tb"] == 25


def test_extract_specs_takes_max():
    """多套餐时应取最大值作为旗舰规格。"""
    text = "2 Cores 4 GB RAM 40 GB SSD\n4 Cores 8 GB RAM 80 GB SSD"
    s = extract_specs(text)
    assert s["vcpu"] == 4
    assert s["ram_gb"] == 8
    assert s["storage_gb"] == 80


def test_extract_specs_needs_ram_keyword():
    """裸容量（无 RAM 关键词）不应被 extract_specs 认作内存。

    这正是需要 parse_flat_tables 做位置推断的原因：
    LET 表格拍平后只剩「32 GB」这种裸值。
    """
    s = extract_specs("32 GB 300 GB 25 TB")
    assert s["ram_gb"] is None
    assert s["storage_gb"] is None


def test_extract_specs_missing_returns_none():
    s = extract_specs("just some prose without specs")
    assert s["vcpu"] is None
    assert s["ram_gb"] is None


def test_extract_cpu_models():
    models = extract_cpu_models("Powered by AMD EPYC 9275F and Ryzen 9 9950X")
    assert any("9275F" in m for m in models)
    assert any("9950X" in m for m in models)


def test_extract_cpu_models_no_false_positive():
    assert extract_cpu_models("cheap vps deals for everyone") == []


# --------------------------------------------------------------------------
# 价格
# --------------------------------------------------------------------------

def test_extract_prices_prefix():
    ps = extract_prices("$2.49/month")
    assert ps and ps[0]["amount"] == 2.49
    assert ps[0]["currency"] == "$"
    assert ps[0]["cycle"] == "month"


def test_extract_prices_suffix_eur():
    ps = extract_prices("€129/year")
    assert ps[0]["amount"] == 129
    assert ps[0]["cycle"] == "year"


def test_extract_prices_postfix_symbol():
    ps = extract_prices("7.49€/quarterly")
    assert ps[0]["amount"] == 7.49
    assert ps[0]["currency"] == "€"
    assert ps[0]["cycle"] == "quarter"


def test_extract_prices_sorted_by_annual():
    """应按时长折算排序，而非字面金额。"""
    ps = extract_prices("$10/month and $50/year")
    # 月付年化 120 > 年付 50，故年付应排在前面
    assert ps[0]["cycle"] == "year"


def test_extract_prices_empty():
    assert extract_prices("no prices here") == []


# --------------------------------------------------------------------------
# 折扣
# --------------------------------------------------------------------------

def test_extract_discount_percent():
    d = extract_discount("Get 60% off lifetime")
    assert d["percent_max"] == 60
    assert d["recurring"] is True


def test_extract_discount_code():
    d = extract_discount("Use coupon code LETB25 at checkout")
    assert "LETB25" in d["codes"]


def test_extract_discount_rejects_placeholder_codes():
    """CODE / DISCOUNT / REQUIRED 这类占位词不是真优惠码。"""
    d = extract_discount("Use coupon code CODE and promo code DISCOUNT")
    assert "CODE" not in d["codes"]
    assert "DISCOUNT" not in d["codes"]


def test_extract_discount_extras():
    d = extract_discount("double storage plus 2 months free")
    assert d["extras"]


def test_extract_discount_empty():
    d = extract_discount("nothing special here")
    assert d["strength"] == ""
    assert d["recurring"] is False


# --------------------------------------------------------------------------
# 活动性质
# --------------------------------------------------------------------------

def test_extract_event_anniversary():
    assert extract_event("BERO HOST 10th Anniversary Deals", "") == "anniversary"


def test_extract_event_black_friday():
    assert extract_event("Black Friday 2025 Deals", "") == "black_friday"


def test_extract_event_priority():
    """周年庆优先于泛化的 sale。"""
    assert extract_event("10th Anniversary Sale", "") == "anniversary"


def test_extract_event_regular():
    assert extract_event("Weekly VPS Thread", "hello") == "regular"


# --------------------------------------------------------------------------
# 地点
# --------------------------------------------------------------------------

def test_extract_locations():
    locs = extract_locations("Hosted in Frankfurt, Germany")
    assert "Frankfurt" in locs
    assert "Germany" in locs


def test_extract_locations_dedup():
    locs = extract_locations("Frankfurt and again Frankfurt")
    assert locs.count("Frankfurt") == 1


# --------------------------------------------------------------------------
# 厂商名
# --------------------------------------------------------------------------

def test_extract_provider_from_separator():
    p = extract_provider("HostBrr | BF2025 Deals | AMD VPS", "labze", "")
    assert p == "HostBrr"


def test_extract_provider_skips_descriptive_head():
    """描述性首段不应被当成厂商名，应回退到域名。"""
    p = extract_provider(
        "Free Ryzen 5900XT Server | From $99/mo", "someone",
        "visit https://budgetvm.com/ for details")
    assert p == "budgetvm.com"


def test_extract_provider_strips_subdomain():
    p = extract_provider("Deals", "x", "https://lg.fra.example.com/x")
    assert p == "example.com"


def test_extract_provider_leb_does_not_use_author():
    """LEB 的作者是编辑，绝不能当厂商名。"""
    p = extract_provider(
        "Some Random Headline About VPS", "raindog308",
        "no domains here", source="leb")
    assert p != "raindog308"


def test_extract_provider_leb_brand_from_text():
    p = extract_provider(
        "Velohost Returns With Even Cheaper Prices", "raindog308",
        "Welcome back, Velohost!", source="leb")
    assert "Velohost" in p


def test_extract_provider_let_prefers_domain_over_title_fragment():
    """LET 不应套用 LEB 的标题裁剪规则。

    「BERO HOST 10th Anniversary Deals」若被裁成
    「BERO HOST 10th Anniversary」会丢掉域名这一更精确的结果。
    """
    p = extract_provider(
        "BERO HOST 10th Anniversary Deals – EPYC Turin",
        "berohost",
        "Order: https://bero-host.de/kvm-rootserver",
        source="let")
    assert p == "bero-host.de"


def test_extract_provider_leb_trims_sentence_suffix():
    """LEB 标题里的句式后缀应被裁掉，留下品牌名。"""
    p = extract_provider(
        "Velohost Returns With Even Cheaper Prices! Get a 4GB VPS",
        "raindog308", "no links", source="leb")
    assert p == "Velohost"


# --------------------------------------------------------------------------
# 库存判定
# --------------------------------------------------------------------------

def test_availability_vendor_takes_priority():
    """商家表态优先于帖文关键词。"""
    a = availability(["sold out"], 1, "ON_SALE", "vendor")
    assert a == "ON_SALE"


def test_availability_vendor_restocking():
    a = availability([], 1, "RESTOCKING", "vendor")
    assert a == "RESTOCKING"


def test_availability_from_stock_hints():
    assert availability(["sold out"], 1) == "SOLD_OUT"
    assert availability(["back in stock"], 1) == "ON_SALE"


def test_availability_user_signal_is_weak():
    """用户说的售罄只能算疑似。"""
    a = availability([], 1, "SOLD_OUT", "user")
    assert a == "LIKELY_SOLD_OUT"


def test_availability_stale_fallback():
    assert availability([], 90) == "UNVERIFIED_STALE"
    assert availability([], 1) == "UNVERIFIED"


def test_age_status():
    assert age_status(1) == "NEW"
    assert age_status(15) == "ACTIVE"
    assert age_status(90) == "STALE"
    assert age_status(None) == "UNKNOWN"


# --------------------------------------------------------------------------
# 记录构建
# --------------------------------------------------------------------------

def test_build_record_shape():
    item = {
        "thread_id": "123", "source": "let",
        "title": "TestHost | 8 vCores 32 GB 300 GB NVMe",
        "link": "https://lowendtalk.com/discussion/123/x",
        "pubDate": "Wed, 01 Oct 2026 00:00:00 +0000",
        "author": "tester",
        "description": "<p>8 vCores, 32 GB RAM, 300 GB NVMe, 25 TB</p>",
    }
    r = build_record(item)
    for key in ("thread_id", "provider", "specs", "prices", "discount",
                "event_type", "availability", "confidence", "source"):
        assert key in r, f"缺少字段 {key}"
    assert r["thread_id"] == "123"
    assert r["source"] == "let"
    assert 0.0 <= r["confidence"] <= 1.0


def test_build_record_table_overrides_specs(bero_plan_lines):
    """表格解析成功时应采用表格里的旗舰套餐。"""
    item = {
        "thread_id": "221713", "source": "let",
        "title": "BERO HOST 10th Anniversary Deals",
        "link": "https://lowendtalk.com/discussion/221713/x",
        "pubDate": "Fri, 03 Oct 2026 12:00:00 +0000",
        "author": "berohost",
        "description": "<pre>" + bero_plan_lines + "</pre>",
    }
    r = build_record(item)
    assert r["specs"]["vcpu"] == 8
    assert r["specs"]["ram_gb"] == 32
    assert r["specs"]["storage_gb"] == 300
    assert r["flagship_price"]["amount"] == 129
    assert len(r["plans"]) >= 5


# --------------------------------------------------------------------------
# 厂商官网推断 extract_vendor_url
# --------------------------------------------------------------------------

def test_vendor_url_when_provider_is_domain():
    assert extract_vendor_url("bero-host.de", "") == "https://bero-host.de"
    assert extract_vendor_url("Royal-Server.Ro", "") == "https://royal-server.ro"
    # 子域应归并到可注册域
    assert extract_vendor_url("www.linveo.com", "") == "https://linveo.com"


def test_vendor_url_matches_provider_name_in_body():
    """正文里与厂商名匹配的链接优先，且尽量取具体活动页。

    行为变更（2026-10）：以前只返回域名首页，现在优先返回
    活动/购买页 —— 实测原帖正文常直接给出 warehouse-deals 这类路径。
    """
    body = '<a href="https://dartnode.com/warehouse-deals">deals</a>'
    assert extract_vendor_url("DartNode", body) == \
        "https://dartnode.com/warehouse-deals"


def test_vendor_url_picks_matching_link_not_first():
    """正文里第一个域名未必是厂商的 —— 必须挑与厂商名匹配的那个。

    DartNode 的帖子里先出现 twitch 链接，再才是官网。
    """
    body = ('<a href="https://twitch.tv/DartNodeTV">直播</a>'
            '<a href="https://snaju.com">partner</a>'
            '<a href="https://dartnode.com">官网</a>')
    assert extract_vendor_url("DartNode", body) == "https://dartnode.com"


def test_vendor_url_skips_benchmark_and_utility_sites():
    """跑分站/工具站出现在正文里，不能当成厂商官网。

    实测 Geekbench 成绩链接曾被误判为厂商官网。
    """
    body = '<a href="https://browser.geekbench.com/v6/cpu/123">成绩</a>'
    assert extract_vendor_url("D4 Networks", body) is None
    body2 = '<a href="https://www.speedtest.net/result/1">测速</a>'
    assert extract_vendor_url("SomeHost", body2) is None


def test_vendor_url_returns_none_when_unknown():
    """无从判断时返回 None —— 宁可不给链接，也不要错链。"""
    assert extract_vendor_url("NUXOA", "") is None
    assert extract_vendor_url("KVM VPS", "no links here") is None
    # 名称过短不足以可靠匹配
    assert extract_vendor_url("ab", '<a href="https://ab.com">x</a>') is None


def test_vendor_url_handles_multipart_suffix():
    """something.co.uk 不能被切成 co.uk（co.uk 本身不是域名）。"""
    body = '<a href="https://host.co.uk">uk host</a>'
    out = extract_vendor_url("Host", body)
    assert out is None or out != "https://co.uk"
    body2 = '<a href="https://myhost.co.uk">x</a>'
    assert extract_vendor_url("MyHost", body2) == "https://myhost.co.uk"


def test_vendor_url_rejects_technical_subdomains():
    """lg.(looking glass) 之类技术主机名不是厂商官网。"""
    assert extract_vendor_url("lg.svr", "") is None
    assert extract_vendor_url("lg.ph", "") is None


def test_vendor_url_accepts_long_tld():
    """新顶级域可长于 6 字符，如 .hosting。"""
    assert extract_vendor_url("Ava.hosting", "") == "https://ava.hosting"


def test_build_record_includes_vendor_url():
    """build_record 必须带上 vendor_url，且取自原始 HTML。

    strip_html 会把 <a href> 剥掉，若误用清洗后的文本，
    厂商链接会全部丢失。
    """
    item = {
        "thread_id": "1", "source": "let",
        "title": "DartNode Black Friday Deals",
        "link": "https://lowendtalk.com/discussion/1/x",
        "pubDate": "Fri, 03 Oct 2026 12:00:00 +0000",
        "author": "dartnode",
        "description": '<p>Deals!</p><a href="https://dartnode.com">官网</a>',
    }
    r = build_record(item)
    # DartNode 已配置参数型推广（aff=LazyMonkey767）：vendor_url 取自原始
    # HTML 后按 affiliate 配置改写为带推广参数的链接（设计如此）。
    assert r["vendor_url"] == "https://dartnode.com?aff=LazyMonkey767"


def test_vendor_url_prefers_offer_page_over_homepage():
    """应优先取活动/购买页，而不是厂商首页。

    实测：原帖正文常直接给活动页，如 linceris.com/deals。
    只给首页会让用户还要自己找活动。
    """
    body = ('<a href="https://linceris.com">官网</a>'
            '<a href="https://linceris.com/deals">Deals</a>')
    out = extract_vendor_url("Linceris.com", body)
    assert out == "https://linceris.com/deals"


def test_vendor_url_picks_store_or_cart_page():
    """商店/购物车页也是有效购买入口。"""
    body = '<a href="https://deluxhost.net/en/cart?add=storage-1&cycle=monthly">买</a>'
    out = extract_vendor_url("DeluxHost.net", body)
    assert "cart" in out


def test_vendor_url_skips_docs_and_terms():
    """文档页、服务条款不是购买入口。

    实测踩到：ravnix.gg/terms、servury.com/docs/...
    """
    body = ('<a href="https://ravnix.gg/terms">ToS</a>'
            '<a href="https://ravnix.gg/vps">VPS</a>')
    out = extract_vendor_url("ravnix.gg", body)
    assert "/terms" not in out
    assert "/vps" in out


def test_vendor_url_skips_speedtest_and_binary():
    """测速子域与二进制文件不是购买入口。

    实测踩到：mtl-lg.servury.com/100MB.bin
    """
    body = ('<a href="https://mtl-lg.servury.com/100MB.bin">测速</a>'
            '<a href="https://servury.com/pricing">价格</a>')
    out = extract_vendor_url("servury.com", body)
    assert ".bin" not in out
    assert "pricing" in out


def test_vendor_url_falls_back_to_homepage():
    """只有首页链接时，退回首页（好过没有）。"""
    body = '<a href="https://example.com">官网</a>'
    out = extract_vendor_url("example.com", body)
    assert out == "https://example.com"


def test_deep_link_filter_rules():
    """_is_usable_deep_link 的边界。"""
    from letdeals.extract import _is_usable_deep_link as ok
    assert ok("https://x.com/deals")
    assert ok("https://x.com/blackfriday/")
    assert not ok("https://x.com/terms")
    assert not ok("https://x.com/docs/a/b")
    assert not ok("https://x.com/file.bin")
    assert not ok("https://lg-x.com/path")
    assert not ok("https://status.x.com/path")


def test_vendor_url_skips_compliance_pages():
    """合规页不是购买入口。

    实测踩到：ravnix.gg/aup（可接受使用政策）。
    """
    body = ('<a href="https://ravnix.gg/aup">AUP</a>'
            '<a href="https://ravnix.gg/kvm-vps">KVM</a>')
    out = extract_vendor_url("ravnix.gg", body)
    assert "/aup" not in out
    assert "kvm" in out


def test_vendor_url_keeps_my_subdomain_purchase_page():
    """my./billing./portal. 这类子域常是购买页，不能误伤。"""
    body = '<a href="https://my.example.com/store/vps">买</a>'
    out = extract_vendor_url("example.com", body)
    assert "my.example.com" in out


def test_build_record_vendor_url_none_when_unknown():
    item = {
        "thread_id": "2", "source": "let",
        "title": "Some Random Title",
        "link": "https://lowendtalk.com/discussion/2/x",
        "pubDate": "Fri, 03 Oct 2026 12:00:00 +0000",
        "author": "someone",
        "description": "<p>no links at all</p>",
    }
    r = build_record(item)
    assert r["vendor_url"] is None
