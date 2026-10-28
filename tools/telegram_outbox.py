"""Durable public-data queue with opaque destination keys and CAS send claims."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess

CATEGORIES = ("added", "changed", "missing", "restored", "expired")
REF = "refs/heads/telegram-outbox"


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def events(payload):
    reports = payload.get("update_history") or [payload.get("updates", {})]
    result = {}
    for report in reports:
        from letdeals.updates import display_time
        day = display_time(report.get("generated_at"))[:10]
        for category in CATEGORIES:
            for item in report.get(category, []):
                value = {"day": day, "category": category, "item": item}
                key = hashlib.sha256(encode(value).encode()).hexdigest()
                result[key] = value
    return result


class Store:
    def git(self, *args, data=None):
        result = subprocess.run(["git", *args], input=data, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError("Publication state operation failed; no unsafe resend attempted")
        return result.stdout.strip()

    def load(self):
        remote = self.git("ls-remote", "origin", REF)
        self.parent = remote.split()[0] if remote else None
        if self.parent:
            self.git("fetch", "--quiet", "origin", REF)
            self.parent = self.git("rev-parse", "FETCH_HEAD")
            state = json.loads(self.git("show", f"{self.parent}:state.json"))
            if state.get("version") != 1:
                raise RuntimeError("Unsupported publication state")
            return state
        if os.environ.get("TELEGRAM_OUTBOX_BOOTSTRAP") != "true":
            raise RuntimeError("Publication ledger missing; explicit bootstrap required")
        baseline = json.loads(Path("deploy/telegram-baseline.json").read_text())
        return {"version": 1, "baseline": baseline, "events": {}, "targets": {}}

    def save(self, state):
        blob = self.git("hash-object", "-w", "--stdin", data=encode(state))
        tree = self.git("mktree", data=f"100644 blob {blob}\tstate.json\n")
        args = ["-c", "user.name=github-actions[bot]", "-c", "user.email=github-actions[bot]@users.noreply.github.com",
                "commit-tree", tree, "-m", "Update publication delivery state"]
        if self.parent:
            args += ["-p", self.parent]
        commit = self.git(*args)
        # Non-fast-forward push is the lock: concurrent runners cannot both claim a send.
        self.git("push", "--quiet", "origin", f"{commit}:{REF}")
        self.parent = commit


def ingest(state, payload):
    for key, value in events(payload).items():
        if key not in state["baseline"]:
            state["events"].setdefault(key, value)


def deliver(store, state, payload, targets, token, send, render, ambiguous, rejected):
    binding = hmac.new(token.encode(), b"outbox-identity-v1", hashlib.sha256).hexdigest()
    if state.get("binding", binding) != binding:
        raise RuntimeError("Bot credential changed; migrate delivery identity before publishing")
    state["binding"] = binding
    ingest(state, payload)
    store.save(state)
    receipts = []
    for index, target in enumerate(targets, 1):
        identity = encode([str(target["chat_id"]), target.get("message_thread_id")])
        key = hmac.new(token.encode(), identity.encode(), hashlib.sha256).hexdigest()
        ledger = state["targets"].setdefault(key, {})
        if any(value == "sending" for value in ledger.values()):
            receipts.append({"target_index": index, "status": "uncertain"})
            continue
        pending = [event for event in state["events"] if ledger.get(event) != "delivered"]
        if not pending:
            receipts.append({"target_index": index, "status": "already-delivered"})
            continue
        # One bounded digest per target; all counts remain visible even if detail is truncated.
        report = {category: [] for category in CATEGORIES}
        for event in pending:
            value = state["events"][event]
            report[value["category"]].append(value["item"])
        text = render({**payload, "updates": report})
        for event in pending:
            ledger[event] = "sending"
        store.save(state)
        try:
            result = send(token, target, text, formatted=True)
        except ambiguous:
            receipts.append({"target_index": index, "status": "uncertain"})
            continue
        except rejected:
            # Only explicit Telegram rejections are retryable. Caller supplies a typed sender.
            for event in pending:
                ledger[event] = "failed"
            store.save(state)
            receipts.append({"target_index": index, "status": "failed"})
            continue
        for event in pending:
            ledger[event] = "delivered"
        store.save(state)
        receipts.append({"target_index": index, "status": "delivered", "message_id": result.get("message_id")})
    return receipts


def main():
    payload = json.loads(Path("site/data/deals.json").read_text())
    store = Store()
    state = store.load()
    ingest(state, payload)
    store.save(state)
    print(f"Durable publication queue saved: {len(state['events'])} events (no destination identifiers)")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["baseline"]:
        payload = json.loads(Path("site/data/deals.json").read_text())
        Path("deploy/telegram-baseline.json").write_text(encode(sorted(events(payload))) + "\n")
    else:
        try:
            main()
        except Exception:
            print("::error::Publication queue could not be persisted; inspect state before retrying")
            raise SystemExit(1)
