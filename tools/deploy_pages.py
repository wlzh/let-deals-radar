"""Upload audited static artifacts; never print credentials or raw CLI logs."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ACCOUNT = "cba95940715cce032368055d71c9f548"
PROJECT = "vps-869hr"
RETRY_DELAYS = (60, 180, 300)


def retryable(output):
    text = output.lower()
    if any(s in text for s in ("authentication", "unauthorized", "forbidden", "permission", "10000", "invalid api token")):
        return False
    return any(s in text for s in ("econnreset", "etimedout", "eai_again", "fetch failed", "network error", "socket hang up", "502 bad gateway", "503 service unavailable", "504 gateway timeout"))


def assert_current():
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    remote = subprocess.check_output(["git", "ls-remote", "origin", "refs/heads/main"], text=True, timeout=60).split()
    if not remote or remote[0] != sha:
        raise RuntimeError("Remote main changed; refusing stale deployment")
    if subprocess.check_output(["git", "status", "--porcelain", "--", "site"], text=True).strip():
        raise RuntimeError("Site differs from committed artifacts")
    return sha


def deploy(binary, source=Path("site")):
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not token:
        raise RuntimeError("CLOUDFLARE_API_TOKEN is missing")
    sha = assert_current()
    # No repository config, functions, credentials or symlinks enter the upload directory.
    if any(p.is_symlink() for p in source.rglob("*")):
        raise RuntimeError("Symlinks are not allowed in site artifacts")
    for path in source.rglob("*"):
        if path.is_file() and (any(part.startswith(".") for part in path.relative_to(source).parts)
                               or token.encode() in path.read_bytes()):
            raise RuntimeError("Sensitive or hidden content detected in site; upload refused")
    env = {**os.environ, "CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "WRANGLER_SEND_METRICS": "false"}
    with tempfile.TemporaryDirectory(prefix="ldr-pages-") as directory:
        shutil.copytree(source, Path(directory) / "site")
        for attempt in range(4):
            if assert_current() != sha:
                raise RuntimeError("Revision changed during deployment")
            try:
                result = subprocess.run(
                    [str(Path(binary).resolve()), "pages", "deploy", "site", "--project-name", PROJECT,
                     "--branch", "main", "--commit-hash", sha, "--commit-dirty=false"],
                    cwd=directory, env=env, capture_output=True, text=True, timeout=300,
                )
            except subprocess.TimeoutExpired:
                # An ambiguous upload may already have published; do not submit it again blindly.
                raise RuntimeError("Upload timed out; inspect Pages before retrying") from None
            if result.returncode == 0:
                print(f"Pages upload accepted: project={PROJECT}, revision={sha}, attempts={attempt + 1}", flush=True)
                return
            if attempt == 3 or not retryable(result.stdout + result.stderr):
                raise RuntimeError("Pages upload failed; non-retryable error or retry budget exhausted (CLI output withheld)")
            print(f"Transient upload error; retry {attempt + 1}/3 in {RETRY_DELAYS[attempt]}s", flush=True)
            time.sleep(RETRY_DELAYS[attempt])


if __name__ == "__main__":
    try:
        deploy(os.environ["WRANGLER_BIN"])
    except (RuntimeError, OSError, subprocess.SubprocessError, KeyError) as exc:
        # Exception strings from third-party processes may contain credentials.
        message = str(exc) if isinstance(exc, RuntimeError) else "Deployment process failed; sensitive details withheld"
        print(f"::error::{message}", flush=True)
        raise SystemExit(1)
