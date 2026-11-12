"""Stable, source-attributed landing pages; never synthesize purchase URLs."""

import hashlib
import html
from pathlib import Path

from . import config, __version__
from .presentation import safe_url
from .updates import display_time


def detail_path(rec):
    source = safe_url(rec.get("url"))
    if not source:
        return ""
    return "/deals/" + hashlib.sha256(source.encode()).hexdigest()[:24] + "/"


POLICY = """<h2>信息来源与购买提醒</h2>
<p>本站自动整理 LowEndTalk / LowEndBox 公开信息，不是商家，不代收款。
抓取时间不代表商家确认时间；未检出不等于停售，用户回帖不等于商家承诺。
未绑定套餐的报价不能与帖文中的配置拼成同一套餐。</p>
<p>付款前核对计费周期、续费价格、税费、机房、流量、退款条款和最终库存。
首期折扣不代表续费同价。部分链接为推广链接，购买后本站可能获得佣金；
推广关系不代表质量担保，也不保证最低价。</p>
<p>发现错误请通过页面原帖入口核对并向原帖作者反馈；本站后续抓取会重新整理，
但无法保证即时同步。保留来源是为了让信息可追溯。</p>"""


def write_details(payload, out_dir, style):
    from .render import AVAIL_CN
    from .structured import item_list_schema, script_html

    root = Path(out_dir)
    paths = []
    esc = html.escape
    for rec in payload.get("records", []):
        rec.pop("detail_path", None)
        path = detail_path(rec)
        if not path:
            continue
        paths.append(path)
        rec["detail_path"] = path
        title = rec.get("title") or "优惠详情"
        description = " · ".join(str(rec.get(k) or "") for k in
                                  ("provider", "service_label", "display_price", "display_price_note"))
        canonical = config.SITE_URL.rstrip("/") + path if config.SITE_URL else ""
        meta = f'<link rel="canonical" href="{esc(canonical, quote=True)}">' if canonical else ""
        meta += f'<meta property="og:title" content="{esc(title, quote=True)}"><meta property="og:description" content="{esc(description, quote=True)}"><meta property="og:type" content="website">'
        if canonical:
            meta += f'<meta property="og:url" content="{esc(canonical, quote=True)}">'
        # Only bound plans may describe a Product, and only on their detail page.
        products = item_list_schema([rec])["itemListElement"]
        if products:
            product = products[0]["item"]
            product["name"] = title
            meta += script_html({"@context": "https://schema.org", **product})
        fields = [("商家", rec.get("provider")), ("类型", rec.get("service_label")),
                  ("报价", rec.get("display_price")), ("报价说明", rec.get("display_price_note")),
                  ("配置", rec.get("display_spec")),
                  ("库存", AVAIL_CN.get(rec.get("availability"), "待确认")),
                  ("地区", " / ".join(rec.get("locations") or [])),
                  ("优惠码", " / ".join((rec.get("discount") or {}).get("codes") or [])),
                  ("原帖发布时间", rec.get("posted")),
                  ("最后检出", display_time((rec.get("tracking") or {}).get("last_seen")))]
        rows = "".join(f'<tr><th>{esc(k)}</th><td>{esc(str(v or "待核对"))}</td></tr>' for k, v in fields)
        actions = ""
        vendor = safe_url(rec.get("vendor_url"))
        if vendor:
            actions += f'<p><a class="purchase" rel="noopener nofollow sponsored" href="{esc(vendor, quote=True)}">{esc(rec.get("purchase_label") or "查看商家活动")}</a></p>'
        if not vendor:
            actions += '<p>购买入口待核实，暂不提供下单按钮。可返回<a href="/">全部优惠与商家目录</a>浏览其他活动。</p>'
        actions += f'<details><summary>信息来源与条款核验</summary><p><a rel="noopener" href="{esc(rec["url"], quote=True)}">查看原帖与完整条款</a></p></details>'
        generated = payload.get("generated_at") or ""
        document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} | {esc(config.SITE_TITLE)}</title>
<meta name="description" content="{esc(description, quote=True)}">{meta}
<style>{style}</style></head><body><main class="wrap">
<nav><a href="/">全部优惠</a> · <a href="/black-friday-2026/">黑五专题</a></nav>
<h1>{esc(title)}</h1><p>数据整理时间：<time datetime="{esc(generated, quote=True)}">{esc(display_time(generated))}</time></p>
<table>{rows}</table>{actions}{POLICY}<p>程序版本 {__version__}</p>
</main>
</body></html>'''
        target = root / path.strip("/") / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document, encoding="utf-8")
    # Remove only pages owned by this generator after their retention window.
    for old in (root / "deals").glob("*/index.html"):
        if "/deals/" + old.parent.name + "/" not in paths:
            if len(old.parent.name) == 24 and all(c in "0123456789abcdef" for c in old.parent.name):
                old.unlink()
                old.parent.rmdir()
    return paths
