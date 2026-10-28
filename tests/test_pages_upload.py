from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from tools import deploy_pages as pages


@pytest.mark.parametrize("message,expected", [
    ("fetch failed", True), ("ECONNRESET", True), ("ETIMEDOUT", True),
    ("Authentication failed: fetch failed", False), ("permission denied", False),
    ("invalid config", False), ("503 Service Unavailable", True),
])
def test_retry_classification(message, expected):
    assert pages.retryable(message) is expected


@pytest.fixture
def upload(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("static")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "secret-test-token")
    monkeypatch.setattr(pages, "assert_current", lambda: "a" * 40)
    delays = []
    monkeypatch.setattr(pages.time, "sleep", delays.append)
    return site, delays


def test_success_after_network_failure(upload, monkeypatch, capsys):
    site, delays = upload
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert command[2:4] == ["deploy", "site"]
        assert command[command.index("--project-name") + 1] == "vps-869hr"
        assert list(Path(kwargs["cwd"]).iterdir()) == [Path(kwargs["cwd"]) / "site"]
        assert kwargs["env"]["CLOUDFLARE_ACCOUNT_ID"] == pages.ACCOUNT
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0,
                               stdout="secret-test-token", stderr="fetch failed")
    monkeypatch.setattr(pages.subprocess, "run", run)
    pages.deploy("/bin/false", site)
    assert delays == [60]
    assert "secret-test-token" not in capsys.readouterr().out


def test_exhausted_retries(upload, monkeypatch):
    site, delays = upload
    monkeypatch.setattr(pages.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="ECONNRESET"))
    with pytest.raises(RuntimeError, match="exhausted"):
        pages.deploy("/bin/false", site)
    assert delays == [60, 180, 300]


def test_no_retry_for_auth_error(upload, monkeypatch):
    site, delays = upload
    monkeypatch.setattr(pages.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="Authentication failed"))
    with pytest.raises(RuntimeError):
        pages.deploy("/bin/false", site)
    assert delays == []


def test_no_retry_for_ambiguous_timeout(upload, monkeypatch):
    site, delays = upload
    def run(*a, **kw):
        raise subprocess.TimeoutExpired("wrangler", 300)
    monkeypatch.setattr(pages.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="timed out"):
        pages.deploy("/bin/false", site)
    assert not delays


def test_missing_token_fails_before_upload(upload, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN")
    with pytest.raises(RuntimeError, match="missing"):
        pages.deploy("/bin/false", upload[0])


def test_reject_symlinks(upload):
    site, _ = upload
    (site / "secret").symlink_to("/etc/passwd")
    with pytest.raises(RuntimeError, match="Symlinks"):
        pages.deploy("/bin/false", site)


@pytest.mark.parametrize("filename,content", [("index.html", "secret-test-token"), (".env", "private")])
def test_reject_sensitive_artifacts(upload, filename, content):
    site, _ = upload
    (site / filename).write_text(content)
    with pytest.raises(RuntimeError, match="Sensitive"):
        pages.deploy("/bin/false", site)


def test_stop_when_revision_changes(upload, monkeypatch):
    values = iter(["a" * 40, "b" * 40])
    monkeypatch.setattr(pages, "assert_current", lambda: next(values))
    with pytest.raises(RuntimeError, match="Revision changed"):
        pages.deploy("/bin/false", upload[0])
