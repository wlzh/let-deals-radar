"""渲染：Markdown 总览表 + 静态站点。

站点是**纯静态**产物（数据内嵌，无运行时依赖），
可直接部署到 Nginx / Cloudflare Pages / GitHub Pages。
站点元信息（标题、域名、sitemap）全部取自 config，便于绑定自有域名。
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
from datetime import datetime, timezone

from . import config, affiliate
from .presentation import prepare, safe_url
from .extract import AVAIL_CN, EVENT_CN

# 活动性质的「力度权重」，用于综合评分
EVENT_WEIGHT = {
    "anniversary": 25, "black_friday": 25, "holiday": 15,
    "launch": 10, "restock": 8, "sale": 5, "regular": 0,
}


# --------------------------------------------------------------------------
# 评分与格式化
# --------------------------------------------------------------------------

def deal_score(rec: dict) -> int:
    """活动力度综合评分（0-120）。分越高越「值得看」。"""
    s = 0.0
    d = rec.get("discount") or {}
    if d.get("recurring"):
        s += 30                       # 循环/永久折扣最值钱
    pm = d.get("percent_max")
    if pm:
        s += min(pm, 70) * 0.8
    s += EVENT_WEIGHT.get(rec.get("event_type"), 0)
    if d.get("extras"):
        s += 10
    if d.get("codes"):
        s += 5
    s += (rec.get("confidence") or 0) * 10
    return int(round(s))


def annual_eur(rec: dict) -> float | None:
    """年化价格（欧元近似），仅用于排序。"""
    p = rec.get("sort_price") if "sort_price" in rec else rec.get("flagship_price") or (
        (rec.get("prices") or [None])[0])
    if not p:
        return None
    mult = config.CYCLE_TO_YEAR.get(p.get("cycle"), 12.0)
    rate = config.FX_TO_EUR.get(p.get("currency"), 1.0)
    return round(p["amount"] * mult * rate, 2)


CYCLE_CN = {"year": "/年", "quarter": "/季", "month": "/月"}


def price_text(rec: dict) -> str:
    if "display_price" in rec:
        return rec["display_price"] + "（" + rec["display_price_note"] + "）"
    p = rec.get("flagship_price") or ((rec.get("prices") or [None])[0])
    if not p:
        return "—"
    return "%s%g%s" % (p["currency"], p["amount"],
                       CYCLE_CN.get(p.get("cycle"), ""))


def spec_text(rec: dict) -> str:
    if "display_spec" in rec:
        return rec["display_spec"]
    s = rec.get("specs") or {}
    bits = []
    if s.get("vcpu"):
        bits.append("%dC" % s["vcpu"])
    if s.get("ram_gb"):
        bits.append("%dG" % int(s["ram_gb"]))
    if s.get("storage_gb"):
        st = s.get("storage_type") or ""
        if st.lower() == "unspecified":
            st = ""
        bits.append("%dG%s" % (int(s["storage_gb"]), st))
    head = "/".join(bits)
    cpu = ", ".join((rec.get("cpu_models") or [])[:2])
    # 规格与 CPU 各自可能缺失：缺一个就只显示另一个，
    # 两个都缺才用占位符，避免「— | Xeon」这种残缺拼接。
    if head and cpu:
        return "%s | %s" % (head, cpu)
    return head or cpu or "—"


def to_markdown(rows: list[dict]) -> str:
    lines = ["| 来源 | 厂商 | 活动性质 | 力度 | 配置范围/套餐 | 帖文报价 | 状态 | 信息完整度 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            (r.get("source") or "").upper(),
            (r.get("provider") or "").replace("|", "/"),
            EVENT_CN.get(r.get("event_type"), r.get("event_type")),
            ((r.get("discount") or {}).get("strength") or "—").replace("|", "/"),
            spec_text(r).replace("|", "/"),
            price_text(r),
            AVAIL_CN.get(r.get("availability"), r.get("availability")),
            "%.2f" % (r.get("confidence") or 0),
        ))
    return "\n".join(lines)


def _load_forum_links() -> dict:
    """读取已发布到论坛的记录，返回 {thread_id: 论坛URL}。

    站点建站发生在发帖之前，所以这里读到的是**上一轮**的结果 ——
    新发的帖要等下一轮建站才出现在站点上（延迟一个周期）。

    文件不存在或损坏时返回空 dict：此时全部回退到原帖链接，
    不会出现死链。
    """
    path = str(config.FORUM_POSTED_LOG)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for tid, info in data.items():
        if isinstance(info, dict) and info.get("url"):
            out[str(tid)] = info["url"]
    return out


def enrich(records: list[dict]) -> list[dict]:
    """为展示补充派生字段。"""
    for r in records:
        prepare(r)
        r["score"] = deal_score(r)
        r["annual_eur"] = annual_eur(r)
    return records


# --------------------------------------------------------------------------
# 静态站
# --------------------------------------------------------------------------

def _json_for_script(obj) -> str:
    """把 JSON 安全内嵌进 <script>，防止 </script> 逃逸。"""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def _header_links_html() -> str:
    """渲染页头附加链接。未配置时返回空串（不影响布局）。"""
    links = getattr(config, "HEADER_LINKS", None) or []
    if not links:
        return ""
    parts = []
    for name, url in links:
        parts.append('<span><a href="%s" target="_blank" rel="noopener">%s</a>'
                     '</span>' % (html.escape(url, quote=True),
                                  html.escape(name)))
    return "\n    ".join(parts)


_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _merchant_links_html():
    entries = [
        ("RackNerd", "https://racknerd.com/", "查看年付活动；先确认机房与续费金额"),
        ("CloudCone", "https://app.cloudcone.com/", "比较套餐与计费周期，按实际资源需求选择"),
        ("DMIT", "https://www.dmit.io/", "关注线路与流量配额，先选机房再选套餐"),
        ("搬瓦工", "https://bandwagonhost.com/", "核对线路、机房和迁移规则"),
        ("ZgoVPS", "https://clients.zgovps.com/", "查看当前套餐、库存与续费条件"),
        ("VIRCS", "https://www.vircs.com/welcome", "核对 IP 类型、地区与使用限制"),
        ("Geelinx", "https://www.geelinx.com/", "查看可选机房，延迟以实际测试为准"),
        ("GreenCloud", "https://greencloudvps.com/billing/aff.php", "比较机房与年付方案，确认活动库存"),
        ("丽萨主机", "https://lisahost.com/", "按地区与 IP 需求筛选套餐"),
        ("LayerOne", "https://layeronecloud.com/", "查看当前 KVM 套餐与促销条件"),
        ("VoyraCloud", "https://www.voyracloud.com/", "核对 IP 属性、带宽与计费规则"),
        ("SmokyHosts", "https://members.smokyhosts.com/", "核对优惠码适用套餐与续费折扣"),
        ("SVR4U", "https://billing.svr4u.com/", "比较地区、配置和账单总额"),
        ("DeluxHost", "https://deluxhost.net/", "查看当前 VPS 与存储套餐"),
        ("EthernetServers", "https://www.ethernetservers.com/", "查看美西机房套餐与当前库存"),
        ("NoLimitCloud", "https://panel.nolimitcloud.eu/", "查看欧洲套餐与流量配额"),
        ("HostDare", "https://www.hostdare.com/", "比较洛杉矶与荷兰机房套餐"),
        ("Linceris", "https://linceris.com/", "查看欧洲机房套餐与活动"),
        ("DartNode", "https://dartnode.com/", "查看德州机房套餐与年付活动"),
        ("BERO", "https://bero-host.de/", "查看德国机房套餐与计费周期"),
        ("Tier.Net", "https://www.tier.net/", "查看美国多机房套餐与线路"),
        ("ColoCrossing", "https://www.colocrossing.com/", "查看美国机房独服与 VPS 套餐"),
        ("BudgetVM", "https://www.budgetvm.com/", "比较机房、配置与续费价格"),
        ("Cloudnium", "https://portal.cloudnium.net/", "查看当前套餐与库存状态"),
        ("Ava Hosting", "https://ava.hosting/", "查看欧洲套餐与促销条件"),
        ("Aspire Hosting", "https://aspirehosting.net/", "查看套餐配置与账单总额"),
        ("FiberState", "https://fiberstate.com/", "查看犹他机房独服与网络套餐"),
        ("CentriXNodes", "https://centrixnodes.net/", "查看当前套餐与库存状态"),
        ("FlipperHost", "https://www.flipperhost.com/", "比较共享主机、VPS 与独服套餐"),
        ("HostNamaste", "https://www.hostnamaste.com/", "查看印度与美国机房套餐"),
        ("Just.Hosting", "https://just.hosting/", "比较多地区机房与计费周期"),
        ("Infrawire", "https://infrawire.net/", "查看欧洲与土耳其机房套餐"),
        ("HostAddon", "https://www.hostaddon.com/", "查看 NVMe 套餐与当前库存"),
        ("SystemFreaks", "https://manager.systemfreaks.com/", "查看欧洲机房套餐与促销"),
        ("PrivateByte", "https://privatebyte.com/", "查看当前套餐与库存状态"),
        ("KhanWebHost", "https://khanwebhost.com/", "查看加拿大机房套餐与活动"),
        ("MetricServers", "https://metricservers.co.uk/", "查看英国机房套餐与当前库存"),
    ]
    links = []
    for name, url, desc in entries:
        target = safe_url(affiliate.rewrite(url))
        if not target:
            continue
        links.append('<a href="%s" target="_blank" rel="noopener nofollow sponsored">'
                     '<b>%s</b><span>%s</span><span class="go">查看商家活动</span></a>' %
                     (html.escape(target, quote=True), html.escape(name), html.escape(desc)))
    return "\n".join(links)


def _initial_rows(records):
    """Readable links and prices before JS loads, including without JS."""
    rows = []
    closed = {"SOLD_OUT", "ENDED", "RESTOCKING", "LIKELY_SOLD_OUT"}
    for r in records:
        if r.get("availability") in closed:
            continue
        esc = html.escape
        link = r.get("vendor_url") or r.get("url")
        action = ('<a class="purchase" href="%s" target="_blank" rel="noopener nofollow sponsored">%s</a>' %
                  (esc(link, quote=True), esc(r["purchase_label"] if r.get("vendor_url") else "查看原帖找购买入口"))) if link else ""
        cells = [("商家", esc(r.get("provider") or "") + "<br>" + action),
                 ("活动", esc(EVENT_CN.get(r.get("event_type"), ""))),
                 ("优惠", esc((r.get("discount") or {}).get("strength") or "未提及")),
                 ("配置", esc(r["display_spec"])),
                 ("报价", esc(price_text(r))),
                 ("库存", esc(AVAIL_CN.get(r.get("availability"), "待确认"))),
                 ("信息完整度", str(round((r.get("confidence") or 0) * 100)) + "%"),
                 ("发布", esc(r.get("posted") or "待确认"))]
        rows.append('<tr class="row">' + ''.join('<td data-label="%s">%s</td>' % c for c in cells) + '</tr>')
    return "\n".join(rows)


def _affiliate_shortcuts_html():
    links = []
    for name, url in (("RackNerd 活动", "https://racknerd.com/"),
                      ("DMIT 套餐", "https://www.dmit.io/"),
                      ("DeluxHost 活动", "https://deluxhost.net/")):
        target = safe_url(affiliate.rewrite(url))
        if target:
            links.append('<a class="purchase" href="%s" target="_blank" rel="noopener nofollow sponsored">%s</a>' %
                         (html.escape(target, quote=True), html.escape(name)))
    return " ".join(links)


def _footer_html() -> str:
    """渲染页脚文案（SITE_FOOTER 为站长本人在仓库变量里维护的可信配置）：

    - 值里已带 <a> 链接标记时，说明站长写的是现成 HTML，原样输出；
      若像纯文本一样转义，页面上会把整坨标记当文本显示出来。
    - 纯文本值（如「Telegram：https://t.me/xxx」）则先转义，再把裸 URL
      转成可点击链接，避免出现不可点击的裸地址。
    两种情况整体都包一层 div。"""
    raw = getattr(config, "SITE_FOOTER", "") or ""
    if not raw:
        return ""
    if "<a" in raw.lower():
        return "<div>%s</div>" % raw
    esc = html.escape(raw)
    linked = _URL_RE.sub(
        lambda m: ('<a href="%s" target="_blank" rel="noopener">%s</a>'
                   % (m.group(0), m.group(0))),
        esc)
    return "<div>%s</div>" % linked


SITE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>@@TITLE@@</title>
<meta name="description" content="@@DESC@@">
@@CANONICAL@@
@@OGTAGS@@
<!-- 站点图标：沿用论坛的 LS 品牌图标，保持视觉一致。
     ico 供浏览器标签页（多尺寸），PNG 供 Android/PWA 与 iOS 主屏。 -->
<link rel="icon" href="favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="192x192" href="icon-192.png">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#75ADFB">
<style>
:root{
  --bg:#f6f7f9; --card:#fff; --ink:#16181d; --muted:#6b7280; --line:#e5e7eb;
  --accent:#2563eb; --accent-soft:#eff6ff; --good:#059669; --warn:#d97706;
  --bad:#dc2626; --chip:#f3f4f6;
}
@media (prefers-color-scheme:dark){
  :root{ --bg:#0f1115; --card:#171a21; --ink:#e8eaed; --muted:#9aa0a6;
         --line:#272b34; --accent:#60a5fa; --accent-soft:#1e293b;
         --good:#34d399; --warn:#fbbf24; --bad:#f87171; --chip:#1f232b; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1400px;margin:0 auto;padding:24px 18px 64px}
header h1{margin:0 0 6px;font-size:24px;letter-spacing:-.3px}
header p{margin:0;color:var(--muted);font-size:13px}
.meta{margin-top:10px;display:flex;flex-wrap:wrap;gap:14px;
  color:var(--muted);font-size:12px}
.meta b{color:var(--ink);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:16px;margin-top:18px}
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
input[type=search],select{background:var(--card);color:var(--ink);
  border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:13px;
  font-family:inherit;outline:none}
input[type=search]{min-width:240px;flex:1}
input[type=search]:focus,select:focus{border-color:var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.chip{background:var(--chip);border:1px solid var(--line);border-radius:999px;
  padding:5px 12px;font-size:12.5px;cursor:pointer;user-select:none;
  color:var(--ink);white-space:nowrap}
.chip:hover{border-color:var(--accent)}
.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.chip .n{opacity:.6;margin-left:5px;font-variant-numeric:tabular-nums}
.chip.on .n{opacity:.85}
label.toggle{display:inline-flex;align-items:center;gap:6px;font-size:13px;
  color:var(--ink);cursor:pointer;white-space:nowrap}
.tblwrap{overflow-x:auto;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:10px 10px;text-align:left;border-bottom:1px solid var(--line);
  vertical-align:top}
th{position:sticky;top:0;background:var(--card);font-weight:600;font-size:12px;
  color:var(--muted);text-transform:uppercase;letter-spacing:.4px;z-index:1;
  cursor:pointer;user-select:none;white-space:nowrap}
th:hover{color:var(--accent)}
tr.row{cursor:pointer}
tr.row:hover{background:var(--accent-soft)}
tr.row.open{background:var(--accent-soft)}
.pv{font-weight:600}
/* 厂商名可点击跳官网：默认与正文同色，只用虚线下划线提示可点，
   避免整列变蓝影响扫读。无官网链接时渲染为纯文本，样式无差别。 */
a.pv-link{color:inherit;text-decoration:none;cursor:pointer;
  border-bottom:1px dotted var(--muted)}
a.pv-link:hover{color:var(--accent);border-bottom-color:var(--accent)}
.spec{font-variant-numeric:tabular-nums;white-space:nowrap}
.price{font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
/* 「发布于」与「置信度」列禁止折行：
   前者如 “20 天前” 会在数字与中文之间的空格处断开；
   后者是「进度条 + 百分比」，窄列时会被拆成两行。 */
.hidesm,td.cf{white-space:nowrap}
.badge{display:inline-block;border-radius:6px;padding:2px 7px;font-size:11.5px;
  font-weight:600;white-space:nowrap}
.b-anniversary,.b-black_friday{background:#fee2e2;color:#b91c1c}
.b-holiday{background:#fef3c7;color:#b45309}
.b-launch{background:#dbeafe;color:#1d4ed8}
.b-restock{background:#d1fae5;color:#047857}
.b-sale{background:#ede9fe;color:#6d28d9}
.b-regular{background:var(--chip);color:var(--muted)}
@media (prefers-color-scheme:dark){
  .b-anniversary,.b-black_friday{background:#450a0a;color:#fca5a5}
  .b-holiday{background:#451a03;color:#fcd34d}
  .b-launch{background:#172554;color:#93c5fd}
  .b-restock{background:#022c22;color:#6ee7b7}
  .b-sale{background:#2e1065;color:#c4b5fd}
}
.av{font-size:12px;white-space:nowrap}
.av-ON_SALE{color:var(--good);font-weight:600}
.av-SOLD_OUT{color:var(--bad);font-weight:600}
.av-ENDED{color:var(--bad)}
.av-RESTOCKING{color:var(--warn);font-weight:600}
.av-PARTIAL{color:var(--warn);font-weight:600}
.av-LIKELY_SOLD_OUT{color:var(--warn)}
.av-UNVERIFIED,.av-UNVERIFIED_STALE{color:var(--muted)}
.bar{display:inline-block;width:44px;height:6px;border-radius:3px;
  background:var(--line);vertical-align:middle;margin-right:6px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--accent)}
.det{background:var(--bg)}
.det>td{padding:0}
.detin{padding:14px 16px;border-left:3px solid var(--accent)}
.detin h4{margin:0 0 8px;font-size:12px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.4px}
.pt{width:100%;border-collapse:collapse;font-size:12.5px;margin-bottom:12px}
.pt th,.pt td{padding:5px 8px;border-bottom:1px solid var(--line)}
.pt th{position:static;background:transparent;font-size:11px}
.kv{display:flex;flex-wrap:wrap;gap:8px 20px;font-size:12.5px;color:var(--muted)}
.kv b{color:var(--ink);font-weight:600}
.tag{display:inline-block;background:var(--chip);border-radius:5px;
  padding:1px 6px;font-size:11.5px;margin-right:4px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
/* Purchase is primary; the source remains available for verification. */
.links{margin-top:12px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.lk{display:inline-block;padding:5px 12px;border-radius:6px;
  font-size:12.5px;text-decoration:none;white-space:nowrap}
.lk-main{background:var(--accent);color:#fff;font-weight:600}
.lk-main:hover{filter:brightness(1.08);text-decoration:none}
.lk-sub{color:var(--accent);border:1px solid var(--line-strong)}
.lk-sub:hover{border-color:var(--accent);text-decoration:none}
.empty{text-align:center;color:var(--muted);padding:48px 0}
footer{margin-top:28px;color:var(--muted);font-size:12px;line-height:1.9}
.note{background:var(--chip);border-left:3px solid var(--warn);
  border-radius:0 8px 8px 0;padding:10px 14px;font-size:12.5px;
  color:var(--muted);margin-top:14px}
@media(max-width:760px){
  .hidesm{display:none}
  input[type=search]{min-width:100%}
}

.feat{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px;margin-top:12px}
.feat a{display:block;background:var(--chip);border:1px solid var(--line);border-radius:10px;padding:10px 12px;color:var(--ink);text-decoration:none}
.feat a:hover{border-color:var(--accent);text-decoration:none}
.feat b{font-size:13.5px}
.feat span{display:block;color:var(--muted);font-size:12px;margin-top:2px;line-height:1.5}
.feat .go{color:var(--accent);font-size:12px;margin-top:4px}
.intro h2{font-size:clamp(20px,3vw,28px);margin:0 0 10px;line-height:1.4}
.intro p{color:var(--muted);line-height:1.8;margin:8px 0}
.quick{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}
button{font:inherit;cursor:pointer;color:var(--ink);background:var(--chip);border:1px solid var(--line);border-radius:8px;padding:9px 12px}
button:hover{border-color:var(--accent)}
button:focus-visible,a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.merchants-title{color:var(--accent);padding:8px 0;margin:0;font-size:15px}
.disclosure{font-size:12px}
.purchase{display:inline-block!important;background:var(--accent)!important;color:var(--bg)!important;font-weight:700;padding:9px 12px;border-radius:8px;text-decoration:none!important;white-space:normal;line-height:1.5}
.price small,.spec small{display:block;color:var(--muted);font-size:11px;white-space:normal;max-width:230px;font-weight:400;margin-top:5px}
.row td{vertical-align:top;overflow-wrap:anywhere}
.spec{white-space:normal;min-width:150px;max-width:240px}
.detin{overflow-wrap:anywhere}
.row .actions{display:flex;flex-direction:column;gap:8px;margin-top:10px;min-width:145px}
.row .actions button{font-size:12px;padding:6px 8px}
.intro{border-top:3px solid var(--accent)}
.aff-quick{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:12px 0}
.controls label{font-size:12px;color:var(--muted)}
#feedback{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--ink);color:var(--bg);border-radius:8px;padding:10px 16px;z-index:10}
@media(max-width:700px){
 .tblwrap{overflow:visible} .tblwrap>table>thead{display:none}
 .tblwrap>table,.tblwrap>table>tbody,.tblwrap>table>tbody>tr,.tblwrap>table>tbody>tr>td{display:block;width:auto}
 .tblwrap .row{padding:14px 0;border-bottom:1px solid var(--line)}
 .tblwrap .row td{padding:5px 12px;border:0;white-space:normal;max-width:none;min-width:0}
 .tblwrap .row td:before{content:attr(data-label);display:inline-block;color:var(--muted);font-size:11px;margin-right:10px}
 .tblwrap .row td:first-child:before{display:none}
 .tblwrap .row td.hidesm{display:none}
 .row .actions{flex-direction:row;flex-wrap:wrap;align-items:center}
 .price small,.spec small{max-width:none}
 .detin{padding:12px}.pt{display:block;overflow:auto;max-width:100%}
 .controls select{max-width:100%}.feat{grid-template-columns:1fr}
}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>@@TITLE@@</h1>
  <p>@@DESC@@</p>
  <div class="meta">
    <span>数据来源 · 公开优惠信息</span>
    <span>共 <b id="m-total">@@COUNT@@</b> 条</span>
    <span>数据更新 <b>@@GENERATED@@</b></span>
    @@HEADER_LINKS@@
  </div>
</header>


<section class="card intro">
  <p id="stale" hidden>数据更新已超过 48 小时，活动和库存可能变化，请务必到商家复核。</p>
  <h2>少翻帖子，找到合适的 VPS 优惠。</h2>
  <p>对比预算、机房与续费条件，复制优惠码，直达商家活动页。先选适合自己的，再看实际到手价。</p>
  <div class="aff-quick"><b>直达商家活动</b> @@AFF_SHORTCUTS@@</div>
  <div class="quick"><button type="button" data-preset="budget">找低预算年付</button><button type="button" data-preset="new">看最近发布</button><button type="button" data-preset="sale">看标注在售</button><button type="button" id="reset">重置筛选</button></div>
  <div class="merchants"><h3 class="merchants-title">已有心仪商家</h3><div class="feat">@@MERCHANTS@@</div></div>
  <p class="disclosure">部分链接为推广链接，符合商家规则的订单可能为本站带来佣金；价格、续费与优惠资格以结算页为准。</p>
</section>

<div class="card">
  <div class="controls">
    <input type="search" id="q" aria-label="搜索优惠" placeholder="搜商家、地区、CPU 或优惠码">
    <select id="src" aria-label="数据来源">
      <option value="all">全部来源</option>
      <option value="let">LET</option>
      <option value="leb">LEB</option>
    </select>
    <select id="avail" aria-label="库存状态">
      <option value="all">全部状态</option>
      <option value="ON_SALE">在售</option>
      <option value="SOLD_OUT">售罄</option>
      <option value="RESTOCKING">补货中</option>
      <option value="PARTIAL">部分售罄</option>
      <option value="UNVERIFIED">待确认</option>
    </select>
    <select id="sort" aria-label="排序方式">
      <option value="score">优先看近期可选优惠</option>
      <option value="new">按最新</option>
      <option value="price">按价格(低→高)</option>
      <option value="conf">按信息完整度</option>
      <option value="provider">按厂商</option>
    </select>
    <label class="toggle"><input type="checkbox" id="bigonly"> 只看大力度</label>
    <label class="toggle"><input type="checkbox" id="hideold"> 隐藏 30 天前</label>
    <label class="toggle"><input type="checkbox" id="hideclosed" checked> 隐藏售罄 / 补货中</label>
    <label>年付预算 <select id="budget" aria-label="年付预算"><option value="0">不限</option><option value="25">约 €25 内</option><option value="50">约 €50 内</option><option value="100">约 €100 内</option></select></label>
    <select id="location" aria-label="机房地区"><option value="">全部地区</option></select>
  </div>
  <div class="chips" id="chips"></div>
  <div class="note">
    <b>关于「库存」</b>：优先采信<b>商家本人在回帖中的表态</b>（点开任意一行看证据原文）；
    无商家表态时退回帖文关键词；仍无法判定则标「待确认」。
    <b>「待确认」不等于有货</b>，下单前请点进原帖或到厂商官网核实。
  </div>
</div>

<div class="card">
  <div class="tblwrap">
    <table>
      <thead>
        <tr>
          <th data-s="provider">厂商 <span class="ar"></span></th>
          <th>活动性质</th>
          <th data-s="score">力度 <span class="ar"></span></th>
          <th>套餐 / 配置范围</th>
          <th data-s="price">报价与周期 <span class="ar"></span></th>
          <th>状态</th>
          <th data-s="conf" title="字段齐全程度，不代表真实性或库存保证">信息完整度 <span class="ar"></span></th>
          <th class="hidesm" data-s="age">发布于 <span class="ar"></span></th>
        </tr>
      </thead>
      <tbody id="tb">@@ROWS@@</tbody>
    </table>
  </div>
  <div class="empty" id="empty" style="display:none">暂时没有匹配的优惠。试试放宽预算、切换地区，或点击上方“重置筛选”。</div>
</div>

<footer>
  <div><b>下单前核对：</b>首期总价、续费金额、税费、机房、IPv4、流量和退款条件。优惠码复制后请确认结算页确实生效。</div>
  <div>信息完整度只表示字段覆盖，不是质量评分。配置范围来自帖文，未绑定套餐的报价不能与最高配置组合理解。</div>
  <div>本站汇总公开渠道的优惠信息，仅作信息聚合，<b>不构成购买建议</b>。</div>
  <div>@@FXNOTE@@</div>
  <div>库存与价格以厂商官网为准；标注「待确认」表示未能自动验证。</div>
  @@FOOTER@@
  <div><span id="busuanzi_container_site_uv">访客数 <span id="busuanzi_value_site_uv"></span> 人</span> | <span id="busuanzi_container_site_pv">访问量 <span id="busuanzi_value_site_pv"></span> 次</span></div>
</footer>
<script async src="//busuanzi.ibruce.info/busuanzi/2.3/busuanzi.pure.mini.js"></script>

</div>

<script type="application/json" id="deals-data">@@DATA@@</script>
<div id="feedback" role="status" hidden></div>
<script>
(function(){
  var DATA = JSON.parse(document.getElementById('deals-data').textContent);
  var R = DATA.records || [];
  var updated=Date.parse(DATA.generated_at);
  if(!Number.isFinite(updated) || Date.now()-updated>48*60*60*1000) document.getElementById('stale').hidden=false;
  var EVENT_CN = @@EVENT_CN@@;
  var AVAIL_CN = @@AVAIL_CN@@;

  var st = { q:'', event:'all', src:'all', avail:'all', big:false,
             hideold:false, hideclosed:true, budget:0, location:'', sort:'score', open:{} };

  function esc(s){
    return String(s==null?'':s).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }
  function specText(r){
    if(r.display_spec) return r.display_spec;
    var s=r.specs||{}, b=[];
    if(s.vcpu) b.push(s.vcpu+'C');
    if(s.ram_gb) b.push(Math.round(s.ram_gb)+'G');
    if(s.storage_gb){
      var t=(s.storage_type||'').toLowerCase()==='unspecified'?'':(s.storage_type||'');
      b.push(Math.round(s.storage_gb)+'G'+t);
    }
    var cpu=(r.cpu_models||[]).slice(0,2).join(', ');
    var head=b.join('/');
    // 规格与 CPU 各自可能缺失：缺一个就只显示另一个，
    // 两个都缺才用占位符，避免出现「— | Xeon」这种残缺拼接。
    if(head&&cpu) return head+' | '+cpu;
    if(head) return head;
    if(cpu) return cpu;
    return '—';
  }
  function priceText(r){
    if(r.display_price) return r.display_price;
    var p=r.flagship_price||((r.prices||[])[0]);
    if(!p) return '—';
    var c={year:'/年',quarter:'/季',month:'/月'}[p.cycle]||'';
    return p.currency+(+p.amount)+c;
  }
  function ageText(r){
    if(r.age_days==null) return '—';
    if(r.age_days===0) return '今天';
    return r.age_days+' 天前';
  }
  function bigDeal(r){
    var d=r.discount||{};
    return !!(d.recurring || (d.percent_max&&d.percent_max>=30)
              || r.event_type==='anniversary' || r.event_type==='black_friday');
  }
  function passes(r){
    if(st.hideclosed && ['SOLD_OUT','ENDED','RESTOCKING','LIKELY_SOLD_OUT'].indexOf(r.availability)>=0) return false;
    if(st.budget && (!r.sort_price || r.sort_price.cycle!=='year' || r.annual_eur==null || r.annual_eur>st.budget)) return false;
    if(st.location && (r.locations||[]).indexOf(st.location)<0) return false;
    if(st.event!=='all' && r.event_type!==st.event) return false;
    if(st.src!=='all' && r.source!==st.src) return false;
    if(st.avail!=='all'){
      if(st.avail==='UNVERIFIED'){
        if(r.availability!=='UNVERIFIED' && r.availability!=='UNVERIFIED_STALE') return false;
      } else if(r.availability!==st.avail) return false;
    }
    if(st.big && !bigDeal(r)) return false;
    if(st.hideold && r.age_days!=null && r.age_days>30) return false;
    if(st.q){
      var hay=[r.provider,r.title,(r.cpu_models||[]).join(' '),
               (r.locations||[]).join(' '),
               ((r.discount||{}).codes||[]).join(' '),
               ((r.discount||{}).strength||''),
               (r.specs_all?JSON.stringify(r.specs_all):'')]
              .join(' ').toLowerCase();
      if(hay.indexOf(st.q.toLowerCase())<0) return false;
    }
    return true;
  }
  function cmp(a,b){
    switch(st.sort){
      case 'new': return (a.age_days==null?1e9:a.age_days)-(b.age_days==null?1e9:b.age_days);
      case 'price': {
        var x=a.annual_eur==null?1e12:a.annual_eur,
            y=b.annual_eur==null?1e12:b.annual_eur;
        return x-y;
      }
      case 'conf': return (b.confidence||0)-(a.confidence||0);
      case 'provider': return String(a.provider).localeCompare(String(b.provider));
      case 'age': return (a.age_days==null?1e9:a.age_days)-(b.age_days==null?1e9:b.age_days);
      default: {
        function rank(r){
          if(['SOLD_OUT','ENDED','RESTOCKING','LIKELY_SOLD_OUT'].indexOf(r.availability)>=0) return 3;
          if(r.age_days==null || r.age_days>30) return 2;
          return r.availability==='ON_SALE'?0:1;
        }
        return rank(a)-rank(b) || (b.score||0)-(a.score||0) || (a.age_days||0)-(b.age_days||0);
      }
    }
  }
  function planRows(r){
    var ps=r.plans||[];
    if(!ps.length) return '';
    var firstKey={}, group=0, rowsHtml='';
    ps.forEach(function(p){
      var key=(p.vcpu||'')+'/'+(p.ram_gb||'')+'/'+(p.storage_gb||'')+'/'+
              ((p.price&&p.price.amount)||'');
      if(firstKey[p.name]===undefined){ firstKey[p.name]=key; }
      else if(firstKey[p.name]!==key){ group++; firstKey[p.name]=key; }
      var label=esc(p.name);
      if(group>0) label+=' <span style="opacity:.5;font-size:11px">#'+(group+1)+'</span>';
      rowsHtml+='<tr><td>'+label+'</td>'+
         '<td>'+(p.vcpu?p.vcpu+'C':'—')+'</td>'+
         '<td>'+(p.ram_gb?Math.round(p.ram_gb)+'G':'—')+'</td>'+
         '<td>'+(p.storage_gb?Math.round(p.storage_gb)+'G'+
              ((p.storage_type&&p.storage_type!=='unspecified')?' '+esc(p.storage_type):''):'—')+'</td>'+
         '<td>'+(p.bandwidth_tb?p.bandwidth_tb+'TB':'—')+'</td>'+
         '<td>'+(p.price?(esc(p.price.currency)+(+p.price.amount)+({year:'/年',month:'/月',quarter:'/季'}[p.price.cycle]||'（周期待核对）')):'—')+'</td></tr>';
    });
    return '<h4>套餐明细（自动解析 '+ps.length+' 条）</h4>'+
           '<table class="pt"><thead><tr><th>套餐</th><th>CPU</th><th>内存</th>'+
           '<th>存储</th><th>流量</th><th>价格</th></tr></thead><tbody>'+
           rowsHtml+'</tbody></table>';
  }
  function detail(r){
    var d=r.discount||{}, codes=d.codes||[], ex=d.extras||[];
    var h='<div class="detin">';
    h+='<h4>'+esc(r.title)+'</h4>';
    h+='<p>先核对首期与续费总价、机房和流量，再决定是否下单。以下为自动抽取，最终以商家结算页为准。</p>';
    h+=planRows(r);
    h+='<h4>抽取信息</h4><div class="kv">';
    h+='<span>来源 <b>'+esc((r.source||'').toUpperCase())+'</b></span>';
    h+='<span>地点 <b>'+esc((r.locations||[]).join(', ')||'—')+'</b></span>';
    h+='<span>活动性质 <b>'+(EVENT_CN[r.event_type]||r.event_type)+'</b></span>';
    h+='<span>信息完整度 <b>'+Math.round((r.confidence||0)*100)+'%</b>（非质量评分）</span>';
    h+='<span>循环折扣 <b>'+(d.recurring?'是':'否/未提及')+'</b></span>';
    if(d.percent_max) h+='<span>最大折扣 <b>'+d.percent_max+'%</b></span>';
    if(r.age_days!=null) h+='<span>发布 <b>'+ageText(r)+'</b></span>';
    if(r.author) h+='<span>发帖人 <b>'+esc(r.author)+'</b></span>';
    if(r.also_on&&r.also_on.length) h+='<span>同时出现在 <b>'+esc(r.also_on.join(', ').toUpperCase())+'</b></span>';
    h+='</div>';
    if(codes.length){
      h+='<h4 style="margin-top:12px">优惠码（需复核）</h4><div>';
      codes.forEach(function(c){ h+='<button type="button" data-copy="'+esc(c)+'">复制 '+esc(c)+'</button> '; });
      h+='</div>';
    }
    if(ex.length){
      h+='<h4 style="margin-top:12px">加赠</h4><div>';
      ex.forEach(function(c){ h+='<span class="tag">'+esc(c)+'</span>'; });
      h+='</div>';
    }
    if(r.stock_hints&&r.stock_hints.length){
      h+='<h4 style="margin-top:12px">帖文库存关键词</h4><div>';
      r.stock_hints.forEach(function(c){ h+='<span class="tag">'+esc(c)+'</span>'; });
      h+='</div>';
    }
    var rp=r.replies;
    if(rp){
      h+='<h4 style="margin-top:12px">回帖证据（库存判定来源）</h4>';
      h+='<div class="kv" style="margin-bottom:6px">';
      h+='<span>回帖总数 <b>'+(rp.reply_count||0)+'</b></span>';
      h+='<span>商家回复 <b>'+(rp.vendor_reply_count||0)+'</b></span>';
      h+='<span>判定来源 <b>'+(rp.source==='vendor'?'商家表态':rp.source==='user'?'用户提及(弱证据)':'无')+'</b></span>';
      h+='</div>';
      var ev=rp.evidence;
      if(ev){
        h+='<div style="background:var(--chip);border-radius:8px;padding:8px 10px;font-size:12.5px;margin-bottom:8px">';
        h+='<div style="color:var(--muted);margin-bottom:3px">'+
           esc(ev.author||'?')+(ev.is_vendor?'（商家）':'（用户）')+
           (ev.datetime?(' · '+esc(String(ev.datetime).slice(0,10))):'')+'</div>';
        h+='<div>'+esc(ev.snippet||'')+'</div></div>';
      }
      if(rp.vendor_signals&&rp.vendor_signals.length){
        h+='<div style="font-size:12px;color:var(--muted);line-height:1.9">';
        rp.vendor_signals.forEach(function(s){
          // 状态码翻成中文；后面的日期与引文保留原文 ——
          // 那是商家回帖的原始证据，翻译会使其失去证据效力。
          h+='<div><span class="tag">'+
             esc(AVAIL_CN[s.status]||s.status)+'</span> '+
             esc(String(s.datetime||'').slice(0,10))+' — '+
             esc((s.snippet||'').slice(0,110))+'</div>';
        });
        h+='</div>';
      }
    }
    h+='<div class="links">';
    if(r.vendor_url){
      if(r.order_url){
        h+='<a class="lk lk-main purchase" href="'+esc(r.order_url)+'" target="_blank" rel="noopener nofollow sponsored">下单</a>';
        h+='<a class="lk lk-sub" href="'+esc(r.vendor_url)+'" target="_blank" rel="noopener nofollow sponsored">注册</a>';
      }else{
        h+='<a class="lk lk-main purchase" href="'+esc(r.vendor_url)+'" target="_blank" rel="noopener nofollow sponsored">'+esc(r.purchase_label)+'</a>';
      }
    }
    if(r.url){
      h+='<a class="lk lk-sub" href="'+esc(r.url)+
         '" target="_blank" rel="noopener">查看原帖与完整条款</a>';
    }
    h+='</div>';
    return h+'</div>';
  }
  function render(){
    var rows=R.filter(passes).sort(cmp);
    var tb=document.getElementById('tb');
    var out='';
    rows.forEach(function(r){
      var open=!!st.open[r.thread_id];
      out+='<tr class="row'+(open?' open':'')+'" data-id="'+esc(r.thread_id)+'">';
      // 厂商名：有推断出官网就做成外链，否则纯文本
      var pv = esc(r.provider);
      if(r.vendor_url){
        pv = '<a class="pv-link" href="'+esc(r.vendor_url)+
             '" target="_blank" rel="noopener nofollow sponsored" '+
             'title="访问官网 '+esc(r.vendor_url)+'">'+pv+'</a>';
      }
      out+='<td><div class="pv">'+pv+'</div><div class="actions">';
      if(r.vendor_url){
        if(r.order_url) out+='<a class="purchase" href="'+esc(r.order_url)+'" target="_blank" rel="noopener nofollow sponsored">下单</a> <a class="purchase" href="'+esc(r.vendor_url)+'" target="_blank" rel="noopener nofollow sponsored">注册</a>';
        else out+='<a class="purchase" href="'+esc(r.vendor_url)+'" target="_blank" rel="noopener nofollow sponsored">'+esc(r.purchase_label)+'</a>';
      }
      else if(r.url) out+='<a href="'+esc(r.url)+'" target="_blank" rel="noopener">查看原帖找购买入口</a>';
      out+='<button type="button" aria-expanded="'+open+'">'+(open?'收起详情':'核对套餐与优惠码')+'</button></div></td>';
      out+='<td data-label="活动"><span class="badge b-'+esc(r.event_type)+'">'+
           esc(EVENT_CN[r.event_type]||r.event_type)+'</span></td>';
      out+='<td data-label="优惠">'+(esc((r.discount||{}).strength)||'<span style="color:var(--muted)">未提及优惠码</span>')+'</td>';
      out+='<td data-label="配置" class="spec">'+esc(specText(r))+'<small>'+esc((r.locations||[]).join(' / '))+'</small></td>';
      out+='<td data-label="报价" class="price">'+esc(priceText(r))+'<small>'+esc(r.display_price_note)+'</small></td>';
      out+='<td data-label="库存"><span class="av av-'+esc(r.availability)+'">'+
           esc(AVAIL_CN[r.availability]||r.availability)+'</span></td>';
      out+='<td data-label="信息完整度" class="cf"><span class="bar"><i style="width:'+
           Math.round((r.confidence||0)*100)+'%"></i></span>'+
           Math.round((r.confidence||0)*100)+'%</td>';
      out+='<td class="hidesm">'+esc(ageText(r))+'</td>';
      out+='</tr>';
      if(open) out+='<tr class="det"><td colspan="8">'+detail(r)+'</td></tr>';
    });
    tb.innerHTML=out;
    document.getElementById('empty').style.display=rows.length?'none':'block';
    document.getElementById('m-total').textContent=rows.length+' / '+R.length;
  }
  function buildChips(){
    var cnt={};
    R.forEach(function(r){ cnt[r.event_type]=(cnt[r.event_type]||0)+1; });
    var keys=Object.keys(cnt).sort(function(a,b){ return cnt[b]-cnt[a]; });
    var h='<button type="button" class="chip on" data-e="all">全部<span class="n">'+R.length+'</span></button>';
    keys.forEach(function(k){
      h+='<button type="button" class="chip" data-e="'+esc(k)+'">'+esc(EVENT_CN[k]||k)+
         '<span class="n">'+cnt[k]+'</span></button>';
    });
    var box=document.getElementById('chips');
    box.innerHTML=h;
    box.addEventListener('click',function(e){
      var c=e.target.closest('.chip'); if(!c) return;
      st.event=c.getAttribute('data-e');
      Array.prototype.forEach.call(box.querySelectorAll('.chip'),function(x){
        x.classList.toggle('on',x===c);
      });
      render();
    });
  }
  document.getElementById('q').addEventListener('input',function(e){
    st.q=e.target.value.trim(); render(); });
  document.getElementById('src').addEventListener('change',function(e){
    st.src=e.target.value; render(); });
  document.getElementById('avail').addEventListener('change',function(e){
    st.avail=e.target.value;
    if(st.avail!=='all'){st.hideclosed=false;document.getElementById('hideclosed').checked=false;}
    render(); });
  document.getElementById('sort').addEventListener('change',function(e){
    st.sort=e.target.value; render(); });
  document.getElementById('bigonly').addEventListener('change',function(e){
    st.big=e.target.checked; render(); });
  document.getElementById('hideold').addEventListener('change',function(e){
    st.hideold=e.target.checked; render(); });
  document.getElementById('hideclosed').addEventListener('change',function(e){st.hideclosed=e.target.checked;render();});
  document.getElementById('budget').addEventListener('change',function(e){st.budget=Number(e.target.value);render();});
  document.getElementById('location').addEventListener('change',function(e){st.location=e.target.value;render();});
  var locations=[];
  R.forEach(function(r){(r.locations||[]).forEach(function(l){if(locations.indexOf(l)<0) locations.push(l);});});
  locations.sort().forEach(function(l){var o=document.createElement('option');o.value=l;o.textContent=l;document.getElementById('location').appendChild(o);});
  function reset(){
    st={q:'',event:'all',src:'all',avail:'all',big:false,hideold:false,hideclosed:true,budget:0,location:'',sort:'score',open:{}};
    ['q','location'].forEach(function(id){document.getElementById(id).value='';});
    ['src','avail'].forEach(function(id){document.getElementById(id).value='all';});
    ['bigonly','hideold'].forEach(function(id){document.getElementById(id).checked=false;});
    document.getElementById('hideclosed').checked=true;
    document.getElementById('budget').value='0';document.getElementById('sort').value='score';
    document.querySelectorAll('.chip').forEach(function(c){c.classList.toggle('on',c.getAttribute('data-e')==='all');});
  }
  document.getElementById('reset').addEventListener('click',function(){reset();render();});
  document.querySelectorAll('[data-preset]').forEach(function(b){b.addEventListener('click',function(){
    reset();var p=b.getAttribute('data-preset');
    if(p==='budget'){st.budget=25;st.sort='price';document.getElementById('budget').value='25';}
    if(p==='new'){st.hideold=true;st.sort='new';document.getElementById('hideold').checked=true;}
    if(p==='sale'){st.avail='ON_SALE';document.getElementById('avail').value='ON_SALE';}
    document.getElementById('sort').value=st.sort;render();
  });});
  var feedbackTimer;
  document.addEventListener('click',async function(e){
    var b=e.target.closest('[data-copy]');if(!b)return;
    var message;
    try{await navigator.clipboard.writeText(b.getAttribute('data-copy'));message='优惠码已复制，请在结算页确认折扣生效';}
    catch(err){message='未能自动复制，请手动复制：'+b.getAttribute('data-copy');}
    var feedback=document.getElementById('feedback');feedback.textContent=message;feedback.hidden=false;
    clearTimeout(feedbackTimer);feedbackTimer=setTimeout(function(){feedback.hidden=true;},5000);
  });
  document.getElementById('tb').addEventListener('click',function(e){
    // 点在厂商官网链接上时，只跳转、不展开该行
    if(e.target.closest('a')) return;
    if(e.target.closest('[data-copy]')) return;
    var tr=e.target.closest('tr.row'); if(!tr) return;
    var id=tr.getAttribute('data-id');
    st.open[id]=!st.open[id];
    render();
  });
  Array.prototype.forEach.call(document.querySelectorAll('th[data-s]'),function(th){
    th.tabIndex=0;th.setAttribute('role','button');
    th.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();th.click();}});
    th.addEventListener('click',function(){
      st.sort=th.getAttribute('data-s');
      document.getElementById('sort').value=st.sort;
      render();
    });
  });
  buildChips();
  render();
})();
</script>
</body>
</html>
"""


def build_site(payload: dict, out_dir=None, verbose: bool = True) -> str:
    """把 payload 渲染成静态站，返回输出目录。"""
    out_dir = str(out_dir or config.SITE_DIR)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "data"), exist_ok=True)

    enrich(payload.get("records", []))

    # API 端点
    api_path = os.path.join(out_dir, "data", "deals.json")
    with open(api_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    generated = str(payload.get("generated_at") or "未知，请到商家复核")
    canonical = ""
    ogtags = ""
    if config.SITE_URL:
        canonical = f'<link rel="canonical" href="{config.SITE_URL}/">'
        _t = html.escape(config.SITE_TITLE)
        _d = html.escape(config.SITE_DESC)
        _img = f"{config.SITE_URL}/og-image.png"
        ogtags = "\n".join([
            '<meta property="og:type" content="website">',
            f'<meta property="og:site_name" content="{_t}">',
            f'<meta property="og:title" content="{_t}">',
            f'<meta property="og:description" content="{_d}">',
            f'<meta property="og:url" content="{config.SITE_URL}/">',
            f'<meta property="og:image" content="{_img}">',
            '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">',
            '<meta name="twitter:card" content="summary_large_image">',
            f'<meta name="twitter:title" content="{_t}">',
            f'<meta name="twitter:description" content="{_d}">',
            f'<meta name="twitter:image" content="{_img}">',
        ])

    html_out = (SITE_TEMPLATE
                .replace("@@TITLE@@", html.escape(config.SITE_TITLE))
                .replace("@@DESC@@", html.escape(config.SITE_DESC, quote=True))
                .replace("@@CANONICAL@@", canonical)
                .replace("@@MERCHANTS@@", _merchant_links_html())
                .replace("@@AFF_SHORTCUTS@@", _affiliate_shortcuts_html())
                .replace("@@ROWS@@", _initial_rows(payload.get("records", [])))
                .replace("@@OGTAGS@@", ogtags)
                .replace("@@DATA@@", _json_for_script(payload))
                .replace("@@COUNT@@", str(payload.get("count", 0)))
                .replace("@@GENERATED@@", generated)
                .replace("@@EVENT_CN@@", json.dumps(EVENT_CN, ensure_ascii=False))
                .replace("@@AVAIL_CN@@", json.dumps(AVAIL_CN, ensure_ascii=False))
                .replace("@@FXNOTE@@", html.escape(config.FX_NOTE))
                .replace("@@HEADER_LINKS@@", _header_links_html())
                .replace("@@FOOTER@@", _footer_html()))

    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    # 部署辅助文件
    with open(os.path.join(out_dir, ".nojekyll"), "w", encoding="utf-8") as f:
        f.write("")

    robots = ["User-agent: *", "Allow: /"]
    if config.SITE_URL:
        robots += ["", f"Sitemap: {config.SITE_URL}/sitemap.xml"]
    with open(os.path.join(out_dir, "robots.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(robots) + "\n")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # 未配置域名时用相对路径，避免生成无效的绝对地址
    loc = f"{config.SITE_URL}/" if config.SITE_URL else "/"
    with open(os.path.join(out_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                f'  <url><loc>{loc}</loc><lastmod>{today}</lastmod></url>\n'
                '</urlset>\n')

    # 站点图标：从 assets/ 复制到输出目录。
    # 图标是二进制资源，不适合内联进模板；放 assets/ 便于替换品牌素材。
    _copy_icons(out_dir, verbose)

    if verbose:
        print(f"站点已生成 -> {os.path.abspath(out_dir)}")
        print("  index.html      %8.1f KB" % (os.path.getsize(index_path) / 1024))
        print("  data/deals.json %8.1f KB" % (os.path.getsize(api_path) / 1024))
    return out_dir


# 需要随站点发布的图标文件
ICON_FILES = ("favicon.ico", "icon-192.png", "icon-512.png",
              "apple-touch-icon.png", "og-image.png")


def _copy_icons(out_dir: str, verbose: bool = True) -> None:
    """把 assets/ 下的图标复制到站点目录，并生成 webmanifest。

    缺失图标时只告警不报错 —— 不应因为图标问题导致建站失败。
    """
    src_dir = config.ASSETS_DIR
    copied = 0
    for name in ICON_FILES:
        src = os.path.join(str(src_dir), name)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(out_dir, name))
            copied += 1
        elif verbose:
            print("  [warn] 缺少图标 assets/%s（运行 tools/make_icons.py 生成）"
                  % name)

    # webmanifest：让 Android/PWA 也能用上图标
    name = (config.SITE_TITLE or "Deals").strip()
    short = name[:12]
    icons = []
    for fn, size in (("icon-192.png", "192x192"),
                     ("icon-512.png", "512x512")):
        if os.path.exists(os.path.join(out_dir, fn)):
            icons.append({"src": fn, "sizes": size, "type": "image/png",
                          "purpose": "any"})
    manifest = {
        "name": name,
        "short_name": short,
        "icons": icons,
        "theme_color": "#75ADFB",
        "background_color": "#ffffff",
        "display": "standalone",
        "start_url": "./",
    }
    with open(os.path.join(out_dir, "manifest.webmanifest"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    if verbose and copied:
        print("  图标            %d 个" % copied)
