"""Interactive setup: token travels in memory/stdin, never argv or a file."""
import getpass
import re
import subprocess
import sys
import warnings

from tools.telegram_notify import TelegramError, api


def main():
    if not sys.stdin.isatty():
        print("Run this setup in an interactive terminal; refusing non-hidden input.")
        return 1
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("Telegram bot token (hidden): ").strip()
            chat = getpass.getpass("Target chat ID or @channel (hidden): ").strip()
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        print("Hidden input unavailable or cancelled. Nothing saved.")
        return 1
    if not re.fullmatch(r"-?\d+|@[A-Za-z0-9_]{5,}", chat):
        print("Invalid chat ID. Nothing saved.")
        return 1
    try:
        api(token, "getMe", {})
        api(token, "sendMessage", {"chat_id": chat, "text": "VPS radar: notification destination verified. Saving GitHub Actions secrets now."})
    except TelegramError as exc:
        print(str(exc))
        return 1
    # gh encrypts these for each repository; stdin avoids process-list exposure.
    for repo in ("wlzh/let-deals-radar", "wlzh/let-deals-radar-scheduler"):
        for name, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat)):
            result = subprocess.run(["gh", "secret", "set", name, "-R", repo],
                                    input=value.encode(), capture_output=True)
            if result.returncode:
                print(f"Secret setup failed for {repo}/{name}; retry setup. No credential output printed.")
                return 1
    print("Secrets saved to both repositories. No local credential file created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
