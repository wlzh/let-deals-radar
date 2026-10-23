"""命令行入口。

用法示例::

    python -m letdeals                       # 抓取全部数据源并生成站点
    python -m letdeals --with-replies        # 同时抓回帖（库存更准）
    python -m letdeals --sources let         # 只用 LowEndTalk
    python -m letdeals --no-site             # 只产 JSON/Markdown
    python -m letdeals --offline-let data/feed.xml
"""

from __future__ import annotations

import argparse
import os
import sys

from . import config
from . import pipeline
from . import render


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="letdeals",
        description="LowEndTalk / LowEndBox VPS 优惠聚合",
    )
    ap.add_argument("--sources", default="let,leb",
                    help="逗号分隔的数据源：let,leb（默认两者）")
    ap.add_argument("--offline-let", metavar="XML",
                    help="用本地 LET RSS 文件（离线重跑）")
    ap.add_argument("--offline-leb", metavar="XML",
                    help="用本地 LEB RSS 文件")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条")
    ap.add_argument("--recent-days", type=int, default=0,
                    help="只保留最近 N 天")
    ap.add_argument("--with-replies", action="store_true",
                    help="抓取回帖（库存准确率高得多，但更慢）")
    ap.add_argument("--reply-max-pages", type=int, default=2,
                    help="每帖最多抓几页（latest 策略下=末页起抓几页）")
    ap.add_argument("--reply-strategy", choices=["latest", "all"],
                    default="latest",
                    help="latest=末页优先(默认，快且新)；all=从头顺序")
    ap.add_argument("--reply-limit", type=int, default=0,
                    help="只对前 N 个 LET 帖子抓回帖（0=全部）")
    ap.add_argument("--no-dedupe", action="store_true", help="跳跨源去重")
    ap.add_argument("--no-site", action="store_true", help="不生成静态站")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--md-out", default=None)
    ap.add_argument("--site-out", default=None)

    # ---- 论坛发帖（旁路功能，默认关闭且默认演练）----
    ap.add_argument("--post-forum", action="store_true",
                    help="把优惠帖搬运到论坛（需 LDR_FORUM_ENABLED=true）")
    ap.add_argument("--forum-only", action="store_true",
                    help="只发帖：读现有 data/deals.json，不抓取也不建站")
    ap.add_argument("--forum-dry-run", action="store_true",
                    help="演练：只打印将要发布的内容，不提交")
    ap.add_argument("--forum-limit", type=int, default=0,
                    help="本次最多新发几条（0=用配置值）")

    ap.add_argument("-q", "--quiet", action="store_true")
    return ap


def _forum_only(args, verbose: bool) -> int:
    """只发帖：读 data/deals.json，不抓取也不建站。"""
    import json
    from . import forum

    path = args.json_out or str(config.DEALS_JSON)
    if not os.path.exists(path):
        print("!! 找不到 %s；请先运行一次抓取。" % path)
        return 1

    with open(path, encoding="utf-8") as f:
        payload = json.load(f)

    recs = payload.get("records") or []
    if not recs:
        print("!! %s 里没有记录。" % path)
        return 1

    if verbose:
        print("=" * 72)
        print("论坛发帖（只发帖模式）")
        print("=" * 72)
        print("  数据来源：%s（%d 条）" % (path, len(recs)))

    try:
        forum.post_deals(
            recs,
            limit=args.forum_limit or None,
            dry_run=True if args.forum_dry_run else None,
            verbose=verbose,
        )
    except Exception as e:              # 不让发帖失败影响整体
        print("  !! 论坛发帖出错：%r" % e)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    verbose = not args.quiet

    # ---- 只发帖模式：读现有 deals.json，不抓取也不建站 ----
    # 用于把发帖与抓取解耦：工作流先更新数据并提交，
    # 再由单独的步骤读同一份 JSON 发帖，避免重复抓取。
    if args.forum_only:
        return _forum_only(args, verbose)

    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    offline = {}
    if args.offline_let:
        offline["let"] = args.offline_let
    if args.offline_leb:
        offline["leb"] = args.offline_leb

    payload = pipeline.run(
        sources=sources,
        offline=offline,
        limit=args.limit,
        recent_days=args.recent_days,
        with_replies=args.with_replies,
        reply_max_pages=args.reply_max_pages,
        reply_strategy=args.reply_strategy,
        reply_limit=args.reply_limit,
        dedupe=not args.no_dedupe,
        verbose=verbose,
    )

    if payload.get("source_failures"):
        print("!! 数据源缺失，拒绝覆盖现有站点：" + ", ".join(payload["source_failures"]))
        return 1
    if not payload.get("records"):
        print("!! 没有抓到任何记录；为避免发布空数据，已中止。")
        print("   提示：可用 --offline-let data/feed_let.xml 复用缓存重跑。")
        return 1

    # 输出：JSON + Markdown
    render.enrich(payload["records"])
    json_out = args.json_out or str(config.DEALS_JSON)
    md_out = args.md_out or str(config.DEALS_MD)
    pipeline.save(payload, json_out, md_out)

    if verbose:
        print()
        print("=" * 72)
        print("字段覆盖率")
        print("=" * 72)
        recs = payload["records"]
        n = len(recs) or 1

        def pct(pred):
            return f"{sum(1 for r in recs if pred(r)) / n * 100:.0f}%"

        # 推广链接：配了就提示一声，能立刻看出是否生效
        from . import affiliate
        if affiliate._load():
            print(f"  推广链接      {affiliate.summary()}")
            print()

        print(f"  vCPU          {pct(lambda r: (r.get('specs') or {}).get('vcpu'))}")
        print(f"  RAM           {pct(lambda r: (r.get('specs') or {}).get('ram_gb'))}")
        print(f"  存储          {pct(lambda r: (r.get('specs') or {}).get('storage_gb'))}")
        print(f"  带宽          {pct(lambda r: (r.get('specs') or {}).get('bandwidth_tb'))}")
        print(f"  CPU 型号      {pct(lambda r: r.get('cpu_models'))}")
        print(f"  价格          {pct(lambda r: r.get('prices'))}")
        print(f"  折扣/优惠码    {pct(lambda r: (r.get('discount') or {}).get('strength'))}")
        print(f"  地点          {pct(lambda r: r.get('locations'))}")
        print(f"  套餐表({sum(1 for r in recs if r.get('plans'))}条)")
        if args.with_replies:
            print(f"  回帖已抓取     {pct(lambda r: r.get('replies'))}")
            print(f"  商家库存表态   "
                  f"{pct(lambda r: (r.get('replies') or {}).get('source') == 'vendor')}")
        print(f"  库存已判定     "
              f"{pct(lambda r: r.get('availability') not in ('UNVERIFIED', 'UNVERIFIED_STALE'))}")
        print()
        dist: dict[str, int] = {}
        for r in recs:
            dist[r.get("availability")] = dist.get(r.get("availability"), 0) + 1
        print("库存状态分布: " + ", ".join(
            f"{k}={v}" for k, v in sorted(dist.items(), key=lambda kv: -kv[1])))

    if not args.no_site:
        if verbose:
            print()
        render.build_site(payload, args.site_out, verbose=verbose)

    # ---- 论坛发帖（旁路：失败不影响站点更新）----
    if args.post_forum:
        if verbose:
            print()
            print("=" * 72)
            print("论坛发帖")
            print("=" * 72)
        try:
            from . import forum
            forum.post_deals(
                payload["records"],
                limit=args.forum_limit or None,
                dry_run=True if args.forum_dry_run else None,
                verbose=verbose,
            )
        except Exception as e:          # 不让发帖失败影响主流程
            print("  !! 论坛发帖出错（不影响站点更新）：%r" % e)

    if verbose:
        print()
        print(f"JSON: {json_out}")
        print(f"MD  : {md_out}")
        if not args.no_site:
            print(f"SITE: {args.site_out or config.SITE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
