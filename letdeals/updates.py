"""Conservative cross-run tracking and meaningful, publishable change reports."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json

from .extract import AVAIL_CN, age_status


FIELDS = {
    "display_price": "报价", "display_spec": "配置", "availability": "库存",
    "discount": "优惠规则", "price_bound": "价格绑定",
    "plans": "套餐表", "prices": "帖文报价列表", "locations": "机房地区",
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


def describe_changes(changes):
    def value(label, item):
        if label == "机房地区":
            return " / ".join(item or []) or "未知"
        if label in {"套餐表", "帖文报价列表"} and isinstance(item, list):
            def price(p):
                cycles = {"month": "/月", "year": "/年", "quarter": "/季"}
                return f"{p.get('currency', '')}{p.get('amount', '?')}{cycles.get(p.get('cycle'), '（周期待核对）')}"
            if label == "帖文报价列表":
                return " / ".join(price(p) for p in item) or "未提取到报价"
            return " / ".join(f"{p.get('name', '未命名套餐')} {price(p.get('price') or {})}" for p in item) or "未提取到套餐"
        if label == "库存":
            return AVAIL_CN.get(item, item or "未知")
        if label == "价格绑定":
            return "已绑定具体套餐" if item else "未绑定具体套餐"
        if label == "优惠规则" and isinstance(item, dict):
            parts = []
            if item.get("recurring"):
                parts.append("循环优惠")
            if item.get("introductory"):
                parts.append("首期优惠，续费待核对")
            if item.get("rule_conflict"):
                parts.append("首期与循环描述冲突")
            if item.get("percent_max"):
                parts.append(f"最高 {item['percent_max']}% off")
            if item.get("codes"):
                parts.append("码 " + "/".join(item["codes"]))
            parts.extend(item.get("extras", []))
            return " / ".join(parts) or "未明确优惠规则"
        return str(item) if item is not None else "未知"
    return "；".join(f"{label}：{value(label, pair.get('before'))} → {value(label, pair.get('after'))}"
                    for label, pair in changes.items())


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


def comparable(field, value):
    if field == "discount":
        return _discount(value)
    if field == "plans":
        value = [{key: plan.get(key) for key in ("name", "vcpu", "ram_gb", "storage_gb", "bandwidth_tb", "price")}
                 for plan in value or []]
    if field in {"plans", "prices", "locations"}:
        return sorted(value or [], key=lambda item: json.dumps(item, sort_keys=True))
    return value


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
            old_value, new_value = comparable(field, old_value), comparable(field, new_value)
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
