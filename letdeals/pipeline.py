"""编排：采集 → 去重 → 抽取 → 输出记录。

新增的**跨源去重**：同一优惠可能在 LET 和 LEB 都出现，
用「厂商 + 旗舰价 + 旗舰配置」指纹识别，保留信息更全的那条。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from . import config
from .extract import build_record
from .replies import fetch_thread_replies
from .sources import fetch_source


# --------------------------------------------------------------------------
# 跨源去重
# --------------------------------------------------------------------------

def dedupe_key(rec: dict) -> str | None:
    """生成去重指纹：厂商 + 价格 + 旗舰配置。

    刻意不包含标题（两边标题措辞不同），也不含 thread_id（本来就不一样）。

    **数据不足时返回 None（表示不参与去重）** —— 否则像
    ``raindog308|-|-----`` 这种「无价格无配置」的指纹会匹配任意两条
    空记录，把《Reverse DNS 教程》和《我的书被用来训练 AI》误判成重复。
    """
    prov = re.sub(r"[^a-z0-9]", "", (rec.get("provider") or "").lower())
    if not prov:
        return None

    p = rec.get("flagship_price") or ((rec.get("prices") or [None])[0])
    s = rec.get("specs") or {}
    has_price = p is not None
    has_specs = any(s.get(k) for k in ("vcpu", "ram_gb", "storage_gb"))

    # 至少要有一项实质标识，否则无从判断是否同一优惠
    if not has_price and not has_specs:
        return None

    price = f"{p['amount']:.2f}{p['currency']}" if p else "-"
    spec = "{}-{}-{}".format(
        s.get("vcpu") or "-", s.get("ram_gb") or "-",
        s.get("storage_gb") or "-")
    return f"{prov}|{price}|{spec}"


# 信息完整度：用于在两个重复条目中挑更好的那个
def _richness(rec: dict) -> tuple:
    return (
        rec.get("confidence") or 0,
        len(rec.get("plans") or []),
        bool(rec.get("replies")),
        1 if rec.get("source") == "let" else 0,   # LET 有回帖层，优先
    )


def dedupe_records(records: list[dict]) -> tuple[list[dict], int]:
    """跨源去重，返回 (去重后记录, 被合并掉的条数)。

    指纹为 None 的记录（数据不足）**原样保留**，不参与合并。
    """
    best: dict[str, dict] = {}
    unpairable: list[dict] = []

    for r in records:
        k = dedupe_key(r)
        if k is None:
            unpairable.append(r)
            continue
        if k not in best:
            best[k] = r
            continue
        if _richness(r) > _richness(best[k]):
            prev = best[k]
            r.setdefault("also_on", [])
            if prev.get("source") != r.get("source"):
                r["also_on"] = sorted(set(
                    (r.get("also_on") or []) + [prev.get("source", "")]))
            best[k] = r

    merged = len(records) - len(best) - len(unpairable)
    return list(best.values()) + unpairable, merged


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def run(
    *,
    sources: tuple[str, ...] = ("let", "leb"),
    offline: dict[str, str] | None = None,
    limit: int = 0,
    recent_days: int = 0,
    with_replies: bool = False,
    reply_max_pages: int = 2,
    reply_strategy: str = "latest",
    reply_limit: int = 0,
    dedupe: bool = True,
    verbose: bool = True,
) -> dict:
    """执行完整管线，返回 payload。"""
    offline = offline or {}
    all_items: list[dict] = []
    source_failures = []

    # 1) 采集 ---------------------------------------------------------------
    if verbose:
        print(f"[1/5] 采集数据源: {', '.join(sources)}")
    for name in sources:
        try:
            items = fetch_source(name, offline=offline.get(name),
                                 verbose=verbose)
            if not items:
                source_failures.append(name)
            all_items.extend(items)
        except Exception as e:                       # noqa: BLE001
            # 单一源失败不应中断整体
            print(f"  !! 数据源 {name} 失败: {type(e).__name__}: {e}")
            source_failures.append(name)

    if limit:
        all_items = all_items[:limit]
    if verbose:
        print(f"  合计 {len(all_items)} 条原始条目")

    if not all_items:
        return {"source": "all", "count": 0, "records": [], "source_failures": source_failures,
                "generated_at": datetime.now(timezone.utc)
                .isoformat(timespec="seconds")}

    # 2) 回帖（仅 LET）-----------------------------------------------------
    reply_map: dict[str, dict] = {}
    if with_replies:
        targets = [it for it in all_items if it.get("source") == "let"]
        if reply_limit:
            targets = targets[:reply_limit]
        if verbose:
            print(f"[2/5] 抓取回帖（{len(targets)} 个 LET 帖子，"
                  f"策略={reply_strategy}，最多 {reply_max_pages} 页）")
        for n, it in enumerate(targets, 1):
            tid = it.get("thread_id")
            if not tid:
                continue
            try:
                info = fetch_thread_replies(
                    tid, it.get("slug", "x"),
                    max_pages=reply_max_pages, strategy=reply_strategy)
                if info.get("ok"):
                    reply_map[tid] = info
            except Exception as e:                   # noqa: BLE001
                if verbose:
                    print(f"    [warn] #{tid} 抓取失败: {type(e).__name__}")
            if verbose and n % 20 == 0:
                print(f"    已处理 {n}/{len(targets)}")
        vgot = sum(1 for v in reply_map.values()
                   if v.get("reply_source") == "vendor")
        if verbose:
            print(f"  完成：{len(reply_map)} 个帖子抓到回帖，"
                  f"其中 {vgot} 个含商家表态")
    else:
        if verbose:
            print("[2/5] 跳过回帖抓取（--with-replies 可启用）")

    # 3) 抽取 ---------------------------------------------------------------
    if verbose:
        print("[3/5] 抽取字段 ...")
    records = []
    for it in all_items:
        try:
            records.append(build_record(it, reply_map.get(it.get("thread_id"))))
        except Exception as e:                       # noqa: BLE001
            if verbose:
                print(f"    [warn] 抽取失败 "
                      f"{it.get('thread_id')}: {type(e).__name__}")

    before = len(records)
    if recent_days:
        records = [r for r in records
                   if r["age_days"] is not None
                   and r["age_days"] <= recent_days]
        if verbose:
            print(f"  时间过滤({recent_days}天内): {before} -> {len(records)}")

    # 4) 去重 ---------------------------------------------------------------
    merged = 0
    if dedupe:
        records, merged = dedupe_records(records)
        if verbose:
            print(f"[4/5] 跨源去重: 合并 {merged} 条重复，"
                  f"剩余 {len(records)} 条")
    else:
        if verbose:
            print(f"[4/5] 跳过去重，{len(records)} 条")

    # 5) 输出 ---------------------------------------------------------------
    by_source: dict[str, int] = {}
    for r in records:
        s = r.get("source", "?")
        by_source[s] = by_source.get(s, 0) + 1

    payload = {
        "source": "LowEndTalk Offers + LowEndBox",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(records),
        "by_source": by_source,
        "deduped": merged,
        "source_failures": source_failures,
        "records": records,
    }

    if verbose:
        print(f"[5/5] 完成：{len(records)} 条记录 "
              f"({', '.join(f'{k}={v}' for k, v in by_source.items())})")
    return payload


def save(payload: dict, json_path=None, md_path=None) -> None:
    """写 JSON（Markdown 由 render 模块负责）。"""
    json_path = json_path or config.DEALS_JSON
    os.makedirs(os.path.dirname(str(json_path)), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    if md_path:
        from .render import to_markdown
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(to_markdown(payload["records"]))
