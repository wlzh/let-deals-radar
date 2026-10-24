"""联盟推广链接（Affiliate）映射。

用途
----
在不牺牲「直达具体套餐页」的前提下带上推广参数，用户下单你拿佣金。

两种模式（按厂商配置自动选择）
------------------------------
**A. 参数追加（推荐）** —— 保留原帖的具体套餐/加购页，把推广参数拼上去：

    https://deluxhost.net/en/cart?add=storage-1&cycle=monthly
      -> https://deluxhost.net/en/cart?add=storage-1&cycle=monthly&ref=YOUR_REF

  好处：用户一步到套餐页，转化率最高；佣金凭参数归属。

**B. 全量替换（兜底）** —— 厂商只支持专属入口时，只能跳到该入口：

    https://racknerd.com/ctrl-control-panel
      -> https://my.racknerd.com/aff.php?aff=YOUR_ID

  代价：用户落在首页，要自己找套餐。

配置方式
--------
环境变量 ``LDR_AFFILIATES``（JSON），值是：

- **字符串**：完整推广链接 -> 走 B（全量替换）
- **对象**：``{"param": "ref", "value": "YOUR_REF"}`` -> 走 A（追加参数）
  可选 ``"landing"`` 指定该厂商的专属落地页

例：

    LDR_AFFILIATES='{
      "deluxhost.net": {"param": "ref", "value": "YOUR_REF"},
      "racknerd.com": "https://my.racknerd.com/aff.php?aff=YOUR_ID"
    }'

安全默认
--------
不配置就**完全不影响现有行为**（用原始链接）。
JSON 写错时静默回退空表 —— 最坏只是没有推广链接，不会中断抓取。
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 域名 -> 配置。字符串 = 完整链接（整体替换）；dict = 追加参数。
#
# 模式选择依据：**看厂商给的链接是什么形式**
#   - 独立入口（如 my.racknerd.com/aff.php?aff=）-> 只能整体替换，
#     因为原站路径不接受该参数。
#   - 原站 + 查询参数（如 deluxhost.net/?ref=）-> 可追加参数，
#     从而保留「具体套餐页」这一层，转化率更高。
DEFAULTS: dict[str, object] = {
    # 站长自己的推广配置（2026-10-23 用户提供，来自其推广清单）
    "racknerd.com": "https://my.racknerd.com/aff.php?aff=15809",
    "cloudcone.com": {"param": "ref", "value": "13794"},
    "dmit.io": "https://www.dmit.io/aff.php?aff=21728",
    "bandwagonhost.com": "https://bandwagonhost.com/aff.php?aff=84775",
    "zgovps.com": {"param": "affid", "value": "1207"},
    "vircs.com": {"param": "vcd", "value": "61a4aae4"},
    "geelinx.com": "https://www.geelinx.com/aff/BLXJTGUH",
    "lisahost.com": "https://lisahost.com/aff.php?aff=9424",
    "voyracloud.com": {"param": "ref_code", "value": "GLGHABPA"},
    "greencloudvps.com": {"param": "aff", "value": "10419"},
    "smokyhosts.com": "https://members.smokyhosts.com/aff.php?aff=70",
    "layeronecloud.com": "https://layeronecloud.com/r/KMA9RZRD/",
    "svr4u.com": "https://billing.svr4u.com/aff.php?aff=56",
    "deluxhost.net": {"param": "ref", "value": "4L64TH55"},
    "ethernetservers.com": "https://www.ethernetservers.com/aff.php?aff=3294",
    "nolimitcloud.eu": "https://panel.nolimitcloud.eu/aff.php?t=d5b7f82f9b30fda818d044fd4760126b",
    "hostdare.com": "https://bill.hostdare.com/aff.php?aff=4796",
    "linceris.com": "https://dashboard.linceris.com/aff.php?aff=28",
    "dartnode.com": {"param": "aff", "value": "LazyMonkey767"},
    "bero-host.de": "https://bero-host.de/affiliate/TGdu8zEWPF",
    "tier.net": "https://billing.tier.net/aff.php?aff=369",
    "colocrossing.com": "https://cloud.colocrossing.com/aff.php?aff=2253",
    "budgetvm.com": {"param": "affid", "value": "23706"},
    "cloudnium.net": {"param": "affid", "value": "359"},
    "ava.hosting": "https://my.ava.hosting/aff.php?aff=15927",
    "aspirehosting.net": "https://billing.aspirehosting.net/aff.php?aff=81",
    "fiberstate.com": "https://billing.fiberstate.com/aff.php?aff=523",
    "centrixnodes.net": "https://billing.centrixnodes.net/aff.php?aff=109",
    "flipperhost.com": "https://www.flipperhost.com/billing/aff.php?aff=386",
    "hostnamaste.com": "https://www.hostnamaste.com/clients/aff.php?aff=387",
    "just.hosting": {"param": "ref", "value": "382103"},
    "infrawire.net": {"param": "aff", "value": "81"},
    "hostaddon.com": "https://www.hostaddon.com/clients/aff.php?aff=47",
    "systemfreaks.com": {"param": "a", "value": "MTAwMg=="},
    "privatebyte.com": {"param": "ref", "value": "PBT4TBQY"},
    "khanwebhost.com": "https://khanwebhost.com/aff.php?aff=239",
    "metricservers.co.uk": "https://metricservers.co.uk/aff.php?aff=2",
    # 待注册补齐：deluxhost.net 及其余数据中厂商
}


def _load() -> dict[str, object]:
    """读取映射：环境变量优先，其次模块内 DEFAULTS。

    解析失败时静默回退 —— 配置写错不应导致抓取流程失败。
    """
    raw = os.getenv("LDR_AFFILIATES", "").strip()
    if not raw:
        return dict(DEFAULTS)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return dict(DEFAULTS)
    if not isinstance(data, dict):
        return dict(DEFAULTS)

    out = dict(DEFAULTS)
    for k, v in data.items():
        if not isinstance(k, str) or not k.strip():
            continue
        key = k.strip().lower()
        if isinstance(v, str) and v.startswith("http"):
            out[key] = v.strip()
        elif isinstance(v, dict) and v.get("param") and v.get("value"):
            item = {"param": str(v["param"]).strip(),
                    "value": str(v["value"]).strip()}
            landing = v.get("landing")
            if isinstance(landing, str) and landing.startswith("http"):
                item["landing"] = landing.strip()
            out[key] = item
    return out


def _domain_of(url: str) -> str:
    try:
        parts = urlsplit(url or "")
        if parts.scheme not in ("http", "https") or parts.username or parts.password:
            return ""
        return (parts.hostname or "").lower().removeprefix("www.").rstrip(".")
    except ValueError:
        return ""


def _match(url: str, table: dict[str, object]):
    """按 精确域名 -> 去子域 的顺序找配置，返回 (配置, 命中的域名)。"""
    dom = _domain_of(url)
    if not dom:
        return None, None
    if dom in table:
        return table[dom], dom
    parts = dom.split(".")
    for i in range(1, len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in table:
            return table[cand], cand
    return None, None


def append_param(url: str, param: str, value: str) -> str:
    """在 URL 上追加查询参数（保留原有参数与路径）。

    若该参数已存在，**替换其值**而不是重复追加 —— 避免出现
    ``?ref=old&ref=new`` 这类让厂商解析歧义的链接。
    """
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if k.lower() != param.lower()]
    q.append((param, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(q), parts.fragment))


# 他人推广参数黑名单。原始帖子里的厂商链接有时混着发帖人自己的
# 推广参数；站内只允许出现站长自己的推广参数，因此未命中站长配置
# 的链接在进站前先剥掉这些参数（优惠码 promocode/coupon 与产品
# 参数 pid 等购买必需参数不在此列，不动）。
_FOREIGN_AFF_PARAMS = {
    "aff", "affid", "aff_id", "affiliate", "affiliate_id", "a_aid",
    "ref", "ref_code", "refid", "referral", "referrer",
    "partner", "partner_id", "vcd",
}


def _strip_foreign(url: str, keep: str | None = None) -> str:
    """剥离 URL 里的他人推广参数；keep 指定要保留的参数名（站长自己的）。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    keep_l = keep.lower() if keep else None
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if k.lower() not in _FOREIGN_AFF_PARAMS or k.lower() == keep_l]
    if len(q) == len(parse_qsl(parts.query, keep_blank_values=True)):
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(q), parts.fragment))


def rewrite(vendor_url: str | None,
            mapping: dict[str, object] | None = None) -> str | None:
    """把厂商链接改写为带推广参数的版本；未配置则原样返回。

    优先「保留具体页面 + 追加参数」，厂商只提供专属入口时才整体替换。
    """
    if not vendor_url:
        return vendor_url
    table = _load() if mapping is None else mapping
    if not table:
        return _strip_foreign(vendor_url)

    cfg, _hit = _match(vendor_url, table)
    if cfg is None:
        # 未配置的厂商：原样保留购买链接，但剥掉他人推广参数 ——
        # 站内只允许出现站长自己的推广参数。
        return _strip_foreign(vendor_url)

    # 完整链接模式：整体替换
    if isinstance(cfg, str):
        return cfg

    # 参数追加模式：保留路径，先剥掉同名/异名的他人推广参数，再加站长的
    return append_param(_strip_foreign(vendor_url, keep=cfg["param"]),
                        cfg["param"], cfg["value"])


def summary(mapping: dict[str, object] | None = None) -> str:
    """一行人类可读摘要，便于启动日志确认。"""
    table = _load() if mapping is None else mapping
    if not table:
        return "未配置推广链接（使用原始链接）"
    parts = []
    for k in sorted(table):
        parts.append("%s(%s)" % (
            k, "追加参数" if isinstance(table[k], dict) else "整体替换"))
    return "已配置 %d 家：%s" % (len(table), "、".join(parts))
