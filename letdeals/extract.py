"""字段抽取：规格、价格、折扣、活动性质、地点。

全部为确定性正则（无 LLM），便于测试与复现。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from . import affiliate, config
from .textutil import parse_flat_tables, strip_html, to_float, to_gb, to_tb

# --------------------------------------------------------------------------
# 正则：规格
# --------------------------------------------------------------------------

RE_VCPU = re.compile(
    r"(\d+)\s*(?:x\s*)?(?:vCore|vCores|vCPU|vCPUs|vcore|vcpu)\b", re.I)
RE_CORE = re.compile(
    r"(\d+)\s*(?:x\s*)?(?:Cores?|Kerne|核心)\b", re.I)

RE_RAM = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(GB|MB|TB)\s*(?:of\s+)?(?:DDR[45]\s*)?(?:ECC\s*)?"
    r"(?:RAM|Memory|Arbeitsspeicher)\b", re.I)
RE_RAM_LABEL = re.compile(
    r"\bRAM\s*[:：]\s*(\d+(?:[.,]\d+)?)\s*(GB|MB|TB)", re.I)

RE_STORAGE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(GB|TB)\s*"
    r"(NVMe(?:\s*(?:Gen\s*\d|SSD))?|SSD|HDD|SATA|Storage)\b", re.I)

RE_BW = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(TB|GB)\s*(?:of\s*)?(?:Monthly\s*)?"
    r"(?:Traffic|Bandwidth|BW|transfer|outbound)\b", re.I)
RE_BW_AT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(TB|GB)\s*@", re.I)

# CPU 型号（保守：只认常见服务器 CPU 家族）
RE_CPU_MODEL = re.compile(
    r"\b("
    r"EPYC\s+[0-9]{3,4}[A-Za-z]{0,3}"
    r"|Ryzen\s+(?:9|7|5|3)\s+[0-9]{3,4}[A-Za-z0-9]{0,4}"
    r"|Threadripper\s+[0-9A-Za-z]{3,8}(?:\s+Pro)?"
    r"|Xeon\s+(?:Gold|Silver|Bronze|Platinum|E5-|E-)[0-9A-Za-z]{2,6}"
    r")\b", re.I)


# --------------------------------------------------------------------------
# 正则：价格
# --------------------------------------------------------------------------

CYCLE = (r"(mo|mos|month|months|monthly|yr|yrs|year|years|yearly|"
         r"annual|annually|quarter|quarterly|qtr)")

RE_PRICE_PRE = re.compile(
    r"([$€£])\s*(\d+(?:[.,]\d+)?)\s*(?:USD|EUR|GBP)?\s*(?:/|per\s+)\s*"
    + CYCLE, re.I)
RE_PRICE_POST = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*([$€£]|USD|EUR|GBP)\s*(?:/|per\s+)\s*" + CYCLE, re.I)

CYCLE_NORM = {
    "mo": "month", "mos": "month", "month": "month", "months": "month",
    "monthly": "month",
    "yr": "year", "yrs": "year", "year": "year", "years": "year",
    "yearly": "year", "annual": "year", "annually": "year",
    "quarter": "quarter", "quarterly": "quarter", "qtr": "quarter",
}

CURRENCY_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£"}


# --------------------------------------------------------------------------
# 正则：折扣 / 优惠码
# --------------------------------------------------------------------------

RE_PCT = re.compile(
    r"(\d{1,3})\s*%\s*(?:off|discount|rabatt|reduction|savings?)\b"
    r"|\b(?:off|discount|save|savings?\s+of)\s+(\d{1,3})\s*%", re.I)
RE_RECURRING = re.compile(
    r"\brecurring\b|\blifetime\b|\bfor\s+life\b|\bpermanent(?:ly)?\b|"
    r"\bprice\s+locked\b|\b循环\b|\b永久\b|\b续费同价\b", re.I)
RE_CODE = re.compile(
    r"(?:coupon|promo(?:tion)?\s*code|code|gutschein)\b[^A-Za-z0-9]{0,25}"
    r"([A-Z][A-Z0-9]{2,25})", re.I)
RE_CODE_BARE = re.compile(
    r"\b(LET[A-Z0-9]{2,20}|BF[A-Z0-9]{2,20}|LEB[A-Z0-9]{2,20})\b")

# 误报黑名单：这些「大写词」不是优惠码
CODE_STOP = {
    "HTTPS", "HTTP", "WORKS", "GOOD", "VALIDITY", "APPLIES", "AUTOMATICALLY",
    "DOES", "NOT", "SIGNED", "IS", "ARE", "WILL", "CAN", "WITH", "ONLY",
    "EXCLUSIONS", "HAS", "HAVE", "CONNECT", "PLEASE", "ENTER", "THIS", "THAT",
    "CODE", "CODES", "DISCOUNT", "REQUIRED", "NEEDED", "APPLICABLE",
    "AVAILABLE", "EXPIRES", "PROMO", "COUPON", "GUTSCHEIN", "FREE",
    "PLAN", "PLANS", "VPS", "SSD", "NVME", "HDD", "RAM", "CPU", "USD",
    "EUR", "GBP", "IPV4", "IPV6", "DDR4", "DDR5", "AMD", "INTEL", "XEON",
    "EPYC", "RYZEN", "WIN10", "WIN11", "WIN2019", "WIN2022", "LET", "GET",
    "USE", "THE", "AND", "FOR", "ALL", "NEW", "OFF", "ONE", "TWO", "ADD",
    "MONTH", "YEAR", "SAVE", "EXTRA", "BONUS", "DEAL", "SALE", "ORDER",
}

RE_STOCK = re.compile(
    r"\b(out\s+of\s+stock|sold\s*out|restock(?:ed|ing)?|back\s+in\s+stock|"
    r"in\s+stock|no\s+further\s+restocks|limited\s+stock|while\s+stock\s+lasts|"
    r"售罄|缺货|补货|有货)\b", re.I)

RE_EXTRAS = re.compile(
    r"(double\s+(?:ram|bandwidth|storage|traffic|ssd|nvme|cores?|vcpu)|"
    r"triple\s+\w+|free\s+(?:permanent\s+)?upgrade|"
    r"\d+\s+months?\s+free|"
    r"12\s+months?\s+for\s+the\s+price\s+of\s+10|"
    r"免费(?:升级|加倍))", re.I)


# --------------------------------------------------------------------------
# 活动性质
# --------------------------------------------------------------------------

EVENT_RULES = [
    ("anniversary", re.compile(
        r"anniversary|birthday|\b\d+\s*(?:th|st|nd|rd)\s+(?:years?|birthday)|"
        r"years?\s+in\s+business|周年|庆典|jubil", re.I)),
    ("black_friday", re.compile(
        r"black\s*friday|\bBF20\d\d\b|cyber\s*monday|黑五", re.I)),
    ("holiday", re.compile(
        r"christmas|halloween|easter|national\s*day|mid-?autumn|new\s*year|"
        r"advent|summer\s*sale|end\s*of\s*summer|freedom\s*sale|festival|"
        r"中秋|国庆|圣诞|春节", re.I)),
    ("launch", re.compile(
        r"\blaunch|is\s+back|returns?\b|new\s+location|first\s+(?:deal|vps)|"
        r"now\s+live|debut|上新|开业", re.I)),
    ("restock", re.compile(r"restock|back\s+in\s+stock|补货", re.I)),
    ("sale", re.compile(
        r"\bsale\b|\bdeal\b|\bpromo|\bdiscount|flash\s*sale|clearance|budget|"
        r"special\s+offer|特价|促销|优惠", re.I)),
]

EVENT_CN = {
    "anniversary": "周年庆", "black_friday": "黑五", "holiday": "节日促销",
    "launch": "上新/回归", "restock": "补货", "sale": "常规促销",
    "regular": "常规帖",
}

AVAIL_CN = {
    "NOT_OBSERVED": "本轮未检出（非确认停售）",
    "ON_SALE": "在售", "SOLD_OUT": "售罄", "ENDED": "活动结束",
    "RESTOCKING": "补货中", "PARTIAL": "部分售罄",
    "LIKELY_SOLD_OUT": "疑似售罄",
    "UNVERIFIED": "待确认", "UNVERIFIED_STALE": "待确认(较旧)",
}


# --------------------------------------------------------------------------
# 地点
# --------------------------------------------------------------------------

LOCATIONS = [
    "Frankfurt", "Amsterdam", "Netherlands", "Germany", "Deutschland",
    "London", "Paris", "France", "Singapore", "Tokyo", "Japan",
    "Hong Kong", "Los Angeles", "New York", "Miami", "Dallas", "Chicago",
    "Seattle", "Montreal", "Canada", "Poland", "Warsaw", "Wrocław",
    "Zurich", "Switzerland", "Velbert", "Eygelshoven", "Roubaix",
    "Salt Lake City", "Buffalo", "Ashburn", "Atlanta", "Phoenix",
    "Arizona", "Ohio", "Texas", "Utah", "Milan", "Italy", "Spain",
    "Sweden", "Norway", "Finland", "Denmark", "Austria", "Vienna",
    "Prague", "Czechia", "Estonia", "Latvia", "Lithuania", "Romania",
    "Bulgaria", "India", "Australia", "Sydney", "Brazil", "Sao Paulo",
    "Kansas", "Denver", "Detroit", "Silicon Valley", "Santa Clara",
]
RE_LOC = re.compile(
    r"\b(" + "|".join(re.escape(x) for x in LOCATIONS) + r")\b", re.I)

# 域名回退时排除的通用/第三方站点
DOMAIN_SKIP = {
    "lowendtalk.com", "lowendbox.com", "github.com", "youtube.com",
    "discord.gg", "discord.com", "twitter.com", "x.com", "twitch.tv",
    "google.com", "t.me", "telegram.org", "cloudflare.com", "imgur.com",
    "i.imgur.com", "pastebin.com", "instatus.com", "trustpilot.com",
    "statuspage.io", "facebook.com", "instagram.com", "reddit.com",
}

# 从文本里识别商家品牌的句式：
#   "from Velohost" / "by RackNerd" / "Velohost Returns" / "Offer from X"
RE_BRAND_IN_TEXT = re.compile(
    r"\b(?:from|by|via|at)\s+([A-Z][A-Za-z0-9\-\.]{2,28})\b"
    r"|^([A-Z][A-Za-z0-9\-\.]{2,28})\s+(?:Returns?|is\s+Back|Back|"
    r"Launches|Offers|Deals|Sale|Anniversary)\b", re.M)


# --------------------------------------------------------------------------
# 抽取函数
# --------------------------------------------------------------------------

def extract_provider(title: str, author: str, body: str,
                     source: str = "let") -> str:
    """从标题取商家名。

    优先用分隔符切出的首段；但很多帖子标题是描述性句子
    （如 "Free Ryzen 5900XT Server | From $99/mo"），首段不是商家名。
    此时回退到正文里的域名，最后才用作者名。

    source="leb" 时**不能**回退到作者名 —— LEB 是博客，
    作者是编辑（如 raindog308）而非商家，回退会让所有条目同名。
    """
    # Explicit brand prefixes survive geographic/title decorations.
    branded = re.sub(r"^\[(?:MY/SG/US|US|EU|UK)\]\s*", "", title, flags=re.I)
    for name in ("LayerOne", "SVR4U"):
        if re.match(r"^\[?" + name + r"\b", branded, re.I):
            return name
    head = re.split(r"\s*[|│｜–—]\s*|\s+-\s+", title, maxsplit=1)[0].strip()
    head = re.sub(r"^[\[\【(（][^\])）]{0,30}[\])）]\s*", "", head).strip()
    head = re.sub(r"\s*[:\-]\s*$", "", head)

    # **仅对 LEB** 裁掉常见句式后缀（「X Returns With ...」「X: ...」）。
    # LET 的标题首段往往就是商家名，套用此规则会把
    # 「BERO HOST 10th Anniversary Deals」误裁成「BERO HOST 10th Anniversary」，
    # 从而丢掉更精确的域名回退结果。
    if source == "leb":
        head = re.sub(
            r"\s+\b(?:returns?|is\s+back|back|relaunch(?:es)?|deals?|sale|"
            r"offer|promo(?:tion)?|launch(?:es)?)\b.*$", "",
            head, flags=re.I).strip()
        head = re.sub(r"\s*[:\-–—]\s*$", "", head).strip()

    looks_descriptive = bool(
        re.search(r"[$€£]\s*\d", head)
        or re.search(r"\b(from|starting|for only|deals?|sale|off|free)\b",
                     head, re.I)
        or len(head.split()) > 6
        or re.search(r"\d+\s*(?:GB|TB|Cores?|vCPU|RAM)", head, re.I)
    )
    if 2 <= len(head) <= 60 and not looks_descriptive:
        return head

    # 回退 1：正文中的厂商域名（只取顶级域完整的）
    for m in re.finditer(
            r"https?://(?:www\.)?([a-z0-9][a-z0-9\-]*\.[a-z.]{2,10})",
            body, re.I):
        dom = m.group(1).lower().rstrip(".")
        if dom in DOMAIN_SKIP or dom.endswith((".lowendtalk.com",
                                               ".lowendbox.com")):
            continue
        if re.fullmatch(r"[a-z0-9\-]+\.[a-z]{2,6}", dom):
            return dom

    # 回退 1b：允许带子域，裁掉常见前缀
    for m in re.finditer(
            r"https?://(?:www\.)?([a-z0-9][a-z0-9\-\.]*\.[a-z]{2,6})",
            body, re.I):
        dom = m.group(1).lower().rstrip(".")
        if dom in DOMAIN_SKIP or dom.endswith((".lowendtalk.com",
                                               ".lowendbox.com")):
            continue
        parts = dom.split(".")
        return ".".join(parts[-2:]) if len(parts) > 2 else dom

    # 回退 2：LEB 的正文/标题里常直接写商家名（如 "from Velohost"）。
    # 作者是编辑，不能当商家名，因此优先从文本里找已知品牌词
    if source == "leb":
        m = RE_BRAND_IN_TEXT.search(title + "\n" + body[:600])
        if m:
            return (m.group(1) or m.group(2)).strip()
        # 再退一步：用裁剩的标题首段
        return head[:40] if head else "UNKNOWN"

    return author or "UNKNOWN"


# --------------------------------------------------------------------------
# 厂商官网推断
# --------------------------------------------------------------------------

# 这些域名出现在帖子里也不代表厂商官网：跑分站、图床、短链、社交、
# 统计、建站依赖等。宁可少给链接，也不要指向错误的外链。
VENDOR_SKIP = DOMAIN_SKIP | {
    "bit.ly", "tinyurl.com", "t.co", "linktr.ee", "gravatar.com",
    "wordpress.org", "w.org", "gstatic.com", "googleapis.com",
    "jsdelivr.net", "unpkg.com", "cdnjs.com", "bootstrapcdn.com",
    "fontawesome.com", "creativecommons.org", "wikipedia.org",
    "archive.org", "speedtest.net", "whatsmydns.net", "bgp.tools",
    "bgp.he.net", "ipinfo.io", "ip.sb", "check.place", "hetrixtools.com",
    "uptimerobot.com", "letsencrypt.org", "nginx.org",
    "docker.com", "apache.org", "mysql.com", "redis.io", "php.net",
    "ubuntu.com", "debian.org", "centos.org", "almalinux.org",
    "rockylinux.org", "windows.com", "microsoft.com", "vmware.com",
    "proxmox.com", "whmcs.com", "clientexec.com", "blesta.com",
    "virtfusion.com", "solusvm.com", "virtualizor.com",
    # 跑分/评测站：帖子里常贴成绩链接，很容易被误判为厂商
    "geekbench.com", "cpubenchmark.net", "passmark.com", "userbenchmark.com",
    "browser.geekbench.com", "diskbenchmark.com", "fio.readthedocs.io",
    # 网络工具站
    "ping.pe", "check-host.net", "ipip.net", "17ce.com", "boce.com",
}

# 常见的二级后缀：可注册域要取三段，否则 something.co.uk 会被切成 co.uk
MULTI_SUFFIX = {
    "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk",
    "com.au", "net.au", "org.au", "co.nz", "net.nz", "org.nz",
    "co.jp", "ne.jp", "or.jp", "com.cn", "net.cn", "org.cn", "gov.cn",
    "com.br", "com.mx", "com.ar", "com.co", "com.pe", "com.ve",
    "com.tw", "com.hk", "com.sg", "com.my", "com.ph", "com.vn",
    "com.tr", "com.ua", "com.ru", "co.kr", "co.in", "co.id", "co.th",
    "co.za", "co.il", "com.sa", "com.eg", "com.ng", "com.pk",
}

# 厂商名本身就是域名时的形态（如 bero-host.de / Royal-Server.Ro）
RE_IS_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9\-]*(\.[a-z0-9\-]+)+$", re.I)

# 技术性主机名，不是厂商官网：lg=looking glass、speedtest、status 等
TECH_PREFIX = {
    "lg", "speedtest", "test", "status", "panel", "cp", "cpanel", "whm",
    "ns1", "ns2", "ns3", "dns", "mail", "smtp", "imap", "pop",
    "api", "admin", "billing", "client", "clients", "portal", "support",
    "help", "docs", "wiki", "blog", "forum", "community", "cdn", "static",
    "mirror", "download", "downloads", "node", "nodes", "vps", "host",
}


def _key(s: str) -> str:
    """归一化用于相似度比较：只留小写字母数字。"""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _registrable(dom: str) -> str:
    """取可注册域，正确处理 co.uk 这类多段后缀。"""
    parts = dom.lower().strip(".").split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_SUFFIX:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) > 2 else dom.lower()


def _plausible_domain(dom: str) -> bool:
    """粗筛：形如 label.tld，且 TLD 长度合理。

    注意 TLD 上限不能按老式 6 字符设：.hosting/.solutions/.technology
    等新顶级域更长，卡 6 会把 Ava.hosting 这类厂商判为非法。
    """
    if not re.fullmatch(r"[a-z0-9\-]+(\.[a-z0-9\-]+)+", dom):
        return False
    tld = dom.rsplit(".", 1)[-1]
    if not re.fullmatch(r"[a-z]{2,24}", tld):
        return False
    # 明显的技术前缀不作为厂商域（lg.svr、status.xxx 等）
    first = dom.split(".", 1)[0]
    if first in TECH_PREFIX and dom.count(".") == 1:
        return False
    return True


def extract_vendor_url(provider: str, body: str) -> str | None:
    """尽力推断厂商的**活动/购买页**，不确定时退回首页。

    优先级（从具体到笼统）：
      1) 正文里与厂商域名匹配、且路径像「活动/购买页」的链接
      2) 正文里与厂商域名匹配的任意深层链接
      3) 厂商名本身就是域名           -> https://<provider>
      4) 正文域名与厂商名匹配         -> https://<domain>

    为什么优先深链接：原帖正文常直接给活动页或购买页，
    例如 linceris.com/deals、deluxhost.net/en/cart?add=...。
    只给首页会让用户还要自己找活动，体验差。

    但**必须挑准**：正文里也夹杂 lg-xxx（测速）、status-xxx（状态页）、
    twitch.tv（直播）等非购买链接，取第一个会错。
    """
    p = (provider or "").strip()
    if not p:
        return None

    body = body or ""
    pk = _key(p)
    if pk == "layerone":
        pk = "layeronecloud"

    # 收集正文外链，按「是否匹配厂商域名」筛选
    cands: list[tuple[str, str]] = []      # (完整 URL, 域名)
    for m in re.finditer(r'href=["\'](https?://[^"\']+)["\']', body, re.I):
        url = m.group(1).replace("&amp;", "&").strip()
        dm = re.match(r"https?://(?:www\.)?([a-z0-9][a-z0-9\-\.]*\.[a-z]{2,24})",
                      url, re.I)
        if not dm:
            continue
        dom = _registrable(dm.group(1))
        if not _plausible_domain(dom) or dom in VENDOR_SKIP:
            continue
        if dom.endswith((".lowendtalk.com", ".lowendbox.com")):
            continue
        cands.append((url, dom))

    # 与厂商名匹配的候选
    matched: list[str] = []
    if len(pk) >= 4:
        for url, dom in cands:
            dk = _key(dom.rsplit(".", 1)[0])
            if len(dk) >= 4 and (dk == pk or dk in pk or pk in dk):
                if url not in matched:
                    matched.append(url)

    # 1) 优先「像活动/购买页」的深链接
    for url in matched:
        if _looks_like_offer_page(url):
            return url
    # 2) 次选：深层链接，但必须排除辅助页与技术页
    #    （第 2 档比第 1 档宽松，容易被 /docs/、/terms、测速文件混进来，
    #      实测遇到 ravnix.gg/terms、servury.com/docs/...、mtl-lg.servury.com/100MB.bin）
    for url in matched:
        if _path_depth(url) > 0 and _is_usable_deep_link(url):
            return url
    # 3) 厂商名本身就是域名 -> 首页
    if RE_IS_DOMAIN.match(p) and _plausible_domain(p.lower()):
        dom = _registrable(p)
        if _plausible_domain(dom) and dom not in VENDOR_SKIP:
            return "https://" + dom
    # 4) 匹配到的首页（仅根路径）
    for url in matched:
        if _path_depth(url) == 0:
            return url

    return None


# 路径里出现这些词，说明是活动/购买/套餐页，而不是首页或关于页
OFFER_HINTS = (
    "deal", "deals", "sale", "sales", "promo", "offer", "offers",
    "special", "discount", "pricing", "price", "plans", "plan",
    "order", "cart", "store", "buy", "checkout", "shop",
    "vps", "kvm", "server", "servers", "hosting", "cloud",
)

# 这些路径段说明是辅助页面，不是购买入口
NON_OFFER_HINTS = (
    "legal", "tos", "privacy", "sla", "about", "contact", "blog",
    "status", "support", "help", "docs", "wiki", "faq", "terms",
    "policy", "career", "careers", "login", "signin", "register",
    "signup", "logout", "account", "profile",
    # 合规/条款类（实测踩到 ravnix.gg/aup）
    "aup", "acceptable-use", "refund", "dmca", "abuse", "gdpr",
    "cookie", "disclaimer", "imprint",
)


def _path_depth(url: str) -> int:
    """URL 的路径深度：首页返回 0，/deals 返回 1。"""
    m = re.match(r"https?://[^/]+(/[^?#]*)?", url)
    if not m:
        return 0
    return len([x for x in (m.group(1) or "").strip("/").split("/") if x])


def _looks_like_offer_page(url: str) -> bool:
    """判断 URL 是否指向活动/购买/套餐页。

    要求：有路径，且路径中含 offer 类关键词，且不含 legal/status 等
    辅助页关键词。宁可退回首页，也不要把「隐私政策」当购买入口。
    """
    if _path_depth(url) == 0:
        return False
    low = url.lower()
    if any(h in low for h in NON_OFFER_HINTS):
        return False
    return any(h in low for h in OFFER_HINTS)


# 明显不是页面入口的：二进制文件、测速文件
JUNK_EXT = (".bin", ".zip", ".gz", ".tar", ".iso", ".img", ".txt",
            ".exe", ".dmg", ".deb", ".rpm", ".test", ".dat")

# 技术/辅助子域：测速、状态、文档、CDN
JUNK_SUBDOMAIN = ("lg-", "lg.", "speedtest", "test.", "status", "status.",
                  "docs", "docs.", "wiki", "wiki.", "cdn", "cdn.",
                  "static", "static.", "mirror", "mirror.")


def _is_usable_deep_link(url: str) -> bool:
    """深链接是否可用作「购买/活动」入口。

    排除三类实测踩到的干扰项：
      1) 二进制/测速文件   —— mtl-lg.servury.com/100MB.bin
      2) 技术子域         —— lg-fra.servitro.com、status.servitro.com
      3) 辅助页关键词     —— /terms、/docs/、/legal
    """
    low = url.lower()

    # 路径是二进制文件
    path = re.sub(r"^https?://[^/]+", "", low).split("?")[0]
    if path.endswith(JUNK_EXT):
        return False

    # 技术子域
    host = re.sub(r"^https?://", "", low).split("/")[0]
    if any(host.startswith(s) or ("." + s) in host for s in JUNK_SUBDOMAIN):
        return False

    # 辅助页关键词
    if any(h in low for h in NON_OFFER_HINTS):
        return False

    return True




def extract_specs(text: str) -> dict:
    vcpu = [int(m) for m in RE_VCPU.findall(text)]
    if not vcpu:
        vcpu = [int(m) for m in RE_CORE.findall(text)]

    ram = [to_gb(to_float(a), u) for a, u in RE_RAM.findall(text)]
    ram += [to_gb(to_float(a), u) for a, u in RE_RAM_LABEL.findall(text)]

    storage = []
    for a, u, kind in RE_STORAGE.findall(text):
        kind_n = re.sub(r"\s+", " ", kind).strip()
        if kind_n.lower() == "storage":
            kind_n = "unspecified"
        storage.append({"gb": to_gb(to_float(a), u), "type": kind_n})

    bw = [to_tb(to_float(a), u) for a, u in RE_BW.findall(text)]
    bw += [to_tb(to_float(a), u) for a, u in RE_BW_AT.findall(text)]

    def top(vals):
        return max(vals) if vals else None

    storage_sorted = sorted(storage, key=lambda x: x["gb"], reverse=True)
    flagship = storage_sorted[0] if storage_sorted else None

    return {
        "vcpu": top(vcpu),
        "ram_gb": top(ram),
        "storage_gb": flagship["gb"] if flagship else None,
        "storage_type": flagship["type"] if flagship else None,
        "bandwidth_tb": top(bw),
        "_all": {
            "vcpu": sorted(set(vcpu), reverse=True)[:8],
            "ram_gb": sorted(set(ram), reverse=True)[:8],
            "storage_gb": [s["gb"] for s in storage_sorted][:8],
        },
    }


def extract_prices(text: str) -> list[dict]:
    found = []
    for cur, amt, cyc in RE_PRICE_PRE.findall(text):
        found.append({"amount": to_float(amt), "currency": cur,
                      "cycle": CYCLE_NORM.get(cyc.lower(), cyc.lower())})
    for amt, cur, cyc in RE_PRICE_POST.findall(text):
        c = cur if cur in "$€£" else CURRENCY_SYMBOL.get(cur.upper(), cur)
        found.append({"amount": to_float(amt), "currency": c,
                      "cycle": CYCLE_NORM.get(cyc.lower(), cyc.lower())})

    mult = {"month": 12, "quarter": 4, "year": 1}
    seen, uniq = set(), []
    for p in found:
        key = (p["amount"], p["currency"], p["cycle"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    uniq.sort(key=lambda p: p["amount"] * mult.get(p["cycle"], 12))
    return uniq


def extract_discount(text: str) -> dict:
    pcts = []
    for a, b in RE_PCT.findall(text):
        v = a or b
        if v and 0 < int(v) <= 95:
            pcts.append(int(v))
    recurring = bool(RE_RECURRING.search(text))
    introductory = bool(re.search(r"\bfirst[ -]+(?:month|year|billing|payment|invoice|term)\b|\binitial[ -]+(?:term|payment)\b|首[月年期]", text, re.I))
    rule_conflict = introductory and recurring
    if introductory:
        recurring = False

    codes = set()
    for m in RE_CODE.findall(text):
        u = m.upper()
        if u not in CODE_STOP and not u.isdigit() and (m.isupper() or any(c.isdigit() for c in m)):
            codes.add(m)
    for m in RE_CODE_BARE.findall(text):
        u = m.upper()
        if u not in CODE_STOP:
            codes.add(u)

    extras = sorted({m.group(0).strip().lower()
                     for m in RE_EXTRAS.finditer(text)})

    parts = []
    if introductory:
        parts.append("首期优惠，续费待核对" if not rule_conflict else "含首期与循环描述，适用规则待核对")
    if recurring:
        parts.append("循环/永久")
    if pcts:
        parts.append(f"最高 {max(pcts)}% off")
    if extras:
        parts.append("+".join(extras[:2]))
    if codes:
        parts.append("码 " + "/".join(sorted(codes)[:3]))

    return {
        "strength": " · ".join(parts) if parts else "",
        "percent_max": max(pcts) if pcts else None,
        "recurring": recurring,
        "introductory": introductory,
        "rule_conflict": rule_conflict,
        "codes": sorted(codes)[:6],
        "extras": extras[:5],
    }


def extract_event(title: str, body: str) -> str:
    hay = title + "\n" + body[:1500]
    for name, rx in EVENT_RULES:
        if rx.search(hay):
            return name
    return "regular"


def extract_locations(text: str) -> list[str]:
    seen, out = set(), []
    for m in RE_LOC.finditer(text):
        v = m.group(1)
        k = v.lower()
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out[:5]


def extract_cpu_models(text: str) -> list[str]:
    return sorted({re.sub(r"\s+", " ", m).strip()
                   for m in RE_CPU_MODEL.findall(text)})[:5]


def extract_stock_hints(text: str) -> list[str]:
    return sorted({m.group(0).strip() for m in RE_STOCK.finditer(text)})


def age_days(pub: str) -> int | None:
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except Exception:
        return None


def age_status(days: int | None) -> str:
    if days is None:
        return "UNKNOWN"
    if days < config.NEW_DAYS:
        return "NEW"
    if days <= config.ACTIVE_DAYS:
        return "ACTIVE"
    return "STALE"


def availability(stock_hints: list[str], days: int | None,
                 reply_status: str | None = None,
                 reply_source: str | None = None) -> str:
    """可用性判定。

    优先级：**商家回帖表态 > 帖文关键词 > 用户回帖(弱证据) > 帖子年龄**。
    回帖是库存的权威来源 —— 商家只在回帖里说「售罄 / 补货」。
    """
    if reply_source == "vendor" and reply_status:
        return reply_status if reply_status in (
            "SOLD_OUT", "RESTOCKING", "ENDED", "ON_SALE", "PARTIAL") \
            else "UNVERIFIED"

    blob = " ".join(stock_hints).lower()
    if re.search(r"sold\s*out|out\s+of\s+stock|售罄|缺货", blob):
        return "SOLD_OUT"
    if re.search(r"no\s+further\s+restocks", blob):
        return "ENDED"
    if re.search(r"restock|back\s+in\s+stock|in\s+stock|补货|有货", blob):
        return "ON_SALE"

    if reply_source == "user" and reply_status == "SOLD_OUT":
        return "LIKELY_SOLD_OUT"

    if days is not None and days > config.ACTIVE_DAYS:
        return "UNVERIFIED_STALE"
    return "UNVERIFIED"


# --------------------------------------------------------------------------
# 记录构建
# --------------------------------------------------------------------------

KEY_FIELDS = ("vcpu", "ram_gb", "storage_gb", "prices", "cpu_models", "discount")


def build_record(item: dict, reply_info: dict | None = None) -> dict:
    """把一个 RSS item 变成结构化记录。"""
    raw_body = item.get("description", "") or ""
    body = strip_html(raw_body)
    text = item.get("title", "") + "\n" + body

    specs = extract_specs(text)
    plans = parse_flat_tables(body)
    prices = extract_prices(text)
    discount = extract_discount(text)
    cpu_models = extract_cpu_models(text)
    locs = extract_locations(item.get("title", "") + "\n" + body[:800])
    stock = extract_stock_hints(text)
    days = age_days(item.get("pubDate", ""))

    # 表格解析成功时，用表格里的旗舰套餐覆盖/补齐 specs（更准确）
    flagship_price = None
    if plans:
        top_plan = max(plans, key=lambda p: (p["ram_gb"] or 0, p["vcpu"] or 0))
        if top_plan["vcpu"]:
            specs["vcpu"] = top_plan["vcpu"]
        if top_plan["ram_gb"]:
            specs["ram_gb"] = top_plan["ram_gb"]
        if top_plan["storage_gb"]:
            specs["storage_gb"] = top_plan["storage_gb"]
            specs["storage_type"] = top_plan["storage_type"]
        if top_plan["bandwidth_tb"]:
            specs["bandwidth_tb"] = top_plan["bandwidth_tb"]
        tp = top_plan.get("price")
        if tp and tp.get("cycle"):
            flagship_price = {"amount": tp["amount"],
                              "currency": tp["currency"], "cycle": tp["cycle"]}
            prices = [flagship_price] + [
                p for p in prices
                if not (abs(p["amount"] - tp["amount"]) < 1e-9
                        and p["currency"] == tp["currency"])
            ]
        specs["_all"] = {
            "vcpu": sorted({p["vcpu"] for p in plans if p["vcpu"]},
                           reverse=True),
            "ram_gb": sorted({p["ram_gb"] for p in plans if p["ram_gb"]},
                             reverse=True),
            "storage_gb": sorted({p["storage_gb"] for p in plans
                                  if p["storage_gb"]}, reverse=True),
        }

    filled = sum([
        specs["vcpu"] is not None,
        specs["ram_gb"] is not None,
        specs["storage_gb"] is not None,
        bool(prices),
        bool(cpu_models),
        bool(discount["strength"]),
    ])
    confidence = round(filled / len(KEY_FIELDS), 2)

    reply_status = reply_source = None
    reply_block = None
    if reply_info and reply_info.get("ok"):
        reply_status = reply_info.get("reply_status")
        reply_source = reply_info.get("reply_source")
        reply_block = {
            "reply_count": reply_info.get("reply_count"),
            "vendor_reply_count": reply_info.get("vendor_reply_count"),
            "status": reply_status,
            "source": reply_source,
            "evidence": reply_info.get("reply_evidence"),
            "vendor_signals": reply_info.get("vendor_signals", []),
            "fetched_at": reply_info.get("fetched_at"),
        }

    provider = extract_provider(item.get("title", ""),
                                item.get("author", ""), body,
                                item.get("source", "let"))

    # 厂商链接：优先取原帖里的活动/购买页；若该厂商配置了推广链接
    # （letdeals/affiliate.py），则替换成你的推广链接。
    vendor_url = extract_vendor_url(provider, raw_body)
    if not vendor_url:
        candidates = {}
        for url in re.findall(r'href=["\'](https?://[^"\']+)["\']', raw_body, re.I):
            domain = affiliate._domain_of(url)
            domain = _registrable(domain) if domain else ""
            if domain and domain not in VENDOR_SKIP and _looks_like_offer_page(url) and _is_usable_deep_link(url):
                candidates.setdefault(domain, url.replace("&amp;", "&"))
        # Do not guess between several merchants mentioned in the same post.
        if len(candidates) == 1:
            provider, vendor_url = next(iter(candidates.items()))
    _origin_url = vendor_url
    vendor_url = affiliate.rewrite(vendor_url)
    # 整体替换型厂商：推广链接只能落在专属入口，会丢掉原帖的具体
    # 套餐/下单页。把原始深链单独保留为 order_url，页面据此渲染
    # 「下单」（直达套餐页）+「注册」（推广入口，先注册再下单，
    # 订单照样归属推广）两个按钮；参数追加型不丢链接，不需要。
    _order_url = None
    if _origin_url and vendor_url and vendor_url != _origin_url:
        _cfg, _hit = affiliate._match(_origin_url, affiliate._load())
        if isinstance(_cfg, str):
            _p = urlsplit(_origin_url)
            if (_p.path or "").strip("/") or _p.query:
                _order_url = _origin_url

    return {
        "thread_id": str(item.get("thread_id", "")),
        "source": item.get("source", "let"),
        "provider": provider,
        "title": item.get("title", ""),
        "url": item.get("link", ""),
        # 必须用**原始 HTML** 抽取：strip_html 会把 <a href> 一并剥掉，
        # 而厂商链接恰恰只存在于 href 里。
        "vendor_url": vendor_url,
        "order_url": _order_url,
        "author": item.get("author", ""),
        "posted": item.get("pubDate", ""),
        "age_days": days,
        "age_status": age_status(days),
        "availability": availability(stock, days, reply_status, reply_source),
        "replies": reply_block,
        "event_type": extract_event(item.get("title", ""), body),
        "discount": discount,
        "specs": {k: v for k, v in specs.items() if not k.startswith("_")},
        "specs_all": specs["_all"],
        "plans": plans[:40],
        "flagship_price": flagship_price,
        "cpu_models": cpu_models,
        "prices": prices[:4],
        "locations": locs,
        "stock_hints": stock,
        "confidence": confidence,
    }
