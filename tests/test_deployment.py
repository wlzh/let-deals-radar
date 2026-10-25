"""Deployment gates must never overwrite newer source or accept stale HTML."""
from io import BytesIO
from pathlib import Path
import subprocess
from urllib.error import URLError

import pytest

from tools import verify_live


COMMIT_SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "commit_refresh.sh"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def refresh_repo(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    work = tmp_path / "work"
    git(tmp_path, "clone", str(remote), str(work))
    git(work, "checkout", "-b", "main")
    git(work, "config", "user.name", "Test")
    git(work, "config", "user.email", "test@example.invalid")
    for name in ("data/deals.json", "data/deals.md", "site/index.html", "source.py"):
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("initial\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "initial")
    git(work, "push", "-u", "origin", "main")
    return work, remote


def run_refresh(work):
    return subprocess.run(["bash", str(COMMIT_SCRIPT)], cwd=work, capture_output=True, text=True)


def test_refresh_commits_only_generated_files(refresh_repo):
    work, remote = refresh_repo
    (work / "site/index.html").write_text("fresh\n")
    (work / "source.py").write_text("unrelated local edit\n")
    assert run_refresh(work).returncode == 0
    assert git(remote, "show", "main:site/index.html") == "fresh"
    assert git(remote, "show", "main:source.py") == "initial"
    assert "source.py" in git(work, "status", "--short")


def test_refresh_no_change_is_success(refresh_repo):
    work, _ = refresh_repo
    before = git(work, "rev-parse", "HEAD")
    assert run_refresh(work).returncode == 0
    assert git(work, "rev-parse", "HEAD") == before


def test_refresh_rejects_new_remote_source(refresh_repo, tmp_path):
    work, remote = refresh_repo
    other = tmp_path / "other"
    git(tmp_path, "clone", "-b", "main", str(remote), str(other))
    (other / "source.py").write_text("new source\n")
    git(other, "add", ".")
    git(other, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "new source")
    git(other, "push", "origin", "main")
    (work / "site/index.html").write_text("stale build\n")
    result = run_refresh(work)
    assert result.returncode != 0
    assert "main changed" in result.stdout
    assert git(remote, "show", "main:source.py") == "new source"
    assert git(remote, "show", "main:site/index.html") == "initial"


def test_refresh_rejects_unexpected_staged_source(refresh_repo):
    work, remote = refresh_repo
    (work / "source.py").write_text("staged source\n")
    git(work, "add", "source.py")
    assert run_refresh(work).returncode != 0
    assert git(remote, "show", "main:source.py") == "initial"


@pytest.mark.parametrize("mode", ["match", "stale", "unreachable"])
def test_live_check_compares_all_artifacts(tmp_path, monkeypatch, mode):
    for name in verify_live.FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fresh")
    calls = []

    def fetch(request, timeout):
        calls.append(request.full_url)
        if mode == "unreachable":
            raise URLError("unreachable")
        return BytesIO(b"fresh" if mode == "match" else b"old")

    monkeypatch.setattr(verify_live, "urlopen", fetch)
    errors = verify_live.verify_once("https://example.invalid/", tmp_path)
    assert len(calls) == 3
    assert len(errors) == (0 if mode == "match" else 3)
