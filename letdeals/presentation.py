"""Conservative display fields shared by the live page and Markdown export."""

import re
from urllib.parse import urlsplit

from . import affiliate
from .extract import CODE_STOP, LOCATIONS


def safe_url(url):
    try:
        p = urlsplit(url or "")
        return url if p.scheme in ("http", "https") and p.hostname and not p.username and not p.password else None
    except ValueError:
        return None


def prepare(r):
    """Never imply that independent maxima and a low price form one plan."""
    title = r.get("title", "")
    places = {p.lower(): p for p in LOCATIONS}
    r["locations"] = list(dict.fromkeys(places.get(p.lower(), p) for p in r.get("locations", []) if isinstance(p, str)))
    for name, domain in (("SVR4U", "svr4u.com"), ("LayerOne", "layeronecloud.com")):
        prefix = re.sub(r"^\[(?:MY/SG/US|US|EU|UK)\]\s*", "", title, flags=re.I)
        if re.match(r"^\[?" + name + r"\b", prefix, re.I):
            r["provider"] = name
            if not r.get("vendor_url"):
                r["vendor_url"] = "https://" + domain + "/"
    url = safe_url(r.get("vendor_url"))
    r["vendor_url"] = affiliate.rewrite(url) if url else None
    r["affiliate"] = bool(affiliate._match(r.get("vendor_url") or "", affiliate._load())[0])
    r["url"] = safe_url(r.get("url")) or ""
    r["forum_url"] = None
    discount = r.setdefault("discount", {})
    discount["codes"] = [c for c in discount.get("codes", [])
                         if isinstance(c, str) and c.upper() not in CODE_STOP
                         and re.fullmatch(r"[A-Za-z0-9_-]{3,32}", c)]
    strength = re.sub(r"(?: · )?码 [^·]*", "", discount.get("strength") or "").strip(" ·")
    if discount["codes"]:
        strength += (" · " if strength else "") + "码 " + "/".join(discount["codes"][:3])
    discount["strength"] = strength

    # Old snapshots have no price-cycle provenance. Do not trust their
    # historical hard-coded annual flagship price during a render-only build.
    plans = r.get("plans") or []
    priced = [p for p in plans if (p.get("price") or {}).get("cycle") in ("year", "month", "quarter")]
    selected = max(priced, key=lambda p: (p.get("ram_gb") or 0, p.get("vcpu") or 0), default=None)
    cycles = {"year": "/年", "month": "/月", "quarter": "/季"}
    r["price_bound"] = bool(selected)
    if selected:
        p = selected["price"]
        r["display_spec"] = " / ".join(str(selected[k]) + unit for k, unit in
            (("vcpu", " 核"), ("ram_gb", " GB 内存"), ("storage_gb", " GB 存储")) if selected.get(k))
        r["display_price"] = f'{p["currency"]}{p["amount"]:g}{cycles[p["cycle"]]}'
        r["display_price_note"] = selected.get("name", "表格套餐") + " · 自动解析，需核对"
        r["sort_price"] = p
    else:
        bits = []
        for key, label in (("vcpu", "核"), ("ram_gb", "GB 内存"), ("storage_gb", "GB 存储")):
            values = [v for v in (r.get("specs_all") or {}).get(key, []) if isinstance(v, (int, float)) and v > 0]
            if values:
                lo, hi = min(values), max(values)
                bits.append((f"{lo:g}–{hi:g}" if lo != hi else f"{lo:g}") + " " + label)
        r["display_spec"] = "帖文提及：" + " / ".join(bits) if bits else "查看原帖核对配置"
        # Prefer explicit price in title: it is a quote, not a matched plan.
        from .extract import extract_prices
        prices = extract_prices(title) or r.get("prices") or []
        p = next((p for p in prices if p.get("cycle") in cycles), None)
        r["display_price"] = f'{p["currency"]}{p["amount"]:g}{cycles[p["cycle"]]}' if p else "到商家查价"
        r["display_price_note"] = "帖文报价，未与配置绑定" if p else "价格周期待核对"
        r["sort_price"] = p
    r["purchase_label"] = "查看活动与下单"
    if r.get("availability") in ("SOLD_OUT", "ENDED", "RESTOCKING", "LIKELY_SOLD_OUT"):
        r["purchase_label"] = "查看补货与替代套餐"
    elif r.get("availability") != "ON_SALE":
        r["purchase_label"] = "到商家核对价格与库存"
    return r
