"""Send bounded plain-text reports; never print API URLs or credentials."""
import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TelegramError(RuntimeError):
    pass


def api(token, method, payload):
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token):
        raise TelegramError("Invalid bot token format")
    if method not in {"getMe", "sendMessage"}:
        raise TelegramError("Unsupported bot method")
    request = Request(f"https://api.telegram.org/bot{token}/{method}",
                      data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    for attempt in range(3):
        try:
            with urlopen(request, timeout=15) as response:
                result = json.load(response)
            if not result.get("ok"):
                raise TelegramError("Telegram rejected request")
            return result["result"]
        except HTTPError as exc:
            code = exc.code
            if code == 429 and attempt < 2:
                try:
                    delay = int(json.load(exc).get("parameters", {}).get("retry_after", 5))
                except (ValueError, TypeError):
                    delay = 5
                if delay > 30:
                    raise TelegramError("Telegram rate limit exceeds retry budget") from None
                time.sleep(max(1, delay))
                continue
            if code < 500 or attempt == 2:
                raise TelegramError(f"Telegram HTTP {code}") from None
        except (URLError, TimeoutError, OSError, ValueError, KeyError):
            if attempt == 2:
                raise TelegramError("Telegram transport or response failed; credentials redacted") from None
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    raise TelegramError("Telegram notification failed")


def clean(value, limit=180):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value))[:limit]


def message(status, payload, site_url, run_url, stage=""):
    if status != "success":
        return f"VPS 更新异常\n阶段：{clean(stage or status)}\n本轮未确认发布成功，请检查运行记录；网页可能仍是上次成功版本。\n{run_url}"
    report = payload.get("updates") or {}
    from letdeals.updates import display_time
    lines = ["VPS 优惠更新完成（线上校验通过）", f"更新时间：{display_time(payload.get('generated_at'))}",
             f"共 {payload.get('count', 0)} 条，本轮检出 {payload.get('observed_count', payload.get('count', 0))} 条"]
    labels = {"added": "新增", "changed": "信息变化", "missing": "未检出（非停售）", "restored": "重新检出", "expired": "移出保留窗口"}
    lines.append("；".join(f"{label} {len(report.get(key, []))}" for key, label in labels.items()))
    if report.get("baseline"):
        lines.append("本轮建立变化基线，不把全部记录当新增。")
    for key, label in labels.items():
        for record in report.get(key, [])[:4]:
            fields = ' / ' + '、'.join(record["changes"]) if record.get("changes") else ''
            lines.append(f"{label}：{clean(record.get('provider'))} · {clean(record.get('title'))}{fields}")
    footer = "\n报价/库存以商家结算页为准；变化可能来自原帖或解析调整。\n" + site_url + "\n" + run_url
    return "\n".join(lines)[:max(0, 3900 - len(footer))] + footer


def outcome(state):
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"delivery={state}\n")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token and not chat:
        outcome("not-configured")
        print("::notice::Telegram not configured; no notification sent.")
        return 0
    if not token or not chat:
        outcome("failed")
        print("::error::Both Telegram secrets are required.")
        return 1
    status = os.environ.get("REFRESH_STATUS", "failure")
    payload = {}
    testing = os.environ.get("NOTIFICATION_TEST") == "true"
    if status == "success" and not testing:
        try:
            payload = json.loads(Path("site/data/deals.json").read_text())
        except (ValueError, OSError):
            outcome("failed")
            print("::error::Cannot read verified report for Telegram.")
            return 1
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    text = message(status, payload, os.environ.get("LDR_SITE_URL", "https://vps.869hr.uk"),
                   f"https://github.com/{repo}/actions/runs/{run}", os.environ.get("FAILURE_STAGE", ""))
    if testing:
        text = f"VPS radar Telegram 连接测试：通知路径可用。本消息不代表执行了数据更新。\nhttps://github.com/{repo}/actions/runs/{run}"
    try:
        api(token, "sendMessage", {"chat_id": chat, "text": text, "link_preview_options": {"is_disabled": True}})
    except TelegramError as exc:
        outcome("failed")
        print(f"::error::{exc}")
        return 1
    print("Telegram report delivered.")
    outcome("delivered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
