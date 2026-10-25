#!/usr/bin/env bash
# Do not transplant an old build onto newer code or force-push generated data.
set -euo pipefail

BASE="${BUILD_SOURCE_SHA:-$(git rev-parse HEAD)}"
git fetch origin main
if [ "$(git rev-parse origin/main)" != "$BASE" ]; then
  echo '::error::main changed during this build. Rerun against latest main; no changes pushed.'
  exit 1
fi
if ! git diff --cached --quiet; then
  echo '::error::Unexpected staged changes; refusing to commit.'
  exit 1
fi

git add data/deals.json data/deals.md site/
if [ -f data/forum_posted.json ]; then
  git add data/forum_posted.json
fi
if git diff --cached --quiet; then
  echo 'No generated changes to commit.'
  exit 0
fi
git -c user.name='github-actions[bot]' \
    -c user.email='github-actions[bot]@users.noreply.github.com' \
    commit -m 'chore: refresh deals'
# A concurrent update after the fetch is still protected by fast-forward push.
git push origin HEAD:main
