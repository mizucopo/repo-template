#!/usr/bin/env bash
set -euo pipefail

latest_ref="refs/heads/automation/docker-latest"
latest_tracking_ref="refs/remotes/origin/automation/docker-latest"
latest_commit=""
latest_source_sha=""
set +e
git ls-remote --exit-code --refs origin "$latest_ref" >/dev/null
latest_lookup_status=$?
set -e
case "$latest_lookup_status" in
  0)
    git fetch --force origin "$latest_ref:$latest_tracking_ref"
    latest_commit="$(git rev-parse "$latest_tracking_ref")"
    latest_source_sha="$(
      git show -s --format=%B "$latest_commit" \
        | sed -n 's/^source-sha: //p'
    )"
    if [[ ! "$latest_source_sha" =~ ^[0-9a-f]{40}$ ]]; then
      echo "The latest marker has no valid source commit." >&2
      exit 1
    fi
    ;;
  2) ;;
  *)
    echo "Could not inspect the latest marker." >&2
    exit 1
    ;;
esac

operation="${1:-check}"
case "$operation" in
  check)
    publish_latest=false
    if [ -z "$latest_source_sha" ] || [ "$latest_source_sha" = "$GITHUB_SHA" ]; then
      publish_latest=true
    elif git merge-base --is-ancestor "$latest_source_sha" "$GITHUB_SHA"; then
      publish_latest=true
    elif ! git merge-base --is-ancestor "$GITHUB_SHA" "$latest_source_sha"; then
      echo "The latest marker is not on the current release history." >&2
      exit 1
    fi
    echo "publish_latest=$publish_latest" >> "$GITHUB_OUTPUT"
    ;;
  record)
    if [ "$latest_source_sha" = "$GITHUB_SHA" ]; then
      exit 0
    fi
    if [ -n "$latest_source_sha" ] \
      && ! git merge-base --is-ancestor "$latest_source_sha" "$GITHUB_SHA"; then
      echo "Refusing to replace a newer or divergent latest marker." >&2
      exit 1
    fi
    next_latest_commit="$(
      printf 'Docker latest marker\n\nsource-sha: %s\n' "$GITHUB_SHA" \
        | git -c user.name=github-actions -c user.email=github-actions@github.com \
          commit-tree "$(git rev-parse 'HEAD^{tree}')" -p "$GITHUB_SHA"
    )"
    git push \
      --force-with-lease="$latest_ref:$latest_commit" \
      origin "${next_latest_commit}:$latest_ref"
    ;;
  *)
    echo "Usage: $0 {check|record}" >&2
    exit 2
    ;;
esac
