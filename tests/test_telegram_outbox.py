from copy import deepcopy
import json
import subprocess

import pytest

from tools import telegram_outbox as outbox
from tools.telegram_notify import TelegramError, AmbiguousTelegramError
from tools import telegram_notify as tg
from urllib.error import URLError


class MemoryStore:
    def __init__(self):
        self.saved = []

    def save(self, state):
        self.saved.append(deepcopy(state))


def state():
    return {"version": 1, "baseline": [], "events": {}, "targets": {}}


def payload(day="2026-10-28"):
    return {"updates": {"generated_at": day + "T10:00:00Z", "added": [{"id": "x", "title": "deal"}]}}


TARGETS = [{"chat_id": "-100123", "message_thread_id": 1}, {"chat_id": "-100456"}]


def deliver(store, ledger, data, send):
    return outbox.deliver(store, ledger, data, TARGETS, "private-token", send, lambda p: json.dumps(p),
                          AmbiguousTelegramError, TelegramError)


def test_partial_failure_retries_only_failed_target():
    store, ledger, calls = MemoryStore(), state(), []
    def send(token, target, text, **kw):
        calls.append(target)
        if target == TARGETS[1]:
            raise TelegramError("rejected")
        return {"message_id": 1}
    first = deliver(store, ledger, payload(), send)
    assert [r["status"] for r in first] == ["delivered", "failed"]
    calls.clear()
    second = deliver(store, ledger, {}, lambda token, target, *a, **kw: calls.append(target) or {"message_id": 2})
    assert calls == [TARGETS[1]]
    assert [r["status"] for r in second] == ["already-delivered", "delivered"]
    assert "-100123" not in json.dumps(ledger) and "private-token" not in json.dumps(ledger)


def test_pending_survives_empty_history_and_next_day():
    ledger = state()
    outbox.ingest(ledger, payload())
    assert len(ledger["events"]) == 1
    calls = []
    deliver(MemoryStore(), ledger, {"generated_at": "2026-10-29"}, lambda *a, **kw: calls.append(1) or {})
    assert len(calls) == 2


def test_ambiguous_send_is_not_automatically_duplicated():
    ledger, store = state(), MemoryStore()
    def unknown(*a, **kw):
        raise AmbiguousTelegramError("lost response")
    assert all(r["status"] == "uncertain" for r in deliver(store, ledger, payload(), unknown))
    def forbidden(*a, **kw):
        pytest.fail("must not resend uncertain message")
    assert all(r["status"] == "uncertain" for r in deliver(store, ledger, {}, forbidden))


def test_claim_is_saved_before_external_send():
    store, ledger = MemoryStore(), state()
    def send(*a, **kw):
        assert any("sending" in x.values() for x in store.saved[-1]["targets"].values())
        return {}
    deliver(store, ledger, payload(), send)


def test_failed_claim_persistence_prevents_send():
    store, ledger = MemoryStore(), state()
    def save(value):
        if value["targets"]:
            raise RuntimeError("conflict")
    store.save = save
    with pytest.raises(RuntimeError):
        deliver(store, ledger, payload(), lambda *a, **kw: pytest.fail("send forbidden"))


def test_baseline_prevents_migration_spam():
    ledger = state()
    ledger["baseline"] = list(outbox.events(payload()))
    receipts = deliver(MemoryStore(), ledger, payload(), lambda *a, **kw: pytest.fail("baseline already published"))
    assert all(r["status"] == "already-delivered" for r in receipts)


def test_credential_rotation_fails_closed():
    ledger = state()
    ledger["binding"] = "different-key"
    with pytest.raises(RuntimeError, match="credential changed"):
        deliver(MemoryStore(), ledger, payload(), lambda *a: None)


def test_timestamp_only_change_does_not_republish():
    a, b = payload(), payload()
    b["updates"]["generated_at"] = "2026-10-28T11:00:00Z"
    assert outbox.events(a) == outbox.events(b)


def test_unexpected_sender_error_leaves_uncertain_claim():
    ledger = state()
    def crash(*a, **kw):
        raise ValueError("unexpected")
    with pytest.raises(ValueError):
        deliver(MemoryStore(), ledger, payload(), crash)
    assert any("sending" in x.values() for x in ledger["targets"].values())


def test_telegram_network_ambiguity_is_not_retried(monkeypatch):
    calls = []
    def fetch(*a, **kw):
        calls.append(1)
        raise URLError("lost response")
    monkeypatch.setattr(tg, "urlopen", fetch)
    with pytest.raises(AmbiguousTelegramError):
        tg.api("123456:" + "x" * 32, "sendMessage", {})
    assert len(calls) == 1


@pytest.mark.parametrize("same_content", [False, True])
def test_git_store_cas_rejects_concurrent_writer(tmp_path, monkeypatch, same_content):
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True)
    monkeypatch.chdir(work)
    (work / "deploy").mkdir()
    (work / "deploy/telegram-baseline.json").write_text("[]")
    monkeypatch.setenv("TELEGRAM_OUTBOX_BOOTSTRAP", "true")
    first, second = outbox.Store(), outbox.Store()
    a, b = first.load(), second.load()
    a["events"]["one"] = {}
    b["events"]["one" if same_content else "two"] = {}
    first.save(a)
    with pytest.raises(RuntimeError):
        second.save(b)
    assert outbox.Store().load()["events"] == {"one": {}}
