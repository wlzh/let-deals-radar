"""Report configured and missing personal affiliate mappings, without guessing IDs."""

import json
from collections import defaultdict

from letdeals import affiliate


def report(payload):
    groups = defaultdict(list)
    unknown = []
    mapping = affiliate._load()
    for r in payload["records"]:
        domain = affiliate._domain_of(r.get("vendor_url") or "")
        if not domain:
            unknown.append(r)
        elif not affiliate._match(r["vendor_url"], mapping)[0]:
            groups[domain].append(r)
    lines = ["# 个人 AFF 配置盘点", "",
             "这里的缺失是未配置站长个人推广标识，不代表商家没有联盟计划。不得使用原帖作者的 ID 代替。", "",
             "## 已配置", "", "| 域名 | 当前推广规则 |", "|---|---|"]
    for domain, cfg in sorted(mapping.items()):
        value = cfg if isinstance(cfg, str) else cfg["param"] + "=" + cfg["value"]
        lines.append(f"| {domain} | `{value}` |")
    lines += ["", "## 有入口但尚未配置个人 AFF", "", "| 商家入口域名 | 当前优惠数 | 来源样本 |", "|---|---:|---|"]
    for domain, rows in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines.append(f"| {domain} | {len(rows)} | [原帖]({rows[0]['url']}) |")
    lines += ["", "## 尚未确认购买入口", ""]
    for r in unknown:
        title = (r.get("title") or r.get("provider") or "未知").replace("[", "(").replace("]", ")")
        lines.append(f"- [{title}]({r['url']})")
    lines += ["", "补充每家后台提供的个人推广链接后，再核对域名、深链支持与商家归因规则。不猜测佣金率、Cookie 时长或账户归属。", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(json.load(open("site/data/deals.json"))))
