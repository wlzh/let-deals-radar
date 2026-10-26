"""Interactive setup: token travels in memory/stdin, never argv or a file."""
import getpass
import json
import re
import subprocess
import sys
import warnings
from pathlib import Path
from urllib.parse import urlsplit

from tools.telegram_notify import TelegramError, api, private_alert


def resolve_target(token, url):
    parsed = urlsplit(url)
    match = re.fullmatch(r"/([A-Za-z0-9_]{5,})(?:/(\d+))?/?", parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "t.me" or not match or parsed.query or parsed.fragment:
        raise TelegramError("Invalid configured Telegram publication URL")
    username, reference = match.groups()
    info = api(token, "getChat", {"chat_id": "@" + username})
    if info.get("type") not in {"channel", "supergroup", "group"}:
        raise TelegramError("Publication target is not a group or channel")
    result = {"chat_id": str(info["id"])}
    if reference and info.get("is_forum"):
        result["message_thread_id"] = int(reference)
    elif reference and info.get("type") != "channel":
        raise TelegramError("URL has a topic reference but Telegram does not confirm a forum; verify destination")
    # For channels /123 identifies an old post, not a writable topic.
    print(f"Validated @{username}: {info['type']}, " + (f"topic {reference}" if result.get("message_thread_id") else "new group/channel message"))
    return result


def choose_private_chat(token, bot_id, targets):
    try:
        updates = api(token, "getUpdates", {"limit": 100, "timeout": 0})
    except TelegramError:
        print("Cannot list recent private conversations (e.g. existing webhook); webhook was not modified. Enter your known private ID below.")
        updates = []
    contacts = {}
    for update in updates:
        msg = update.get("message", {})
        chat = msg.get("chat", {})
        if chat.get("type") == "private" and not msg.get("from", {}).get("is_bot") and msg.get("from", {}).get("id") == chat.get("id"):
            contacts[str(chat["id"])] = chat
    if not contacts:
        print("No recent private update available; listing human administrators of the configured destinations for you to identify YOUR account.")
        for target in targets:
            try:
                members = api(token, "getChatAdministrators", {"chat_id": target["chat_id"]})
                for member in members:
                    user = member.get("user", {})
                    if user.get("id") and not user.get("is_bot"):
                        contacts[str(user["id"])] = user
            except TelegramError:
                continue
    for identity, chat in contacts.items():
        print(f"Human account candidate: {identity}, @{chat.get('username', '')} {chat.get('first_name', '')}")
    value = input("Confirm YOUR OWN personal numeric user ID (not the bot, not another administrator): ").strip()
    if not re.fullmatch(r"[1-9]\d*", value):
        raise TelegramError("Private alert destination must be a positive numeric user ID")
    if value == str(bot_id):
        raise TelegramError("This is the BOT ID, not your personal user ID. No secrets saved.")
    info = api(token, "getChat", {"chat_id": value})
    if info.get("type") != "private":
        raise TelegramError("Alert destination is not a private conversation")
    return value


def main():
    if not sys.stdin.isatty():
        print("Run this setup in an interactive terminal; refusing non-hidden input.")
        return 1
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("Telegram bot token (hidden): ").strip()
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        print("Hidden input unavailable or cancelled. Nothing saved.")
        return 1
    try:
        bot = api(token, "getMe", {})
        if bot.get("username", "").lower() != "mswnlz_github_resource_sync_bot":
            raise TelegramError("Unexpected bot; this setup expects @mswnlz_github_resource_sync_bot")
        urls = json.loads((Path(__file__).resolve().parents[1] / "deploy/telegram-targets.json").read_text())
        targets = [resolve_target(token, url) for url in urls]
        alert = choose_private_chat(token, bot.get("id"), targets)
        if input("Save these publication targets and your PRIVATE alert chat? Type yes: ").strip() != "yes":
            print("Cancelled; nothing saved.")
            return 1
        private_alert(token, alert, "VPS radar: PRIVATE alert destination verified. No test messages sent to the six publication targets. Saving GitHub Actions secrets now.")
    except TelegramError as exc:
        print(str(exc))
        return 1
    # gh encrypts these for each repository; stdin avoids process-list exposure.
    for repo in ("wlzh/let-deals-radar", "wlzh/let-deals-radar-scheduler"):
        for name, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_TARGETS", json.dumps(targets)),
                            ("TELEGRAM_ALERT_CHAT_ID", alert)):
            result = subprocess.run(["gh", "secret", "set", name, "-R", repo],
                                    input=value.encode(), capture_output=True)
            if result.returncode:
                print(f"Secret setup failed for {repo}/{name}; retry setup. No credential output printed.")
                return 1
    print("Secrets saved to both repositories. No local credential file created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
