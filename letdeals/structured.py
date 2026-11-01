"""schema.org JSON-LD 结构化数据。

站点是纯静态产物，没有运行时渲染，所以结构化数据在建站时一次性生成：
搜索引擎靠它理解「这是优惠列表 / FAQ / 面包屑」，进而拿到富摘要展示。
所有字段都可能来自抓取的帖文，因此输出前必须做 </script> 逃逸防护
（见 script_html，与 render._json_for_script 同一思路）。
"""

from __future__ import annotations

import json

from . import config

_CONTEXT = "https://schema.org"

# 货币符号 → ISO 4217。schema.org 的 priceCurrency 只认 ISO 代码。
CURRENCY_ISO = {"$": "USD", "€": "EUR", "£": "GBP"}

# 内部库存状态 → schema.org Offer/availability。
# UNVERIFIED / UNVERIFIED_STALE / NOT_OBSERVED 不映射：
# 未核实就向搜索引擎声称「有货」，比缺字段更糟。
AVAIL_SCHEMA = {"ON_SALE": "InStock", "PARTIAL": "LimitedAvailability",
                "RESTOCKING": "LimitedAvailability", "SOLD_OUT": "SoldOut",
                "LIKELY_SOLD_OUT": "SoldOut", "ENDED": "SoldOut"}


def _put(d: dict, key: str, value) -> None:
    """值非空才写入。缺字段好过脏字段：空串/None 会让结构化数据被判无效。"""
    if value:
        d[key] = value


# --------------------------------------------------------------------------
# 站点级
# --------------------------------------------------------------------------

def website_schema(payload: dict) -> dict:
    """站点级 WebSite 信息。

    payload 参数暂不取值，保留是为了与其它 builder 签名一致，
    日后要带生成时间之类的站点元信息时不用改调用方。
    """
    schema = {"@context": _CONTEXT, "@type": "WebSite",
              "name": config.SITE_TITLE, "description": config.SITE_DESC,
              "inLanguage": "zh-CN",
              "publisher": {"@type": "Organization",
                            "name": config.SITE_TITLE}}
    # 未绑定域名时省略 url：空串会污染 canonical 信号
    _put(schema, "url", config.SITE_URL)
    return schema


# --------------------------------------------------------------------------
# 列表 / 导航
# --------------------------------------------------------------------------

def _offer(rec: dict, url: str) -> dict | None:
    """Offer 只在有可解析的数字价格时生成。

    没有价格（或币种不在 CURRENCY_ISO 里）的 Offer 会被
    搜索引擎判为无效结构，宁可不输出。
    """
    p = rec.get("sort_price") or rec.get("flagship_price")
    if not isinstance(p, dict) or not isinstance(p.get("amount"), (int, float)):
        return None
    currency = CURRENCY_ISO.get(p.get("currency"))
    if not currency:
        return None
    offer = {"@type": "Offer", "price": p["amount"], "priceCurrency": currency}
    _put(offer, "availability", AVAIL_SCHEMA.get(rec.get("availability")))
    _put(offer, "url", url)
    return offer


def item_list_schema(records: list, limit: int = 60) -> dict:
    """优惠列表 ItemList。

    保持调用方给出的顺序 —— 排序策略属于展示层，这里不重排。
    落地链接优先用联盟链接（vendor_url）：那是我们希望用户到达的地址。

    Google 富摘要门槛（Semrush 2026-11-01 结构化数据 53 项的修法）：
    - Merchant listing 要求 Product 有 image：帖文没有单品图，
      统一用站内 og-image.png（与 render/bfpage 的 og:image 同一文件），
      未绑定 SITE_URL 时不输出空 image。
    - Product 富摘要要求 offers / aggregateRating / review 三者至少其一：
      拿不到数字价格的记录（到商家查价、未知币种）无法编造评分，
      直接不进结构化 ItemList —— 页面上的行照常展示，
      只是不再以 Product 形式向搜索引擎声称。
    - Carousel 要求 ListItem 的 item 与 url 二选一：只留 item，
      Product 里本来就有 url，不再在 ListItem 层重复写 url。
    """
    elements = []
    image_url = (f"{config.SITE_URL}/og-image.png"
                 if config.SITE_URL else None)
    for rec in records:
        if len(elements) >= limit:
            break
        url = rec.get("vendor_url") or rec.get("url") or ""
        offer = _offer(rec, url)
        if offer is None:
            # 无有效 Offer 的 Product 达不到富摘要门槛，跳过结构化输出。
            continue
        discount = rec.get("discount") or {}
        codes = [c for c in (discount.get("codes") or []) if c]

        description = " · ".join(
            part for part in (rec.get("display_spec"), rec.get("display_price"))
            if part)
        if codes:
            description = (description + " · 优惠码 " if description
                           else "优惠码 ") + "/".join(codes)

        product = {"@type": "Product",
                   "name": ((rec.get("provider") or "") + " VPS 优惠").strip()}
        _put(product, "description", description)
        _put(product, "url", url)
        _put(product, "image", image_url)
        product["offers"] = offer

        item = {"@type": "ListItem", "position": len(elements) + 1}
        _put(item, "name", " ".join(
            part for part in (rec.get("provider"), rec.get("display_price"))
            if part))
        item["item"] = product
        elements.append(item)
    return {"@context": _CONTEXT, "@type": "ItemList",
            "itemListElement": elements}


def breadcrumb_schema(items: list) -> dict:
    """BreadcrumbList。item 放地址字符串即可（schema.org 允许 URL 文本）。

    非法项跳过并保持编号连续，避免出现 position 断档。
    """
    elements = []
    for name, url in items:
        if not name or not url:
            continue
        elements.append({"@type": "ListItem", "position": len(elements) + 1,
                         "name": name, "item": url})
    return {"@context": _CONTEXT, "@type": "BreadcrumbList",
            "itemListElement": elements}


# --------------------------------------------------------------------------
# FAQ
# --------------------------------------------------------------------------

def faq_schema(faq: list) -> dict:
    """FAQPage。问/答都是调用方给的纯文本，不做 HTML 处理，转义交给 json。"""
    main_entity = []
    for question, answer in faq:
        if not question or not answer:
            continue
        main_entity.append({
            "@type": "Question", "name": question,
            "acceptedAnswer": {"@type": "Answer", "text": answer},
        })
    return {"@context": _CONTEXT, "@type": "FAQPage",
            "mainEntity": main_entity}


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------

def script_html(schema: dict) -> str:
    """把 schema 包成 <script type="application/ld+json"> 块。

    <、>、& 必须转成 Unicode 转义（与 render._json_for_script 同理）：
    帖文数据里的一句「</script>」就能提前闭合标签、注入任意脚本。
    """
    body = (json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))
    return ('<script type="application/ld+json">' + body
            + "</script>\n")
