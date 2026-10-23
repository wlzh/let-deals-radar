"""渲染：Markdown 总览表 + 静态站点。

站点是**纯静态**产物（数据内嵌，无运行时依赖），
可直接部署到 Nginx / Cloudflare Pages / GitHub Pages。
站点元信息（标题、域名、sitemap）全部取自 config，便于绑定自有域名。
"""

from __future__ import annotations

import html
import json
import os
import shutil
from datetime import datetime, timezone

from . import config
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
    p = rec.get("flagship_price") or (
        (rec.get("prices") or [None])[0])
    if not p:
        return None
    mult = config.CYCLE_TO_YEAR.get(p.get("cycle"), 12.0)
    rate = config.FX_TO_EUR.get(p.get("currency"), 1.0)
    return round(p["amount"] * mult * rate, 2)


CYCLE_CN = {"year": "/年", "quarter": "/季", "month": "/月"}


def price_text(rec: dict) -> str:
    p = rec.get("flagship_price") or ((rec.get("prices") or [None])[0])
    if not p:
        return "—"
    return "%s%g%s" % (p["currency"], p["amount"],
                       CYCLE_CN.get(p.get("cycle"), ""))


def spec_text(rec: dict) -> str:
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
    lines = ["| 来源 | 厂商 | 活动性质 | 力度 | 配置/CPU | 旗舰价 | 状态 | 置信度 |",
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
    forum_links = _load_forum_links()
    for r in records:
        r["score"] = deal_score(r)
        r["annual_eur"] = annual_eur(r)
        # 论坛对应帖子的链接；没有则为 None（渲染时回退到原帖）
        r["forum_url"] = forum_links.get(str(r.get("thread_id")))
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
/* 详情底部的链接区：主链接（中文详情）实心、次链接（原帖）描边，
   视觉上引导用户先看论坛，同时保留核对原帖的入口。 */
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
    <span>生成于 <b>@@GENERATED@@</b></span>
    <span><a href="data/deals.json">原始 JSON</a></span>
    @@HEADER_LINKS@@
  </div>
</header>


<div class="card">
  <h3 style="margin:0 0 4px;font-size:15px">📌 推荐商家（站长自用 / 严选）</h3>
  <p style="margin:0;color:var(--muted);font-size:12.5px">以下为长期在用的商家，通过链接下单可享与官网相同的价格，部分链接含推广佣金（由商家承担，不影响你的价格）。</p>
  <div class="feat">
    <a href="https://my.racknerd.com/aff.php?aff=15809" target="_blank" rel="noopener nofollow sponsored"><b>RackNerd</b><span>主打性价比的老牌美国 VPS，常年有年付特价机。</span><span class="go">去看看 →</span></a>
    <a href="https://app.cloudcone.com/?ref=13794" target="_blank" rel="noopener nofollow sponsored"><b>CloudCone</b><span>美国 VPS，活动时年付约 70 美金，弹性升降配。</span><span class="go">去看看 →</span></a>
    <a href="https://www.dmit.io/aff.php?aff=21728" target="_blank" rel="noopener nofollow sponsored"><b>DMIT</b><span>高端线路代表，CN2 GIA / CMIN2，对国内访问友好。</span><span class="go">去看看 →</span></a>
    <a href="https://bandwagonhost.com/aff.php?aff=84775" target="_blank" rel="noopener nofollow sponsored"><b>搬瓦工 BandwagonHost</b><span>老牌商家，CN2 GIA-E 线路口碑稳定，机房可切换。</span><span class="go">去看看 →</span></a>
    <a href="https://clients.zgovps.com/?affid=1207" target="_blank" rel="noopener nofollow sponsored"><b>ZgoVPS</b><span>站长多年自用的小鸡，稳定省心。</span><span class="go">去看看 →</span></a>
    <a href="https://www.vircs.com/welcome?vcd=61a4aae4" target="_blank" rel="noopener nofollow sponsored"><b>VIRCS</b><span>家宽落地机，住宅 IP 属性，适合需要原生 IP 的场景。</span><span class="go">去看看 →</span></a>
    <a href="https://www.geelinx.com/aff/BLXJTGUH" target="_blank" rel="noopener nofollow sponsored"><b>Geelinx</b><span>到日本线路的 VPS，低延迟。</span><span class="go">去看看 →</span></a>
    <a href="https://greencloudvps.com/billing/aff.php?aff=10419" target="_blank" rel="noopener nofollow sponsored"><b>GreenCloud</b><span>老牌商家，Budget KVM 年付特价常年有货，机房多。</span><span class="go">去看看 →</span></a>
    <a href="https://lisahost.com/aff.php?aff=9424" target="_blank" rel="noopener nofollow sponsored"><b>丽萨主机 LisaHost</b><span>老牌主机商，线路与机房选择多。</span><span class="go">去看看 →</span></a>
        <a href="https://layeronecloud.com/r/KMA9RZRD/" target="_blank" rel="noopener nofollow sponsored"><b>LayerOne</b><span>性价比 KVM 小鸡，LET 促销机常年有货。</span><span class="go">去看看 →</span></a>
    <a href="https://www.voyracloud.com/?ref_code=GLGHABPA" target="_blank" rel="noopener nofollow sponsored"><b>VoyraCloud</b><span>主打住宅 IP 的云主机，原生 IP 场景适用。</span><span class="go">去看看 →</span></a>
  </div>
</div>

<div class="card">
  <div class="controls">
    <input type="search" id="q" placeholder="搜索厂商 / CPU / 优惠码 / 地点…">
    <select id="src">
      <option value="all">全部来源</option>
      <option value="let">LET</option>
      <option value="leb">LEB</option>
    </select>
    <select id="avail">
      <option value="all">全部状态</option>
      <option value="ON_SALE">在售</option>
      <option value="SOLD_OUT">售罄</option>
      <option value="RESTOCKING">补货中</option>
      <option value="PARTIAL">部分售罄</option>
      <option value="UNVERIFIED">待确认</option>
    </select>
    <select id="sort">
      <option value="score">按活动力度</option>
      <option value="new">按最新</option>
      <option value="price">按价格(低→高)</option>
      <option value="conf">按置信度</option>
      <option value="provider">按厂商</option>
    </select>
    <label class="toggle"><input type="checkbox" id="bigonly"> 只看大力度</label>
    <label class="toggle"><input type="checkbox" id="hideold"> 隐藏 30 天前</label>
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
          <th data-s="event">活动性质 <span class="ar"></span></th>
          <th data-s="score">力度 <span class="ar"></span></th>
          <th data-s="spec">配置 / CPU <span class="ar"></span></th>
          <th data-s="price">旗舰价 <span class="ar"></span></th>
          <th data-s="avail">状态 <span class="ar"></span></th>
          <th data-s="conf">置信度 <span class="ar"></span></th>
          <th class="hidesm" data-s="age">发布于 <span class="ar"></span></th>
        </tr>
      </thead>
      <tbody id="tb"></tbody>
    </table>
  </div>
  <div class="empty" id="empty" style="display:none">没有匹配的条目</div>
</div>

<footer>
  <div>本站汇总公开渠道的优惠信息，仅作信息聚合，<b>不构成购买建议</b>。</div>
  <div>@@FXNOTE@@</div>
  <div>库存与价格以厂商官网为准；标注「待确认」表示未能自动验证。</div>
  @@FOOTER@@
</footer>

</div>

<script type="application/json" id="deals-data">@@DATA@@</script>
<script>
(function(){
  var DATA = JSON.parse(document.getElementById('deals-data').textContent);
  var R = DATA.records || [];
  var EVENT_CN = @@EVENT_CN@@;
  var AVAIL_CN = @@AVAIL_CN@@;

  var st = { q:'', event:'all', src:'all', avail:'all', big:false,
             hideold:false, sort:'score', open:{} };

  function esc(s){
    return String(s==null?'':s).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }
  function specText(r){
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
      default: return (b.score||0)-(a.score||0);
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
         '<td>'+(p.price?(p.price.currency+(+p.price.amount)+'/年'):'—')+'</td></tr>';
    });
    return '<h4>套餐明细（自动解析 '+ps.length+' 条）</h4>'+
           '<table class="pt"><thead><tr><th>套餐</th><th>CPU</th><th>内存</th>'+
           '<th>存储</th><th>流量</th><th>价格</th></tr></thead><tbody>'+
           rowsHtml+'</tbody></table>';
  }
  function detail(r){
    var d=r.discount||{}, codes=d.codes||[], ex=d.extras||[];
    var h='<div class="detin">';
    h+=planRows(r);
    h+='<h4>抽取信息</h4><div class="kv">';
    h+='<span>来源 <b>'+esc((r.source||'').toUpperCase())+'</b></span>';
    h+='<span>地点 <b>'+((r.locations||[]).join(', ')||'—')+'</b></span>';
    h+='<span>活动性质 <b>'+(EVENT_CN[r.event_type]||r.event_type)+'</b></span>';
    h+='<span>置信度 <b>'+Math.round((r.confidence||0)*100)+'%</b></span>';
    h+='<span>循环折扣 <b>'+(d.recurring?'是':'否/未提及')+'</b></span>';
    if(d.percent_max) h+='<span>最大折扣 <b>'+d.percent_max+'%</b></span>';
    if(r.age_days!=null) h+='<span>发布 <b>'+ageText(r)+'</b></span>';
    if(r.author) h+='<span>发帖人 <b>'+esc(r.author)+'</b></span>';
    if(r.also_on&&r.also_on.length) h+='<span>同时出现在 <b>'+esc(r.also_on.join(', ').toUpperCase())+'</b></span>';
    h+='</div>';
    if(codes.length){
      h+='<h4 style="margin-top:12px">优惠码（需复核）</h4><div>';
      codes.forEach(function(c){ h+='<span class="tag">'+esc(c)+'</span>'; });
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
    // 链接区：优先引导到论坛的中文详情，同时保留原帖供核对。
    // 站点建站早于发帖，所以 forum_url 来自上一轮结果；没有对应
    // 帖子时只显示原帖链接，不会出现死链。
    h+='<div class="links">';
    if(r.forum_url){
      h+='<a class="lk lk-main" href="'+esc(r.forum_url)+
         '" target="_blank" rel="noopener">中文详情 &rarr;</a>';
      h+='<a class="lk lk-sub" href="'+esc(r.url)+
         '" target="_blank" rel="noopener">原帖 &rarr;</a>';
    } else {
      h+='<a class="lk lk-sub" href="'+esc(r.url)+
         '" target="_blank" rel="noopener">查看原帖 &rarr;</a>';
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
             '" target="_blank" rel="noopener nofollow" '+
             'title="访问官网 '+esc(r.vendor_url)+'">'+pv+'</a>';
      }
      out+='<td><div class="pv">'+pv+'</div></td>';
      out+='<td><span class="badge b-'+esc(r.event_type)+'">'+
           esc(EVENT_CN[r.event_type]||r.event_type)+'</span></td>';
      out+='<td>'+(esc((r.discount||{}).strength)||'<span style="color:var(--muted)">—</span>')+'</td>';
      out+='<td class="spec">'+esc(specText(r))+'</td>';
      out+='<td class="price">'+esc(priceText(r))+'</td>';
      out+='<td><span class="av av-'+esc(r.availability)+'">'+
           esc(AVAIL_CN[r.availability]||r.availability)+'</span></td>';
      out+='<td class="cf"><span class="bar"><i style="width:'+
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
    var h='<div class="chip on" data-e="all">全部<span class="n">'+R.length+'</span></div>';
    keys.forEach(function(k){
      h+='<div class="chip" data-e="'+esc(k)+'">'+esc(EVENT_CN[k]||k)+
         '<span class="n">'+cnt[k]+'</span></div>';
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
    st.avail=e.target.value; render(); });
  document.getElementById('sort').addEventListener('change',function(e){
    st.sort=e.target.value; render(); });
  document.getElementById('bigonly').addEventListener('change',function(e){
    st.big=e.target.checked; render(); });
  document.getElementById('hideold').addEventListener('change',function(e){
    st.hideold=e.target.checked; render(); });
  document.getElementById('tb').addEventListener('click',function(e){
    // 点在厂商官网链接上时，只跳转、不展开该行
    if(e.target.closest('a')) return;
    var tr=e.target.closest('tr.row'); if(!tr) return;
    var id=tr.getAttribute('data-id');
    st.open[id]=!st.open[id];
    render();
  });
  Array.prototype.forEach.call(document.querySelectorAll('th[data-s]'),function(th){
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

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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
                .replace("@@OGTAGS@@", ogtags)
                .replace("@@DATA@@", _json_for_script(payload))
                .replace("@@COUNT@@", str(payload.get("count", 0)))
                .replace("@@GENERATED@@", generated)
                .replace("@@EVENT_CN@@", json.dumps(EVENT_CN, ensure_ascii=False))
                .replace("@@AVAIL_CN@@", json.dumps(AVAIL_CN, ensure_ascii=False))
                .replace("@@FXNOTE@@", html.escape(config.FX_NOTE))
                .replace("@@HEADER_LINKS@@", _header_links_html())
                .replace("@@FOOTER@@",
                         html.escape(config.SITE_FOOTER) if config.SITE_FOOTER
                         else ""))

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
