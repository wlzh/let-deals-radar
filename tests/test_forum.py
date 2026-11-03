"""论坛发帖模块测试。

重点覆盖「安全」相关的行为：总开关、演练、幂等、限流、筛选。
这些一旦失效会导致重复刷屏，因此必须固化为测试。
"""

from __future__ import annotations

import json
import os

import pytest

from letdeals import config, forum


# --------------------------------------------------------------------------
# 样本
# --------------------------------------------------------------------------

def _rec(**over):
    r = {
        "thread_id": "1001",
        "source": "let",
        "provider": "example.com",
        "title": "Example Host Black Friday Deals",
        "url": "https://lowendtalk.com/discussion/1001/x",
        "vendor_url": "https://example.com",
        "event_type": "black_friday",
        "age_days": 1,
        "availability": "ON_SALE",
        "specs": {"vcpu": 4, "ram_gb": 8.0, "storage_gb": 80.0,
                  "storage_type": "NVMe", "bandwidth_tb": 5.0},
        "flagship_price": {"amount": 29.0, "currency": "$", "cycle": "year"},
        "prices": [{"amount": 29.0, "currency": "$", "cycle": "year"}],
        "discount": {"strength": "50% off", "percent_max": 50,
                     "recurring": True, "codes": ["BF50"], "extras": []},
        "cpu_models": ["Ryzen 9 7950X"],
        "locations": ["Frankfurt"],
        "plans": [],
    }
    r.update(over)
    return r


@pytest.fixture
def log_path(tmp_path):
    return str(tmp_path / "posted.json")


@pytest.fixture
def fake_client():
    """记录调用并返回递增的帖子 id。"""
    calls = []

    def create(title, content):
        calls.append({"title": title, "content": content})
        n = len(calls)
        return {"id": 9000 + n, "slug": str(9000 + n), "approved": True,
                "url": "https://forum.test/d/%d" % (9000 + n)}

    create.calls = calls
    return create


@pytest.fixture(autouse=True)
def _restore_config(monkeypatch):
    """每个用例后恢复配置，避免互相污染。"""
    monkeypatch.setattr(forum, "_request", lambda *args, **kwargs: {"data": {"id": "test"}})
    monkeypatch.setattr(forum.time, "sleep", lambda _: None)
    saved = {k: getattr(config, k) for k in (
        "FORUM_ENABLED", "FORUM_DRY_RUN", "FORUM_API_KEY",
        "FORUM_BASE_URL", "FORUM_TAG_ID", "FORUM_MAX_PER_RUN",
        "FORUM_MAX_AGE_DAYS", "FORUM_MIN_PERCENT",
    )}
    yield
    for k, v in saved.items():
        setattr(config, k, v)


def _enable(**over):
    config.FORUM_ENABLED = True
    config.FORUM_API_KEY = "test-key"
    config.FORUM_BASE_URL = "https://forum.test"
    config.FORUM_TAG_ID = "21"
    config.FORUM_DRY_RUN = False
    for k, v in over.items():
        setattr(config, k, v)


# --------------------------------------------------------------------------
# 总开关
# --------------------------------------------------------------------------

def test_disabled_does_nothing(log_path, fake_client):
    """总开关关闭时不应调用任何 API。"""
    config.FORUM_ENABLED = False
    st = forum.post_deals([_rec()], posted_path=log_path,
                          verbose=False, client=fake_client)
    assert st["posted"] == 0
    assert st["enabled"] is False
    assert fake_client.calls == []


def test_enabled_without_key_skips_real_post(log_path, fake_client):
    """实际发布模式下缺 Key 应中止，且不调用 API。

    注意：即使注入了 client（测试用），凭据检查也必须生效 ——
    这是安全底线，不能因为便于测试而绕过。
    """
    config.FORUM_ENABLED = True
    config.FORUM_API_KEY = ""
    config.FORUM_DRY_RUN = False
    st = forum.post_deals([_rec()], posted_path=log_path,
                          verbose=False, client=fake_client)
    assert st["posted"] == 0
    assert fake_client.calls == []
    assert any("LDR_FORUM_API_KEY" in e for e in st["errors"])


def test_dry_run_works_without_key(log_path):
    """演练模式不需要凭据 —— 只预览内容。"""
    config.FORUM_ENABLED = True
    config.FORUM_API_KEY = ""
    config.FORUM_DRY_RUN = True
    st = forum.post_deals([_rec()], posted_path=log_path, verbose=False)
    assert st["dry_run"] is True
    assert len(st["posts"]) == 1
    # 演练不应写记录文件（否则会误以为已发布）
    assert not os.path.exists(log_path)


# --------------------------------------------------------------------------
# 幂等（最重要）
# --------------------------------------------------------------------------

def test_never_posts_same_thread_twice(log_path, fake_client):
    """同一 thread_id 绝不重复发布。"""
    _enable()
    recs = [_rec(thread_id="1"), _rec(thread_id="2")]

    st1 = forum.post_deals(recs, posted_path=log_path,
                           verbose=False, client=fake_client)
    assert st1["posted"] == 2
    assert len(fake_client.calls) == 2

    st2 = forum.post_deals(recs, posted_path=log_path,
                           verbose=False, client=fake_client)
    assert st2["posted"] == 0
    assert st2["already"] == 2
    assert len(fake_client.calls) == 2, "第二次不应再调用 API"


def test_posted_log_persists_across_runs(log_path, fake_client):
    """记录要落盘，新进程也能读到。"""
    _enable()
    forum.post_deals([_rec(thread_id="7")], posted_path=log_path,
                     verbose=False, client=fake_client)
    saved = json.load(open(log_path, encoding="utf-8"))
    assert "7" in saved
    assert saved["7"]["url"].endswith("/9001")


# --------------------------------------------------------------------------
# 限流
# --------------------------------------------------------------------------

def test_limit_caps_new_posts_per_run(log_path, fake_client):
    """每次最多新发 limit 条（已发过的不占配额）。"""
    _enable(FORUM_MAX_PER_RUN=2, FORUM_MAX_AGE_DAYS=999)
    recs = [_rec(thread_id=str(i)) for i in range(10)]

    st = forum.post_deals(recs, posted_path=log_path,
                          verbose=False, client=fake_client)
    assert st["posted"] == 2
    assert len(fake_client.calls) == 2


def test_limit_argument_overrides_config(log_path, fake_client):
    _enable(FORUM_MAX_PER_RUN=5, FORUM_MAX_AGE_DAYS=999)
    recs = [_rec(thread_id=str(i)) for i in range(10)]
    st = forum.post_deals(recs, limit=1, posted_path=log_path,
                          verbose=False, client=fake_client)
    assert st["posted"] == 1


def test_batches_drain_backlog_without_duplicates(log_path, fake_client):
    """分批发布：连跑多次应发完全部且无重复。"""
    _enable(FORUM_MAX_PER_RUN=3, FORUM_MAX_AGE_DAYS=999)
    recs = [_rec(thread_id=str(i)) for i in range(7)]

    seen = []
    for _ in range(5):
        st = forum.post_deals(recs, posted_path=log_path,
                              verbose=False, client=fake_client)
        seen.extend(p["thread_id"] for p in st["posts"])

    assert sorted(seen) == sorted({str(i) for i in range(7)}), "应恰好各发一次"
    assert len(seen) == len(set(seen)), "不应有重复"


# --------------------------------------------------------------------------
# 筛选
# --------------------------------------------------------------------------

def test_skips_old_posts(log_path, fake_client):
    _enable(FORUM_MAX_AGE_DAYS=3)
    st = forum.post_deals([_rec(age_days=30)], posted_path=log_path,
                          verbose=False, client=fake_client)
    assert st["posted"] == 0
    assert st["skipped"] == 1
    assert fake_client.calls == []


def test_skips_post_without_url(log_path, fake_client):
    _enable()
    st = forum.post_deals([_rec(url="")], posted_path=log_path,
                          verbose=False, client=fake_client)
    assert st["posted"] == 0


def test_worthy_accepts_key_events():
    """重点活动无条件通过。"""
    for ev in ("anniversary", "black_friday", "holiday", "restock"):
        ok, why = forum.is_worthy(_rec(event_type=ev, discount={}))
        assert ok, "%s 应通过" % ev


def test_worthy_accepts_big_discount():
    ok, why = forum.is_worthy(_rec(event_type="sale",
                                   discount={"percent_max": 50}))
    assert ok and "50%" in why


def test_worthy_rejects_plain_post():
    """无优惠的普通帖不该搬运。"""
    ok, why = forum.is_worthy(_rec(event_type="regular", discount={},
                                   prices=[], flagship_price=None))
    assert not ok


def test_worthy_accepts_recurring_discount():
    ok, _ = forum.is_worthy(_rec(event_type="sale",
                                 discount={"recurring": True}))
    assert ok


# --------------------------------------------------------------------------
# 厂商名质量门槛
# --------------------------------------------------------------------------

def test_provider_ok_accepts_domain():
    assert forum._provider_ok("centrixnodes.net")[0]
    assert forum._provider_ok("bero-host.de")[0]


def test_provider_ok_accepts_brand_names():
    """没有域名但确实是商家的品牌名应当通过。"""
    for name in ("Velohost", "NUXOA", "NameBig", "DartNode", "HostBrr"):
        assert forum._provider_ok(name)[0], "%s 应通过" % name


def test_provider_ok_rejects_location_phrases():
    """实测问题：'Dallas Dedicated' 是机房名+描述，不是商家名。"""
    assert not forum._provider_ok("Dallas Dedicated")[0]
    assert not forum._provider_ok("Dedicated Performance")[0]


def test_provider_ok_rejects_truncated_title():
    """实测问题：标题被截断后当成厂商名。"""
    assert not forum._provider_ok(
        "More of RackNerd & AdminBolt! Here\u2019s 50")[0]


def test_provider_ok_rejects_technical_hostname():
    """实测问题：lg.svr 是 looking glass 技术子域，不是厂商官网。"""
    assert not forum._provider_ok("lg.svr")[0]
    assert not forum._provider_ok("status.example.com")[0]


def test_provider_ok_accepts_new_tlds():
    """新顶级域不能被误拦 —— 实测 godlike.host 是真实商家。"""
    assert forum._provider_ok("godlike.host")[0]
    assert forum._provider_ok("Ava.hosting")[0]
    assert forum._provider_ok("some.cloud")[0]


def test_provider_ok_rejects_tech_abbreviation_combos():
    """实测问题：'BGP VPS' 是技术术语组合，不是商家名。"""
    assert not forum._provider_ok("BGP VPS")[0]
    assert not forum._provider_ok("IPv6 VPS")[0]


def test_provider_ok_rejects_punctuation_and_length():
    assert not forum._provider_ok("Some Host!")[0]
    assert not forum._provider_ok("A" * 40)[0]
    assert not forum._provider_ok("")[0]
    assert not forum._provider_ok("UNKNOWN")[0]


def test_informative_requires_two_facts():
    """只有一项信息的帖子发出去等于空帖。"""
    # 只有流量
    thin = _rec(specs={"bandwidth_tb": 8.0}, prices=[], flagship_price=None,
                cpu_models=[], locations=[])
    ok, why = forum._informative(thin)
    assert not ok
    assert "1 项" in why

    # 什么都没有
    empty = _rec(specs={}, prices=[], flagship_price=None,
                 cpu_models=[], locations=[])
    assert not forum._informative(empty)[0]


def test_informative_accepts_normal_post():
    assert forum._informative(_rec())[0]


def test_worthy_rejects_thin_post():
    """信息量不足时不搬运，即使折扣达标。"""
    thin = _rec(specs={"bandwidth_tb": 8.0}, prices=[], flagship_price=None,
                cpu_models=[], locations=[],
                discount={"recurring": True})
    ok, why = forum.is_worthy(thin)
    assert not ok
    assert "信息量" in why


def test_worthy_rejects_bad_provider():
    """厂商名不可靠时，即使折扣很大也不搬运。"""
    ok, why = forum.is_worthy(
        _rec(provider="Dallas Dedicated", event_type="black_friday"))
    assert not ok
    assert "厂商名" in why


# --------------------------------------------------------------------------
# 规格可信度（防串味）
# --------------------------------------------------------------------------

def test_plausible_rejects_huge_ram_at_tiny_price():
    """实测串味样本：192G 内存卖 $7、512G 卖 €2.6。"""
    r = _rec(specs={"vcpu": 6, "ram_gb": 192.0, "storage_gb": 2048.0},
             prices=[{"amount": 7.0, "currency": "$", "cycle": "month"}])
    assert not forum._plausible_specs(r)[0]

    r2 = _rec(specs={"vcpu": 10, "ram_gb": 512.0, "storage_gb": 4096.0},
              prices=[{"amount": 2.6, "currency": "€", "cycle": "month"}])
    assert not forum._plausible_specs(r2)[0]


def test_plausible_rejects_big_storage_at_tiny_price():
    """实测串味样本：4096G 存储配 $2.04（那是 VPS 的价格）。"""
    r = _rec(specs={"vcpu": 2, "ram_gb": None, "storage_gb": 4096.0},
             prices=[{"amount": 2.04, "currency": "$", "cycle": "month"}])
    assert not forum._plausible_specs(r)[0]


def test_plausible_allows_storage_vps():
    """存储型 VPS 天然「小内存 + 大存储」，但价格与存储量相称，不能拦。

    实测样本：SmokyHosts 5120G HDD @ $19.95、DediRock 2048G @ $8.88。
    """
    r = _rec(specs={"vcpu": 2, "ram_gb": 8.0, "storage_gb": 5120.0},
             prices=[{"amount": 19.95, "currency": "$", "cycle": "month"}])
    assert forum._plausible_specs(r)[0]

    r2 = _rec(specs={"vcpu": 2, "ram_gb": 4.0, "storage_gb": 2048.0},
              prices=[{"amount": 8.88, "currency": "$", "cycle": "month"}])
    assert forum._plausible_specs(r2)[0]


def test_plausible_allows_normal_vps():
    r = _rec(specs={"vcpu": 4, "ram_gb": 8.0, "storage_gb": 80.0},
             prices=[{"amount": 29.0, "currency": "$", "cycle": "year"}])
    assert forum._plausible_specs(r)[0]


def test_implausible_specs_omitted_from_post():
    """规格不可信时，标题与正文都不显示配置，但给出说明。"""
    r = _rec(specs={"vcpu": 6, "ram_gb": 192.0, "storage_gb": 2048.0},
             prices=[{"amount": 7.0, "currency": "$", "cycle": "month"}])
    title, body = forum.build_post(r)
    assert "192G" not in title
    assert "192G" not in body
    assert "不列出" in body, "应说明为何省略配置"


# --------------------------------------------------------------------------
# 正文生成
# --------------------------------------------------------------------------

def test_build_post_keeps_price_and_specs_untranslated():
    """价格与规格必须原样保留，不能翻译或改写。"""
    title, body = forum.build_post(_rec())
    assert "$29/年" in body
    assert "4 核" in body
    assert "8G 内存" in body
    assert "80G NVMe 存储" in body


def test_build_post_includes_source_and_disclaimer():
    _, body = forum.build_post(_rec())
    assert "lowendtalk.com/discussion/1001" in body
    assert "example.com" in body
    assert "机器人自动搬运" in body
    assert "以商家页面为准" in body


def test_build_post_title_has_event_and_provider():
    title, _ = forum.build_post(_rec())
    assert "黑五" in title
    assert "example.com" in title


def test_build_post_handles_missing_fields():
    """字段缺失不应抛异常。"""
    title, body = forum.build_post({"thread_id": "1", "url": "https://x.test"})
    assert title
    assert "以商家页面为准" in body


def test_number_formatting_drops_trailing_zero():
    """660.0 应显示为 660，而不是 660.0。"""
    assert forum._num(660.0) == "660"
    assert forum._num(25.0) == "25"
    assert forum._num(3.99) == "3.99"
    assert forum._money({"amount": 129.0, "currency": "€",
                         "cycle": "year"}) == "€129/年"


def test_build_post_includes_site_link():
    """帖子要带站点入口，把论坛读者导回汇总页。"""
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(config, "SITE_URL", "https://vps.example.com")
    try:
        _, body = forum.build_post(_rec())
    finally:
        mp.undo()
    assert "更多优惠汇总" in body
    assert "https://vps.example.com/" in body
    # 位置应在出处之后、免责声明之前
    assert body.index("更多优惠汇总") > body.index("原帖地址")
    assert body.index("更多优惠汇总") < body.index("机器人自动搬运")


def test_build_post_omits_site_link_when_unset():
    """未配置站点地址时不输出半截链接。"""
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(config, "SITE_URL", "")
    try:
        _, body = forum.build_post(_rec())
    finally:
        mp.undo()
    assert "更多优惠汇总" not in body


def test_build_post_includes_plan_table():
    rec = _rec(plans=[{"name": "Starter", "vcpu": 2, "ram_gb": 4.0,
                       "storage_gb": 40.0, "bandwidth_tb": 2.0,
                       "price": {"amount": 19.0, "currency": "$",
                                 "cycle": "year"}}])
    _, body = forum.build_post(rec)
    assert "| 套餐 |" in body
    assert "Starter" in body
    assert "$19/年" in body


# --------------------------------------------------------------------------
# 服务类型识别（避免把邮箱托管当成 VPS）
# --------------------------------------------------------------------------

def test_detect_kind_email_hosting():
    """实测遇到的问题：MailBig 是邮箱托管，不是 VPS。"""
    r = _rec(title="MailBig Halloween Sale - Email Hosting From £3.37/yr")
    assert forum.detect_kind(r) == "email"


def test_detect_kind_defaults_to_vps():
    assert forum.detect_kind(_rec(title="Black Friday KVM VPS Deals")) == "vps"


def test_detect_kind_storage_and_dedicated():
    assert forum.detect_kind(
        _rec(title="Storage VPS with 2TB")) == "storage"
    assert forum.detect_kind(
        _rec(title="Dedicated Server Sale")) == "dedicated"


def test_non_vps_kind_is_labeled_in_title_and_body():
    """非 VPS 服务必须在标题和正文中标注，避免误导。"""
    rec = _rec(title="MailBig Email Hosting Sale", event_type="holiday")
    title, body = forum.build_post(rec)
    assert "邮箱托管" in title
    assert "非 VPS" in body


def test_vps_kind_not_labeled():
    """普通 VPS 不应被加上多余标注。"""
    title, body = forum.build_post(_rec(title="KVM VPS Black Friday"))
    assert "邮箱托管" not in title
    assert "非 VPS" not in body


# --------------------------------------------------------------------------
# 容错
# --------------------------------------------------------------------------

def test_single_failure_does_not_stop_others(log_path):
    """单条失败不应影响其余条目。"""
    _enable(FORUM_MAX_PER_RUN=5, FORUM_MAX_AGE_DAYS=999)
    calls = []

    def flaky(title, content):
        calls.append(title)
        if len(calls) == 1:
            raise forum.ForumError("模拟失败")
        return {"id": 1, "url": "https://forum.test/d/1"}

    recs = [_rec(thread_id=str(i)) for i in range(3)]
    st = forum.post_deals(recs, posted_path=log_path,
                          verbose=False, client=flaky)
    assert st["failed"] == 1
    assert st["posted"] == 2


def test_corrupt_log_does_not_crash(tmp_path, fake_client):
    """记录文件损坏时应能继续（而不是抛异常）。"""
    _enable()
    p = tmp_path / "bad.json"
    p.write_text("{ this is not json", encoding="utf-8")
    st = forum.post_deals([_rec(thread_id="5")], posted_path=str(p),
                          verbose=False, client=fake_client)
    assert st["posted"] == 1


# --------------------------------------------------------------------------
# 清理已删除帖子的记录
# --------------------------------------------------------------------------

def test_verify_posted_drops_deleted(monkeypatch):
    """帖子在论坛被删后，记录应被剔除，否则那些活动永远发不出去。

    实测遇到：删掉 4 条测试帖后，对应活动因记录说「已发过」而不再搬运。
    """
    posted = {
        "1": {"discussion_id": "100", "title": "alive"},
        "2": {"discussion_id": "200", "title": "deleted"},
    }

    def fake_request(method, path, payload=None, **kw):
        if path == "/discussions/200":
            raise forum.ForumError("HTTP 404: Not Found")
        return {"data": {"id": "100"}}

    monkeypatch.setattr(forum, "_request", fake_request)
    kept, dropped = forum.verify_posted(
        posted, api_key="k", base_url="https://f.test", verbose=False)
    assert dropped == 1
    assert "1" in kept
    assert "2" not in kept


def test_verify_posted_keeps_on_network_error(monkeypatch):
    """网络抖动时不能剔除记录 —— 那会导致重复发帖。"""
    posted = {"1": {"discussion_id": "100", "title": "x"}}

    def fake_request(method, path, payload=None, **kw):
        raise forum.ForumError("网络错误：timed out")

    monkeypatch.setattr(forum, "_request", fake_request)
    kept, dropped = forum.verify_posted(
        posted, api_key="k", base_url="https://f.test", verbose=False)
    assert dropped == 0
    assert "1" in kept
