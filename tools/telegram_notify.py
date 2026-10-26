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
    if method not in {"getMe", "getChat", "getUpdates", "sendMessage"}:
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
    lines = ["VPS 优惠信息更新", f"更新时间：{display_time(payload.get('generated_at'))}",
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


def destinations(raw):
    try:
        targets = json.loads(raw)
    except ValueError:
        raise TelegramError("Invalid publication destinations") from None
    if not isinstance(targets, list) or not 1 <= len(targets) <= 10:
        raise TelegramError("Publication destinations must be a list of 1-10 chats")
    seen = set()
    for item in targets:
        if not isinstance(item, dict) or not re.fullmatch(r"-?\d+|@[A-Za-z0-9_]{5,}", str(item.get("chat_id", ""))):
            raise TelegramError("Invalid publication chat")
        thread = item.get("message_thread_id")
        if thread is not None and (type(thread) is not int or thread <= 0):
            raise TelegramError("Invalid publication topic")
        identity = (str(item["chat_id"]), thread)
        if identity in seen:
            raise TelegramError("Duplicate publication destination")
        seen.add(identity)
    return targets


def send(token, target, text):
    body = {"chat_id": target["chat_id"], "text": text, "link_preview_options": {"is_disabled": True}}
    if target.get("message_thread_id"):
        body["message_thread_id"] = target["message_thread_id"]
    return api(token, "sendMessage", body)


def private_alert(token, chat, text):
    # The error path never loads publication targets. Positive IDs are private
    # users; also verify chat type with Telegram before sending any diagnostics.
    if not re.fullmatch(r"[1-9]\d*", chat):
        raise TelegramError("Private alert chat must be a positive numeric user ID")
    info = api(token, "getChat", {"chat_id": chat})
    if info.get("type") != "private":
        raise TelegramError("Refusing to send diagnostics outside a private chat")
    send(token, {"chat_id": chat}, text)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    alert = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "")
    targets = os.environ.get("TELEGRAM_TARGETS", "")
    if not token and not alert and not targets:
        outcome("not-configured")
        print("::notice::Telegram not configured; no notification sent.")
        return 0
    if not token or not alert:
        outcome("failed")
        print("::error::Bot token and private alert destination are required.")
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
            try:
                private_alert(token, alert, "VPS radar：已验证更新报告无法读取，未向发布目标发送消息。请检查 Actions。")
            except TelegramError:
                print("::warning::Private Telegram alert also failed.")
            return 1
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    run_url = f"https://github.com/{repo}/actions/runs/{run}"
    text = message(status, payload, os.environ.get("LDR_SITE_URL", "https://vps.869hr.uk"),
                   "" if status == "success" else run_url, os.environ.get("FAILURE_STAGE", ""))
    if testing:
        text = f"VPS radar Telegram 连接测试：通知路径可用。本消息不代表执行了数据更新。\nhttps://github.com/{repo}/actions/runs/{run}"
    alert_sent = False
    try:
        if testing or status != "success":
            private_alert(token, alert, text)
            print("Telegram private message delivered; no publication targets contacted.")
            outcome("private-delivered")
            return 0
        report = payload.get("updates") or {}
        publication_targets = destinations(targets)
        if not any(report.get(key) for key in ("added", "changed", "missing", "restored", "expired")):
            print("No meaningful changes; no public Telegram message sent.")
            outcome("no-changes")
            return 0
        failures = []
        receipts = []
        for index, target in enumerate(publication_targets, 1):
            try:
                result = send(token, target, text)
                receipts.append({"target_index": index, "status": "delivered", "message_id": result.get("message_id")})
            except TelegramError as exc:
                failures.append(index)
                receipts.append({"target_index": index, "status": "failed", "reason": str(exc)})
        Path("telegram-delivery.json").write_text(json.dumps({"run_id": run, "destinations": receipts}, indent=2) + "\n")
        if failures:
            private_alert(token, alert, f"VPS radar：发布目标序号 {','.join(map(str, failures))} 发送失败；其余目标已尝试发送。请按投递回执只补发失败目标，避免重复。\n{run_url}")
            alert_sent = True
            raise TelegramError(f"Publication delivery failed for {len(failures)} destination(s); diagnostics stayed private")
    except TelegramError as exc:
        if status == "success" and not testing and not alert_sent:
            try:
                private_alert(token, alert, f"VPS radar：通知配置或发送异常（{exc}），网页状态请查看运行记录。\n{run_url}")
            except TelegramError:
                print("::warning::Private Telegram alert also failed; no diagnostic sent to publication targets.")
        outcome("failed")
        print(f"::error::{exc}")
        return 1
    print("Telegram report delivered.")
    outcome("delivered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
