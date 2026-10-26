"""Conservative cross-run tracking and meaningful, publishable change reports."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .extract import age_status


FIELDS = {
    "display_price": "报价", "display_spec": "配置", "availability": "库存",
    "discount": "优惠规则", "price_bound": "价格绑定",
}


def display_time(value):
    parsed = _date(value)
    return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S 北京时间") if parsed else "未知"


def daily_report(reports):
    if not reports:
        return {}
    today = display_time(reports[0].get("generated_at"))[:10]
    result = {"generated_at": reports[0].get("generated_at"), "baseline": reports[0].get("baseline")}
    for category in ("added", "changed", "missing", "restored", "expired"):
        unique = {}
        for report in reports:
            if display_time(report.get("generated_at"))[:10] == today:
                for item in report.get(category, []):
                    unique.setdefault(item["id"], item)
        result[category] = list(unique.values())
    return result


def _date(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def key(record):
    return f"{record.get('source', '')}:{record.get('thread_id') or record.get('url', '')}"


def entry(record, **extra):
    return {"id": key(record), "provider": record.get("provider", ""),
            "title": record.get("title", ""), "url": record.get("url", ""), **extra}


def _discount(value):
    value = value or {}
    return {"recurring": bool(value.get("recurring")),
            "introductory": bool(value.get("introductory")),
            "rule_conflict": bool(value.get("rule_conflict")),
            "percent_max": value.get("percent_max"),
            "codes": sorted(value.get("codes", [])), "extras": sorted(value.get("extras", []))}


def reconcile(payload, previous, recent_days=90):
    """Missing feed entries are not sold out. Reject major per-source losses first."""
    now = _date(payload["generated_at"])
    fresh = payload["records"]
    old_records = previous.get("records", [])
    observed = payload.get("by_source", {})
    baseline = previous.get("observed_by_source", previous.get("by_source", {}))
    for source, count in baseline.items():
        if count >= 10 and observed.get(source, 0) < count * 0.6:
            raise ValueError(f"{source}: observed records dropped by more than 40%; preserve previous site")
    old = {key(r): r for r in old_records}
    current = {key(r): r for r in fresh}
    report = {"generated_at": payload["generated_at"], "baseline": not bool(old_records),
              "added": [], "changed": [], "missing": [], "restored": [], "expired": []}
    for record in fresh:
        before = old.get(key(record))
        tracking = (before or {}).get("tracking", {})
        record["tracking"] = {
            "state": "observed", "last_seen": payload["generated_at"],
            "first_seen": tracking.get("first_seen", previous.get("generated_at") if before else None)
                          or payload["generated_at"],
        }
        if not before:
            if old_records:
                report["added"].append(entry(record))
            continue
        if tracking.get("state") == "missing":
            report["restored"].append(entry(record))
        changes = {}
        for field, label in FIELDS.items():
            # A missing observation clears a previous stock claim; do not call its
            # reappearance a stock change unless a new observation differs.
            old_value = before.get("last_observed_availability") if field == "availability" and tracking.get("state") == "missing" else before.get(field)
            new_value = record.get(field)
            if field == "discount":
                old_value, new_value = _discount(old_value), _discount(new_value)
            if old_value != new_value:
                changes[label] = {"before": old_value, "after": new_value}
        if changes:
            report["changed"].append(entry(record, changes=changes))

    for identity, before in old.items():
        if identity in current:
            continue
        tracking = before.get("tracking", {})
        last_seen = tracking.get("last_seen") or previous.get("generated_at")
        seen = _date(last_seen) or now
        posted = _date(before.get("posted"))
        age = (now - posted).days if posted else None
        if (now - seen).days >= 30 or (recent_days and age is not None and age > recent_days):
            report["expired"].append(entry(before, reason="超出保留窗口；不代表确认停售"))
            continue
        retained = deepcopy(before)
        retained["tracking"] = {**tracking, "state": "missing", "last_seen": last_seen,
                                "missing_since": tracking.get("missing_since") or payload["generated_at"]}
        retained["last_observed_availability"] = before.get("last_observed_availability", before.get("availability"))
        retained["availability"] = "NOT_OBSERVED"
        retained["age_days"] = age
        retained["age_status"] = age_status(age)
        retained["order_url"] = None
        fresh.append(retained)
        if tracking.get("state") != "missing":
            report["missing"].append(entry(retained))
    payload["observed_count"] = len(current)
    payload["observed_by_source"] = observed.copy()
    payload["count"] = len(fresh)
    payload["by_source"] = {source: sum(r.get("source") == source for r in fresh)
                            for source in {r.get("source") for r in fresh}}
    payload["updates"] = report
    history = previous.get("update_history", [])
    # Same-day code rebuilds must not erase the day's earlier changes.
    payload["update_history"] = ([report] + history)[:30]
    return payload
