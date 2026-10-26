import pytest

from tools import configure_telegram as setup
from tools.telegram_notify import TelegramError


def test_forum_link_is_topic_not_channel_post(monkeypatch):
    monkeypatch.setattr(setup, "api", lambda *a: {"id": -1001, "type": "supergroup", "is_forum": True})
    target = setup.resolve_target("unused", "https://t.me/tgmShare/3385")
    assert target == {"chat_id": "-1001", "message_thread_id": 3385}


def test_channel_reference_is_not_thread(monkeypatch):
    monkeypatch.setattr(setup, "api", lambda *a: {"id": -1002, "type": "channel"})
    assert setup.resolve_target("unused", "https://t.me/tgmShare/3385") == {"chat_id": "-1002"}


@pytest.mark.parametrize("url", ["https://evil.invalid/tgmShare", "https://t.me/tgmShare?token=bad", "https://t.me/tgmShare/1/2"])
def test_unknown_destinations_are_rejected(url):
    with pytest.raises(TelegramError):
        setup.resolve_target("unused", url)


def test_setup_refuses_non_interactive_secret_input(monkeypatch):
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: False)
    assert setup.main() == 1


def test_setup_rejects_bot_id_as_private_recipient(monkeypatch):
    monkeypatch.setattr(setup, "api", lambda *a: [])
    monkeypatch.setattr("builtins.input", lambda *a: "999999")
    with pytest.raises(TelegramError, match="BOT ID"):
        setup.choose_private_chat("unused", 999999, [])
