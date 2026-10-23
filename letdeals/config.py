"""运行配置。

所有可调项集中在此，并支持环境变量覆盖 —— 便于在 GitHub Actions
和自己的服务器上使用不同配置而无需改代码。
"""

from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------

# 项目根目录（本文件在 <root>/letdeals/config.py）
ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(_env("LDR_DATA_DIR", str(ROOT / "data")))
SITE_DIR = Path(_env("LDR_SITE_DIR", str(ROOT / "site")))
REPLY_CACHE_DIR = DATA_DIR / "replies"

# 站点静态资源（图标等）。与抓取数据分开：这些是需要版本管理的品牌素材，
# 不是可重建的缓存。
ASSETS_DIR = Path(_env("LDR_ASSETS_DIR", str(ROOT / "assets")))

DEALS_JSON = DATA_DIR / "deals.json"
DEALS_MD = DATA_DIR / "deals.md"
FEED_CACHE = DATA_DIR / "feed.xml"


# --------------------------------------------------------------------------
# 站点 / 域名
# --------------------------------------------------------------------------
# 部署到自己域名时，改这几个环境变量即可，无需改代码。

SITE_TITLE = _env("LDR_SITE_TITLE", "VPS 活动雷达")
SITE_DESC = _env(
    "LDR_SITE_DESC",
    "自动汇总公开渠道的 VPS 优惠活动，"
    "标注折扣力度、循环折扣与库存状态。",
)
# 例：https://deals.example.com  （末尾不要带 /）
SITE_URL = _env("LDR_SITE_URL", "").rstrip("/")
SITE_FOOTER = _env(
    "LDR_SITE_FOOTER",
    "大坝家族站点：<a href='https://869hr.uk/'>📖 技术博客</a> · <a href='https://doc.869hr.uk/'>📚 资源收集站</a> · <a href='https://t.me/tgmShareAI'>📢 TG频道</a>",
)

# 页头的附加链接，格式 "名称|地址"，多项用逗号分隔。
# 例：LDR_HEADER_LINKS="GitHub|https://github.com/x/y, Telegram|https://t.me/z"
# 留空则不显示 —— 不影响现有布局。
HEADER_LINKS_RAW = _env(
    "LDR_HEADER_LINKS",
    "📖 博客|https://869hr.uk/,"
    " 📚 资源站|https://doc.869hr.uk/,"
    " 📢 TG|https://t.me/tgmShareAI",
)


def _parse_header_links(raw: str) -> list[tuple[str, str]]:
    """把 "名称|地址,..." 解析成 [(名称, 地址)]，非法项跳过。"""
    out: list[tuple[str, str]] = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk or "|" not in chunk:
            continue
        name, url = chunk.split("|", 1)
        name, url = name.strip(), url.strip()
        if name and url.startswith(("http://", "https://")):
            out.append((name, url))
    return out


HEADER_LINKS = _parse_header_links(HEADER_LINKS_RAW)


# --------------------------------------------------------------------------
# 数据源
# --------------------------------------------------------------------------

LET_FEED_URL = _env(
    "LDR_LET_FEED",
    "https://lowendtalk.com/categories/offers/feed.rss",
)
LEB_FEED_URL = _env("LDR_LEB_FEED", "https://lowendbox.com/feed/")

USER_AGENT = _env(
    "LDR_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)

HTTP_TIMEOUT = _env_int("LDR_HTTP_TIMEOUT", 30)
HTTP_RETRIES = _env_int("LDR_HTTP_RETRIES", 3)
HTTP_BACKOFF = 1.6          # 重试退避基数（秒）

# 抓取回帖时的页间延迟，对站点友好
REPLY_DELAY = 0.8
REPLY_CACHE_TTL = 3600      # 回帖缓存有效期（秒）
REPLY_MAX_PAGES_HARD = 25   # 硬上限，防异常帖拖爆


# --------------------------------------------------------------------------
# 判定阈值
# --------------------------------------------------------------------------

NEW_DAYS = 7        # < 7 天视为新帖
ACTIVE_DAYS = 30    # <= 30 天视为活跃

# 跨币种排序用的近似汇率（仅用于排序，不用于展示成交价）
FX_TO_EUR = {"€": 1.00, "$": 0.92, "£": 1.17}
FX_NOTE = "跨币种排序按近似汇率折算（€1.00 / $0.92 / £1.17），仅供参考"

CYCLE_TO_YEAR = {"year": 1.0, "quarter": 4.0, "month": 12.0}


# --------------------------------------------------------------------------
# 论坛发帖（旁路功能）
# --------------------------------------------------------------------------
# 设计原则：默认关闭 + 默认演练 + 幂等 + 限流。
# 任何一项没配好都不会真的发帖。

# 总开关。false 时 post_deals 直接返回，不发任何内容。
# 想临时停发帖，改这一个值即可，无需碰论坛。
FORUM_ENABLED = _env_bool("LDR_FORUM_ENABLED", False)

# 论坛地址与 API
FORUM_BASE_URL = _env("LDR_FORUM_URL", "https://lowseek.ai").rstrip("/")
FORUM_API_KEY = _env("LDR_FORUM_API_KEY", "")

# 目标标签 ID（「内容转载」）
FORUM_TAG_ID = _env("LDR_FORUM_TAG_ID", "21")

# 机器人账号 ID（用于日志核对；不参与鉴权）
FORUM_BOT_USER_ID = _env("LDR_FORUM_BOT_ID", "678")

# 每次运行最多发几条（限流，防止一次灌满论坛）
FORUM_MAX_PER_RUN = _env_int("LDR_FORUM_MAX_PER_RUN", 3)

# 只发最近 N 天内的帖子；0 表示不限
FORUM_MAX_AGE_DAYS = _env_int("LDR_FORUM_MAX_AGE_DAYS", 3)

# 已发布记录（幂等：同一 thread_id 永不重复发）
FORUM_POSTED_LOG = DATA_DIR / "forum_posted.json"

# 是否真的提交。false = 只打印将要发布的内容（演练）。
# 需要显式设 LDR_FORUM_DRY_RUN=false 才会真发。
FORUM_DRY_RUN = _env_bool("LDR_FORUM_DRY_RUN", True)

# 筛选门槛：只搬运「够格」的帖子，避免刷屏
# 折扣百分比达到此值，或属于下列活动性质，才算值得搬
FORUM_MIN_PERCENT = _env_int("LDR_FORUM_MIN_PERCENT", 30)
FORUM_EVENT_PRIORITY = ("anniversary", "black_friday", "holiday", "restock")
