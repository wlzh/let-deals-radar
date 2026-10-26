from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from letdeals import render
from letdeals.extract import extract_discount
from letdeals.presentation import prepare
from letdeals.updates import daily_report, display_time, history_reports, reconcile


def record(identity="1", **extra):
    return {"source": "let", "thread_id": identity, "provider": "Example",
            "title": "Example sale", "url": "https://example.invalid/" + identity,
            "posted": "Sun, 25 Oct 2026 00:00:00 +0000", "availability": "ON_SALE",
            "display_price": "$5/月", **extra}


def payload(records, date="2026-10-26T07:00:00+00:00"):
    return {"generated_at": date, "records": records, "count": len(records),
            "by_source": {"let": len(records)}}


def test_first_run_is_baseline_not_all_new():
    result = reconcile(payload([record()]), {})
    assert result["updates"]["baseline"]
    assert result["updates"]["added"] == []


def test_missing_retained_without_claiming_stock():
    before = payload([record(), record("2")], "2026-10-25T07:00:00+00:00")
    result = reconcile(payload([record()]), before)
    missing = result["records"][1]
    assert result["count"] == 2 and result["observed_count"] == 1
    assert missing["availability"] == "NOT_OBSERVED"
    assert missing["last_observed_availability"] == "ON_SALE"
    assert missing["tracking"]["last_seen"] == before["generated_at"]
    assert len(result["updates"]["missing"]) == 1
    prepare(missing)
    assert "核实" in missing["purchase_label"] and not missing["order_url"]
    assert "未检出" in render._initial_rows([missing])


def test_missing_is_not_reported_repeatedly():
    old = reconcile(payload([record(), record("2")]), {})
    missing = reconcile(payload([record()], "2026-10-27T07:00:00+00:00"), old)
    again = reconcile(payload([record()], "2026-10-28T07:00:00+00:00"), missing)
    assert not again["updates"]["missing"]
    assert len(again["update_history"]) == 1
    assert again["last_change_at"] == "2026-10-27T07:00:00+00:00"


def test_returned_record_restored_not_new():
    old = reconcile(payload([record(), record("2")]), {})
    missing = reconcile(payload([record()], "2026-10-27T07:00:00+00:00"), old)
    restored = reconcile(payload([record(), record("2")], "2026-10-28T07:00:00+00:00"), missing)
    assert len(restored["updates"]["restored"]) == 1
    assert not restored["updates"]["changed"]
    assert not restored["updates"]["added"]


def test_meaningful_changes_ignore_fetch_time_and_score():
    before = payload([record(score=1)])
    result = reconcile(payload([record(score=2, replies={"fetched_at": "now"})]), before)
    assert not result["updates"]["changed"]
    changed = reconcile(payload([record(display_price="$4/月", availability="SOLD_OUT")]), before)
    assert set(changed["updates"]["changed"][0]["changes"]) == {"报价", "库存"}


def test_added_and_retention_expiry():
    before = payload([record()], "2026-09-01T07:00:00+00:00")
    result = reconcile(payload([record("2")]), before)
    assert len(result["updates"]["added"]) == 1
    assert len(result["updates"]["expired"]) == 1
    assert result["count"] == 1


def test_age_window_and_history_limit():
    before = payload([record(posted="Sun, 01 Jan 2023 00:00:00 +0000")])
    before["update_history"] = [{"generated_at": (datetime(2026, 10, 25, tzinfo=timezone.utc) - timedelta(days=i)).isoformat(),
                                 "added": [{"id": str(i), "provider": "Example"}]} for i in range(40)]
    result = reconcile(payload([record("2")]), before, 90)
    assert len(result["updates"]["expired"]) == 1
    assert len(result["update_history"]) == 30


def test_per_source_drop_fails_before_mutating_payload():
    before = payload([record(str(i)) for i in range(20)])
    new = payload([record()])
    original = deepcopy(new)
    with pytest.raises(ValueError, match="40%"):
        reconcile(new, before)
    assert new == original


def test_drop_uses_observed_baseline_not_retained_total():
    before = payload([record(str(i)) for i in range(100)])
    before["observed_by_source"] = {"let": 10}
    assert reconcile(payload([record(str(i)) for i in range(10)]), before)


@pytest.mark.parametrize("text", ["Additional 10% off first month. recurring prices listed below",
                                 "First-year discount, lifetime regular pricing", "首月优惠，permanent pricing"])
def test_introductory_rule_cannot_be_permanent(text):
    discount = extract_discount(text)
    assert not discount["recurring"]
    assert discount["introductory"] and discount["rule_conflict"]
    assert "待核对" in discount["strength"] and "循环/永久" not in discount["strength"]


def test_report_html_escapes_untrusted_titles():
    result = reconcile(payload([record("2", title="<script>alert(1)</script>", provider="<script>alert(1)</script>")]), payload([record()]))
    markup = render._updates_html(result)
    assert "<script>" not in markup and "&lt;script&gt;" in markup
    assert markup.count('<details ') == 2
    assert 'data/updates.json' not in markup


def test_daily_summary_uses_china_date_and_deduplicates():
    reports = [{"generated_at": "2026-10-26T17:00:00Z", "added": [{"id": "x"}]},
               {"generated_at": "2026-10-26T16:00:00Z", "added": [{"id": "x"}, {"id": "y"}]},
               {"generated_at": "2026-10-26T15:00:00Z", "added": [{"id": "z"}]}]
    assert len(daily_report(reports)["added"]) == 2
    assert display_time(reports[0]["generated_at"]).startswith("2026-10-27 01:00")


def test_old_discount_schema_does_not_create_false_changes():
    before = payload([record(discount={"recurring": False})])
    result = reconcile(payload([record(discount={"recurring": False, "introductory": False, "rule_conflict": False})]), before)
    assert not result["updates"]["changed"]


def test_cli_drop_guard_preserves_all_artifacts(tmp_path, monkeypatch):
    import json
    from letdeals import cli
    path = tmp_path / "deals.json"
    previous = payload([record(str(i)) for i in range(20)])
    path.write_text(json.dumps(previous))
    monkeypatch.setattr(cli.pipeline, "run", lambda **kw: payload([record()]))
    def unexpected(*args, **kwargs):
        raise AssertionError("guard must fail before saving")
    monkeypatch.setattr(cli.pipeline, "save", unexpected)
    assert cli.main(["--json-out", str(path), "--quiet"]) == 1
    assert json.loads(path.read_text()) == previous


def test_non_flagship_plan_price_change_is_reported():
    plans = [{"name": "Small", "price": {"amount": 5, "currency": "$", "cycle": "month"}},
             {"name": "Large", "price": {"amount": 20, "currency": "$", "cycle": "month"}}]
    before = payload([record(plans=plans)])
    new_plans = deepcopy(plans)
    new_plans[0]["price"]["amount"] = 4
    report = reconcile(payload([record(plans=new_plans)]), before)["updates"]
    assert "套餐表" in report["changed"][0]["changes"]


def test_package_and_location_order_only_is_not_a_change():
    plans = [{"name": "S"}, {"name": "L"}]
    before = payload([record(plans=plans, locations=["Paris", "London"])])
    report = reconcile(payload([record(plans=list(reversed(plans)), locations=["London", "Paris"])]), before)["updates"]
    assert not report["changed"]


def test_many_runs_keep_one_daily_history_with_earliest_and_latest_values():
    state = reconcile(payload([record()]), {})
    for minute in range(1, 41):
        state = reconcile(payload([record(display_price=f"${minute}/月")], f"2026-10-26T08:{minute:02d}:00+00:00"), state)
    assert len(state["update_history"]) == 1
    pair = state["update_history"][0]["changed"][0]["changes"]["报价"]
    assert pair == {"before": "$5/月", "after": "$40/月"}


def test_today_does_not_reuse_yesterdays_changes():
    reports = [{"generated_at": "2026-10-25T07:00:00Z", "added": [{"id": "x"}]}]
    assert not daily_report(reports, "2026-10-26T07:00:00Z")["added"]


def test_history_window_uses_china_calendar_and_excludes_empty_days():
    reports = [{"generated_at": "2026-09-26T15:59:59Z", "added": [{"id": "old"}]},
               {"generated_at": "2026-09-26T16:00:00Z", "added": [{"id": "within"}]},
               {"generated_at": "2026-10-26T00:00:00Z", "added": []}]
    history = history_reports(reports, "2026-10-26T07:00:00Z")
    assert len(history) == 1 and history[0]["added"][0]["id"] == "within"


def test_home_has_only_two_closed_disclosures_above_intro(tmp_path):
    state = reconcile(payload([record("2")]), payload([record()]))
    for i in range(25):
        state["update_history"].insert(0, {"generated_at": f"2026-10-26T08:{i:02d}:00Z", "added": []})
    render.build_site(state, tmp_path, verbose=False)
    page = (tmp_path / "index.html").read_text()
    start = page.index('<section class="card updates"')
    end = page.index('</section>', start)
    card = page[start:end]
    assert card.count('<details ') == 2 and '<details open' not in card
    assert card.count('<ul>') == 2
    assert '最近 30 天' in card and '最近检查' in card and '最近发现变化' in card
    assert '更新明细</summary>' not in card
    assert start < page.index('<h2>少翻帖子')


def test_empty_today_has_no_empty_list_or_zero_counters():
    report = {"generated_at": "2026-10-26T07:00:00Z", "added": []}
    markup = render._updates_html({"generated_at": report["generated_at"], "updates": report})
    assert '<ul>' not in markup and '新增 0 条' not in markup
    assert markup.count('<details ') == 2
