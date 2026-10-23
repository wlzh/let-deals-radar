"""文本工具：HTML 清洗、单位换算、拍平表格重建。

这三个是抽取层的地基，也是最容易出错的地方，
因此单独成模块并配单元测试。
"""

from __future__ import annotations

import html
import re

# --------------------------------------------------------------------------
# HTML 清洗
# --------------------------------------------------------------------------

RE_TAG = re.compile(r"<[^>]+>")
RE_BQ_OPEN = re.compile(r"<blockquote\b", re.I)
RE_BQ_CLOSE = re.compile(r"</blockquote\s*>", re.I)
# 把 HTML 切成「标签 / 文本」token 流
RE_TOKEN = re.compile(r"<[^>]+>|[^<]+", re.S)


def strip_quotes_depth_aware(fragment: str) -> str:
    """剥离任意深度的嵌套 <blockquote>，只保留深度 0 的正文。

    LET 的引用是**多层嵌套**的（一条回复可引用「A 引用 B 引用 C」）。
    简单的非贪婪正则 ``<blockquote.*?</blockquote>`` 会从最内层开始剥，
    反而把外层被引用的**别人的话**留在结果里 —— 导致把用户的话
    当成商家表态。因此必须按嵌套深度过滤。
    """
    out: list[str] = []
    depth = 0
    for tok in RE_TOKEN.findall(fragment):
        if tok.startswith("<"):
            if RE_BQ_OPEN.match(tok):
                depth += 1
            elif RE_BQ_CLOSE.match(tok):
                depth = max(0, depth - 1)
            elif depth == 0 and re.match(
                    r"</(p|div|li|h[1-6]|br)\s*/?>", tok, re.I):
                out.append("\n")
            continue
        if depth == 0:
            out.append(tok)
    return "".join(out)


def strip_html(s: str, *, drop_quotes: bool = False) -> str:
    """HTML -> 纯文本。

    drop_quotes=True 时同时剔除（含嵌套的）引用块，
    用于回帖正文，避免引用内容污染判定。
    """
    if not s:
        return ""
    if drop_quotes:
        s = strip_quotes_depth_aware(s)
    s = re.sub(r"<script.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<style.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|div|li|tr|h[1-6]|td|th)>", "\n", s, flags=re.I)
    s = RE_TAG.sub(" ", s)
    s = html.unescape(s)
    s = s.replace("\u00a0", " ").replace("\u200b", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


# 有些商家直接把 Markdown 写进帖子标题，例如
# "**25G Unmetered** 30% lifetime discount"。
RE_MD_PAIRED = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|~~(.+?)~~", re.S)
RE_MD_LEFTOVER = re.compile(r"\*{2,}|_{2,}|~{2,}")


def strip_md_markers(s: str) -> str:
    """去掉 Markdown 强调标记，保留其中的文字。

    只处理**成对**标记（**x** / __x__ / ~~x~~）以及没有配对的裸标记。
    刻意不动单个 * 或 _ —— 它们可能是正常内容（型号、下划线、通配符）。
    """
    if not s:
        return ""
    out = RE_MD_PAIRED.sub(
        lambda m: m.group(1) or m.group(2) or m.group(3) or "", s)
    out = RE_MD_LEFTOVER.sub("", out)
    out = re.sub(r"[ \t]+", " ", out)
    return out.strip()


# --------------------------------------------------------------------------
# 单位换算
# --------------------------------------------------------------------------

def to_float(s: str) -> float:
    """'1,5' -> 1.5（欧洲小数逗号）。"""
    return float(s.replace(",", "."))


def to_gb(amount: float, unit: str) -> float:
    u = unit.upper()
    if u == "TB":
        return amount * 1024.0
    if u == "MB":
        return amount / 1024.0
    return amount


def to_tb(amount: float, unit: str) -> float:
    u = unit.upper()
    if u == "GB":
        return round(amount / 1024.0, 2)
    return round(amount, 2)


# --------------------------------------------------------------------------
# 拍平表格重建
# --------------------------------------------------------------------------
#
# HTML 表格被 strip_html 拍平后，每个单元格独占一行：
#
#     Anniversary XXL / 8 Cores / 32 GB / 300 GB / 1 IPv4 / /64 IPv6 / 25 TB / €129
#
# 关键：**裸容量单元格本身不带语义**（"300 GB" 可能是内存/硬盘/流量），
# 必须按**出现顺序**推断。LET 的套餐表列序高度一致：
#     套餐名 | CPU | RAM | 存储 | IPv4 | IPv6 | 流量 | 价格

CELL_VCPU = re.compile(
    r"^(\d{1,3})\s*(?:x\s*)?(?:Cores?|vCores?|vCPUs?|Kerne)$", re.I)
CELL_RAM = re.compile(
    r"^(\d+(?:[.,]\d+)?)\s*(GB|MB|TB)(?:\s*DDR[45])?(?:\s*ECC)?$", re.I)
CELL_STORAGE_TYPED = re.compile(
    r"^(\d+(?:[.,]\d+)?)\s*(GB|TB)\s*"
    r"(NVMe(?:\s*(?:Gen\s*\d|SSD))?|SSD|HDD|SATA)$", re.I)
CELL_SIZE_BARE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(GB|MB|TB)$", re.I)
CELL_BW = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(TB|GB)$", re.I)
CELL_PRICE = re.compile(
    r"^([$€£])\s*(\d+(?:[.,]\d+)?)$|^(\d+(?:[.,]\d+)?)\s*([$€£])$")
CELL_IPV4 = re.compile(r"^\d+\s*IPv4$|^IPv4$", re.I)
CELL_IPV6 = re.compile(r"^/?64\s*IPv6$|^IPv6$", re.I)
CELL_PLAN = re.compile(r"^[A-Za-z][\w\s\-\.\+]{1,40}$")

VALUE_CELLS = (CELL_VCPU, CELL_RAM, CELL_STORAGE_TYPED, CELL_SIZE_BARE,
               CELL_BW, CELL_PRICE, CELL_IPV4, CELL_IPV6)


def is_value_cell(s: str) -> bool:
    return any(rx.match(s) for rx in VALUE_CELLS)


def parse_flat_tables(body: str) -> list[dict]:
    """从拍平的文本里恢复出「套餐行」列表。"""
    lines = [ln.strip() for ln in body.split("\n")]
    plans: list[dict] = []
    i, n = 0, len(lines)
    cycle = None

    while i < n:
        name = lines[i]
        header = re.search(r"\bprice\s*/\s*(year|month|quarter)\b", name, re.I)
        if header:
            cycle = header.group(1).lower()
        elif name.lower() in ("price", "pricing"):
            cycle = None
        if (not name or not CELL_PLAN.match(name) or is_value_cell(name)
                or len(name.split()) > 5):
            i += 1
            continue

        seq: list[tuple[str, object]] = []
        last = i
        for j in range(i + 1, min(i + 16, n)):
            cell = lines[j]
            if not cell:
                continue
            # 撞到下一个套餐名 -> 结束本块
            if CELL_PLAN.match(cell) and not is_value_cell(cell):
                break

            if CELL_VCPU.match(cell):
                seq.append(("vcpu", int(CELL_VCPU.match(cell).group(1))))
            elif CELL_STORAGE_TYPED.match(cell):
                mm = CELL_STORAGE_TYPED.match(cell)
                seq.append(("storage", (to_gb(to_float(mm.group(1)), mm.group(2)),
                                        mm.group(3).strip())))
            elif CELL_IPV4.match(cell) or CELL_IPV6.match(cell):
                seq.append(("ip", cell))
            elif CELL_PRICE.match(cell):
                mm = CELL_PRICE.match(cell)
                if mm.group(1):
                    seq.append(("price", (to_float(mm.group(2)), mm.group(1))))
                else:
                    seq.append(("price", (to_float(mm.group(3)), mm.group(4))))
            elif CELL_RAM.match(cell):
                mm = CELL_RAM.match(cell)
                seq.append(("size", (to_float(mm.group(1)), mm.group(2).upper())))
            else:
                break
            last = j

        if not seq:
            i += 1
            continue

        vcpu = next((v for k, v in seq if k == "vcpu"), None)
        price = next((v for k, v in seq if k == "price"), None)
        typed_storage = next((v for k, v in seq if k == "storage"), None)

        sizes = [v for k, v in seq if k == "size"]
        ram = None
        storage = None
        bw = None

        if typed_storage:
            storage = {"gb": typed_storage[0], "type": typed_storage[1]}
            for amt, unit in sizes:
                if unit in ("GB", "MB") and ram is None:
                    ram = to_gb(amt, unit)
                elif unit == "TB" and bw is None:
                    bw = to_tb(amt, unit)
        else:
            # 裸容量：按顺序 第1个=RAM, 第2个=存储, TB=流量
            for amt, unit in sizes:
                if unit in ("GB", "MB"):
                    if ram is None:
                        ram = to_gb(amt, unit)
                    elif storage is None:
                        storage = {"gb": to_gb(amt, unit), "type": "unspecified"}
                    elif bw is None:
                        bw = to_tb(amt, unit)
                elif unit == "TB":
                    if bw is None:
                        bw = to_tb(amt, unit)

        if vcpu is not None and (ram is not None or storage is not None):
            plans.append({
                "name": name, "vcpu": vcpu, "ram_gb": ram,
                "storage_gb": storage["gb"] if storage else None,
                "storage_type": storage["type"] if storage else None,
                "bandwidth_tb": bw,
                "price": ({"amount": price[0], "currency": price[1], "cycle": cycle}
                          if price else None),
            })
            i = last + 1
        else:
            i += 1

    return plans
