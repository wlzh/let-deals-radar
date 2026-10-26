import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from tools import telegram_notify as tg


TOKEN = "123456:" + "x" * 32


def test_success_message_is_bounded_and_has_changes():
    data = {"count": 82, "updates": {"added": [{"provider": "X", "title": "new\nactivity"}]}}
    text = tg.message("success", data, "https://example.invalid", "https://github.com/run")
    assert "新增 1" in text and "new activity" in text
    assert "线上校验通过" in text
    assert len(text) <= 3900


def test_failure_never_announces_success():
    text = tg.message("failure", {}, "site", "run", "live=failure")
    assert "异常" in text and "live=failure" in text and "完成" not in text


def test_api_uses_post_plain_json(monkeypatch):
    def fetch(req, timeout):
        assert req.method == "POST" and timeout == 15
        body = json.loads(req.data)
        assert body["text"] == "report" and "parse_mode" not in body
        return BytesIO(b'{"ok":true,"result":{"message_id":1}}')
    monkeypatch.setattr(tg, "urlopen", fetch)
    assert tg.api(TOKEN, "sendMessage", {"chat_id": "1", "text": "report"})["message_id"] == 1


@pytest.mark.parametrize("error", [URLError("https://api.telegram.org/bot" + TOKEN),
                                      HTTPError("secret" + TOKEN, 401, "bad " + TOKEN, {}, None)])
def test_transport_errors_never_expose_credentials(monkeypatch, error):
    def fetch(*args, **kwargs):
        raise error
    monkeypatch.setattr(tg, "urlopen", fetch)
    monkeypatch.setattr(tg.time, "sleep", lambda _: None)
    with pytest.raises(tg.TelegramError) as result:
        tg.api(TOKEN, "sendMessage", {})
    assert TOKEN not in str(result.value)


def test_missing_secrets_are_explicit_and_safe(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert tg.main() == 0
    assert "not configured" in capsys.readouterr().out
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    assert tg.main() == 1
    assert TOKEN not in capsys.readouterr().out


def test_429_has_bounded_retry(monkeypatch):
    calls = []
    def fetch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise HTTPError("redacted", 429, "limited", {}, BytesIO(b'{"parameters":{"retry_after":1}}'))
        return BytesIO(b'{"ok":true,"result":{}}')
    monkeypatch.setattr(tg, "urlopen", fetch)
    monkeypatch.setattr(tg.time, "sleep", lambda _: None)
    tg.api(TOKEN, "sendMessage", {})
    assert len(calls) == 2


def test_test_message_does_not_pretend_to_publish(monkeypatch):
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("NOTIFICATION_TEST", "true")
    monkeypatch.setattr(tg, "api", lambda *args: sent.append(args[-1]))
    assert tg.main() == 0
    assert "连接测试" in sent[0]["text"]
    assert "更新完成" not in sent[0]["text"]


def test_long_report_preserves_run_link():
    changes = [{"provider": "x" * 180, "title": "y" * 180} for _ in range(30)]
    text = tg.message("success", {"updates": {key: changes for key in ("added", "changed", "missing", "restored", "expired")}},
                      "https://example.invalid", "https://github.com/repo/actions/runs/1")
    assert len(text) <= 3900
    assert text.endswith("https://github.com/repo/actions/runs/1")
