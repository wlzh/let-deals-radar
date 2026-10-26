import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from tools import telegram_notify as tg


TOKEN = "123456:" + "x" * 32


def test_success_message_is_bounded_and_has_changes():
    data = {"count": 82, "updates": {"added": [{"provider": "X", "title": "new\nactivity"}]}}
    text = tg.message("success", data, "https://example.invalid", "https://github.com/run")
    assert "新增 1" in text and "活动详情" in text
    assert "new activity" not in text
    assert "优惠信息更新" in text
    assert len(text) <= 3900


def test_failure_never_announces_success():
    text = tg.message("failure", {}, "site", "run", "live=failure")
    assert "异常" in text and "网页验收：失败" in text and "完成" not in text


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
    monkeypatch.delenv("TELEGRAM_ALERT_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_TARGETS", raising=False)
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
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", "1")
    monkeypatch.setenv("NOTIFICATION_TEST", "true")
    def respond(token, method, body):
        if method == "getMe":
            return {"id": 999}
        if method == "getChat":
            return {"type": "private"}
        sent.append(body)
        return {}
    monkeypatch.setattr(tg, "api", respond)
    assert tg.main() == 0
    assert "连接测试" in sent[0]["text"]
    assert "更新完成" not in sent[0]["text"]


def test_long_report_preserves_run_link():
    changes = [{"provider": "x" * 180, "title": "y" * 180} for _ in range(30)]
    text = tg.message("success", {"updates": {key: changes for key in ("added", "changed", "missing", "restored", "expired")}},
                      "https://example.invalid", "https://github.com/repo/actions/runs/1")
    assert len(text) <= 3900
    assert text.endswith("https://github.com/repo/actions/runs/1")


def test_failure_only_goes_to_private_never_loads_public_targets(monkeypatch):
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", "123")
    monkeypatch.setenv("TELEGRAM_TARGETS", "malformed but must not be touched on failure")
    monkeypatch.setenv("REFRESH_STATUS", "failure")
    monkeypatch.delenv("NOTIFICATION_TEST", raising=False)
    def respond(token, method, body):
        if method == "getMe":
            return {"id": 999}
        if method == "getChat":
            return {"type": "private"}
        sent.append(body)
        return {}
    monkeypatch.setattr(tg, "api", respond)
    assert tg.main() == 0
    assert len(sent) == 1 and sent[0]["chat_id"] == "123"
    assert "异常" in sent[0]["text"]


def test_private_alert_rejects_groups_and_channels(monkeypatch):
    monkeypatch.setattr(tg, "api", lambda *a: {"type": "supergroup"})
    for chat in ("-100123", "@publicchannel", "123"):
        with pytest.raises(tg.TelegramError):
            tg.private_alert(TOKEN, chat, "error")


def setup_public(monkeypatch, tmp_path, report):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "site/data"
    path.mkdir(parents=True)
    (path / "deals.json").write_text(json.dumps({"updates": report}))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", "123")
    monkeypatch.setenv("TELEGRAM_TARGETS", json.dumps([{"chat_id": str(-1000-i), "message_thread_id": 10+i} for i in range(6)]))
    monkeypatch.setenv("REFRESH_STATUS", "success")
    monkeypatch.delenv("NOTIFICATION_TEST", raising=False)


def test_changes_send_to_all_six_topics_without_error_diagnostics(monkeypatch, tmp_path):
    setup_public(monkeypatch, tmp_path, {"added": [{"provider": "X", "title": "sale"}]})
    sent = []
    monkeypatch.setattr(tg, "api", lambda token, method, body: sent.append(body) or {})
    assert tg.main() == 0
    assert len(sent) == 6
    assert all("message_thread_id" in body and "异常" not in body["text"] for body in sent)
    assert all(body["chat_id"] != "123" for body in sent)
    assert len(json.loads((tmp_path / "telegram-delivery.json").read_text())["destinations"]) == 6


def test_no_changes_do_not_spam_public_targets(monkeypatch, tmp_path):
    setup_public(monkeypatch, tmp_path, {})
    def unexpected(*args):
        raise AssertionError("No messages should be sent")
    monkeypatch.setattr(tg, "api", unexpected)
    assert tg.main() == 0


def test_partial_public_failure_reports_only_to_private(monkeypatch, tmp_path):
    setup_public(monkeypatch, tmp_path, {"changed": [{"provider": "X", "title": "price"}]})
    sent = []
    def respond(token, method, body):
        if method == "getMe":
            return {"id": 999}
        if method == "getChat":
            return {"type": "private"}
        if body["chat_id"] == "-1001":
            raise tg.TelegramError("Telegram HTTP 403")
        sent.append(body)
        return {}
    monkeypatch.setattr(tg, "api", respond)
    assert tg.main() == 1
    diagnostics = [body for body in sent if "发送失败" in body["text"]]
    assert len(diagnostics) == 1 and diagnostics[0]["chat_id"] == "123"
    assert len([body for body in sent if body["chat_id"] != "123"]) == 5


def test_alert_rejects_own_bot_id_before_sending(monkeypatch):
    calls = []
    def respond(token, method, body):
        calls.append(method)
        return {"id": 123}
    monkeypatch.setattr(tg, "api", respond)
    with pytest.raises(tg.TelegramError, match="BOT itself"):
        tg.private_alert(TOKEN, "123", "error")
    assert calls == ["getMe"]


def test_403_reason_is_useful_but_redacted(monkeypatch):
    body = json.dumps({"description": "Forbidden: bot cannot send messages to bots " + TOKEN}).encode()
    def fetch(*args, **kwargs):
        raise HTTPError("secret" + TOKEN, 403, "Forbidden", {}, BytesIO(body))
    monkeypatch.setattr(tg, "urlopen", fetch)
    with pytest.raises(tg.TelegramError) as result:
        tg.api(TOKEN, "sendMessage", {})
    assert "personal user ID" in str(result.value)
    assert TOKEN not in str(result.value)


def test_private_statuses_and_error_hints_are_chinese():
    assert tg.stage_text("timer=success;refresh=failure;continuation=skipped") == "等待计时：成功；优惠更新：失败；每日接续：未执行"
    assert "私聊" in tg.error_hint(tg.TelegramError("recipient must start a private conversation"))
