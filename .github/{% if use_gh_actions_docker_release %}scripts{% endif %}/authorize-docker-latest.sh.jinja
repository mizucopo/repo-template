#!/usr/bin/env bash
set -euo pipefail

latest_ref="refs/heads/automation/docker-latest-run"
latest_tracking_ref="refs/remotes/origin/automation/docker-latest-run"
latest_commit=""
latest_run_number=0
set +e
git ls-remote --exit-code --refs origin "$latest_ref" >/dev/null
latest_lookup_status=$?
set -e
case "$latest_lookup_status" in
  0)
    git fetch --force origin "$latest_ref:$latest_tracking_ref"
    latest_commit="$(git rev-parse "$latest_tracking_ref")"
    latest_run_number="$(
      git show -s --format=%B "$latest_commit" \
        | sed -n 's/^run-number: //p'
    )"
    if [[ ! "$latest_run_number" =~ ^[0-9]+$ ]]; then
      echo "The latest-run marker has no valid run number." >&2
      exit 1
    fi
    ;;
  2) ;;
  *)
    echo "Could not inspect the latest-run marker." >&2
    exit 1
    ;;
esac

publish_latest=false
if [ "$latest_run_number" -le "$GITHUB_RUN_NUMBER" ]; then
  next_latest_commit="$(
    printf 'Docker latest marker\n\nrun-number: %s\n' "$GITHUB_RUN_NUMBER" \
      | git -c user.name=github-actions -c user.email=github-actions@github.com \
        commit-tree "$(git rev-parse 'HEAD^{tree}')" -p "$GITHUB_SHA"
  )"
  git push \
    --force-with-lease="$latest_ref:$latest_commit" \
    origin "${next_latest_commit}:$latest_ref"
  publish_latest=true
fi

echo "publish_latest=$publish_latest" >> "$GITHUB_OUTPUT"
