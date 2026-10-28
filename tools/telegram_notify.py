"""Send bounded, escaped reports; never print API URLs or credentials."""
import html
import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SITE_LINK = "https://vps.869hr.uk"


class TelegramError(RuntimeError):
    pass


def api(token, method, payload):
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token):
        raise TelegramError("Invalid bot token format")
    if method not in {"getMe", "getChat", "getUpdates", "getChatAdministrators", "sendMessage"}:
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
            try:
                error_data = json.load(exc)
            except (ValueError, TypeError):
                error_data = {}
            if code == 429 and attempt < 2:
                try:
                    delay = int(error_data.get("parameters", {}).get("retry_after", 5))
                except (ValueError, TypeError):
                    delay = 5
                if delay > 30:
                    raise TelegramError("Telegram rate limit exceeds retry budget") from None
                time.sleep(max(1, delay))
                continue
            if code < 500 or attempt == 2:
                # Only fixed allowlisted explanations, never raw API descriptions
                # or transport exception strings that may embed the token URL.
                description = str(error_data.get("description", "")).lower()
                reasons = (("bots", "cannot message another bot; use YOUR personal user ID"),
                           ("blocked", "bot is blocked by the private recipient"),
                           ("initiate", "recipient must start a private conversation with this bot"),
                           ("deactivated", "private recipient account is deactivated"),
                           ("not a member", "bot is not a member of the destination"),
                           ("not enough rights", "bot lacks destination posting permission"),
                           ("thread not found", "topic ID is invalid or unavailable"))
                reason = next((label for pattern, label in reasons if pattern in description), "request rejected; check destination and permissions")
                raise TelegramError(f"Telegram HTTP {code}: {reason}") from None
        except (URLError, TimeoutError, OSError, ValueError, KeyError):
            if attempt == 2:
                raise TelegramError("Telegram transport or response failed; credentials redacted") from None
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    raise TelegramError("Telegram notification failed")


def clean(value, limit=180):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value))[:limit]


def stage_text(value):
    labels = {"tests": "自动测试", "build": "抓取构建", "commit": "数据提交", "pages": "站点直传", "live": "网页验收",
              "timer": "等待计时", "refresh": "优惠更新", "continuation": "每日接续",
              "manual-publication-live-verification": "补发前网页验收"}
    states = {"success": "成功", "failure": "失败", "skipped": "未执行", "cancelled": "已取消", "": "未执行"}
    return "；".join(labels.get(part.partition("=")[0].strip(), "更新流程") + "：" +
                    states.get(part.partition("=")[2].strip(), "异常，请查看运行记录")
                    for part in value.split(";") if part.strip()) or "更新流程异常"


def error_hint(error):
    text = str(error).lower()
    reasons = (("bot itself", "告警收件人填成了机器人自身，请改为你本人的账号"),
               ("another bot", "不能给机器人发送私聊，请使用你本人的账号"),
               ("blocked", "机器人被收件人屏蔽，请解除屏蔽后重新私聊"),
               ("start a private", "请先向机器人发送 /start 建立私聊"),
               ("posting permission", "机器人没有目标会话的发帖权限"),
               ("topic id", "话题编号不存在或暂不可用"),
               ("transport", "通知网络连接异常，重试后仍未成功"),
               ("destinations", "发布目标配置无效，请重新配置"),
               ("http 401", "机器人凭据无效，请更新安全配置"),
               ("http 403", "目标会话拒绝接收，请检查成员身份、私聊状态和发送权限"))
    return next((hint for pattern, hint in reasons if pattern in text), "通知配置或发送失败，请检查运行记录")


def change_lines(changes):
    from letdeals.updates import describe_changes
    lines = []
    discount = changes.get("优惠规则")
    if discount:
        current = discount.get("after") or {}
        if current.get("percent_max"):
            lines.append(f"最高优惠：{current['percent_max']}%")
        if current.get("codes"):
            lines.append("优惠码：" + "、".join(current["codes"]))
        if current.get("rule_conflict"):
            lines.append("续费提醒：首期与循环描述不一致，暂不按永久折扣展示。")
        elif current.get("introductory"):
            lines.append("续费提醒：首期优惠，续费价格待核对。")
        elif current.get("recurring"):
            lines.append("优惠规则：帖文标注循环优惠，适用条件请到商家核对。")
        else:
            lines.append("优惠规则：条款有调整，请查看完整说明。")
        if current.get("extras"):
            lines.append("附加赠送条件请到网页核对。")
    for label, pair in changes.items():
        if label != "优惠规则":
            lines.append(describe_changes({label: pair}).replace(" → ", " 改为 "))
    return lines


def escaped(value, limit=180):
    return html.escape(clean(value, limit), quote=True)


def message_size(text):
    # Count raw UTF-16 conservatively; Telegram limits text after entity parsing.
    return len(text.encode("utf-16-le", errors="replace")) // 2


def message(status, payload, site_url, run_url, stage=""):
    if status != "success":
        return f"VPS 优惠更新异常\n阶段：{stage_text(stage or status)}\n本轮未确认发布成功，请检查运行记录；网页可能仍是上次成功版本。\n{run_url}"
    report = payload.get("updates") or {}
    from letdeals.updates import display_time
    lines = ["<b>VPS 优惠信息更新</b>", escaped(display_time(payload.get('generated_at'))),
             f"本站收录 {escaped(payload.get('count', 0), 20)} 条活动", "", "<b>本次看点</b>"]
    labels = {"added": "新增优惠", "changed": "优惠信息更新", "restored": "重新检出的活动", "missing": "本轮未检出（非停售）", "expired": "移出保留窗口"}
    counts = [f"{label}：{len(report.get(key, []))} 条" for key, label in labels.items() if report.get(key)]
    lines += counts or ["本轮暂无新增或变化"]
    if report.get("baseline"):
        lines.append("本轮建立变化基线，不把全部记录当新增。")
    changed = report.get("changed", [])
    if changed and all(set(record.get("changes", {})) == {"优惠规则"} for record in changed):
        lines += ["", "本次主要核对优惠规则，不代表商家实际调价。"]
    footer = "\n\n<b>查看全部优惠与更新明细</b>\n" + escaped(site_url, 250)
    footer += "\n\n<i>报价、库存与续费以商家结算页为准。</i>"
    if run_url:
        footer += "\n" + escaped(run_url, 250)
    total = sum(len(report.get(key, [])) for key in labels)
    displayed = 0
    for key, label in labels.items():
        heading_added = False
        for record in report.get(key, [])[:4]:
            if displayed >= 6:
                break
            provider = str(record.get("provider") or "商家待核实").replace("_", " ")
            provider = {"bero-host.de": "BERO HOST", "servers guru": "Servers Guru"}.get(provider, provider)
            block = [] if heading_added else ["", f"<b>{label}</b>"]
            block += ["", f"<b>{displayed + 1}. {escaped(provider, 80)}</b>"]
            if record.get("changes"):
                block += [escaped(line, 180) for line in change_lines(record["changes"])[:5]]
            else:
                summary = record.get("summary") or "活动详情、价格及库存请到网页核实"
                block += [escaped(part, 180) for part in str(summary).split(" · ")[:4]]
            candidate = "\n".join(lines + block) + footer
            if message_size(candidate) > 3700:
                break
            lines += block
            heading_added = True
            displayed += 1
    if total > displayed:
        lines += ["", f"以上展示 {displayed} 条，其余 {total - displayed} 条请到网站查看。"]
    return "\n".join(lines) + footer


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


def send(token, target, text, formatted=False):
    visible = html.unescape(re.sub(r"<[^>]*>", "", text)) if formatted else text
    if not any(line.strip().rstrip("/") in {SITE_LINK, "网站：" + SITE_LINK} for line in visible.splitlines()):
        text = (text if formatted else text[:3900]) + "\n网站：" + SITE_LINK
    if formatted and message_size(text) > 3900:
        raise TelegramError("Formatted report exceeds safe Telegram size")
    body = {"chat_id": target["chat_id"], "text": text, "link_preview_options": {"is_disabled": True}}
    if formatted:
        body["parse_mode"] = "HTML"
    if target.get("message_thread_id"):
        body["message_thread_id"] = target["message_thread_id"]
    return api(token, "sendMessage", body)


def private_alert(token, chat, text, formatted=False):
    # The error path never loads publication targets. Positive IDs are private
    # users; also verify chat type with Telegram before sending any diagnostics.
    if not re.fullmatch(r"[1-9]\d*", chat):
        raise TelegramError("Private alert chat must be a positive numeric user ID")
    bot = api(token, "getMe", {})
    if str(bot.get("id")) == chat:
        raise TelegramError("Alert ID is the BOT itself; enter YOUR personal Telegram user ID")
    info = api(token, "getChat", {"chat_id": chat})
    if info.get("type") != "private":
        raise TelegramError("Refusing to send diagnostics outside a private chat")
    send(token, {"chat_id": chat}, text, formatted=formatted)


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
    preview = os.environ.get("NOTIFICATION_PREVIEW") == "true"
    if (status == "success" and not testing) or preview:
        try:
            payload = json.loads(Path("site/data/deals.json").read_text())
            if os.environ.get("PUBLICATION_SCOPE") == "today" or preview:
                from letdeals.updates import daily_report
                payload["updates"] = daily_report(payload.get("update_history", []), payload.get("generated_at"))
        except (ValueError, OSError):
            outcome("failed")
            print("::error::Cannot read verified report for Telegram.")
            try:
                private_alert(token, alert, "VPS 优惠雷达：已验证更新报告无法读取，未向发布目标发送消息。请检查运行记录。")
            except TelegramError:
                print("::warning::Private Telegram alert also failed.")
            return 1
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    run_url = f"https://github.com/{repo}/actions/runs/{run}"
    text = message("success" if preview else status, payload, os.environ.get("LDR_SITE_URL", "https://vps.869hr.uk"),
                   "" if status == "success" else run_url, os.environ.get("FAILURE_STAGE", ""))
    if status == "success" and os.environ.get("PUBLICATION_SCOPE") == "today":
        text = text.replace("VPS 优惠信息更新", "VPS 优惠今日变化汇总（补发）", 1)
    if testing and not preview:
        text = f"VPS 优惠雷达：Telegram 私聊连接测试成功。本消息不代表执行了数据更新。\nhttps://github.com/{repo}/actions/runs/{run}"
    if preview:
        text = "<b>排版预览（仅私聊，不向群重复发布）</b>\n\n" + text
    alert_sent = False
    try:
        if testing or preview or status != "success":
            private_alert(token, alert, text, formatted=preview)
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
                result = send(token, target, text, formatted=True)
                receipts.append({"target_index": index, "status": "delivered", "message_id": result.get("message_id")})
            except TelegramError as exc:
                failures.append(index)
                receipts.append({"target_index": index, "status": "failed", "reason": str(exc)})
        Path("telegram-delivery.json").write_text(json.dumps({"run_id": run, "destinations": receipts}, indent=2) + "\n")
        if failures:
            private_alert(token, alert, f"VPS 优惠雷达：发布目标序号 {','.join(map(str, failures))} 发送失败；其余目标已尝试发送。请按投递回执只补发失败目标，避免重复。\n{run_url}")
            alert_sent = True
            raise TelegramError(f"Publication delivery failed for {len(failures)} destination(s); diagnostics stayed private")
    except TelegramError as exc:
        if status == "success" and not testing and not alert_sent:
            try:
                private_alert(token, alert, f"VPS 优惠雷达：通知异常。{error_hint(exc)}。网页状态请查看运行记录。\n{run_url}")
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
