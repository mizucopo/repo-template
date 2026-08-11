#!/usr/bin/env bash
set -euo pipefail

lock_ref="refs/heads/automation/docker-release-lock"
lock_tracking_ref="refs/remotes/origin/automation/docker-release-lock"
lock_commit="$(
  printf 'Docker release lock\n\nrun-id: %s\n' "$GITHUB_RUN_ID" \
    | git -c user.name=github-actions -c user.email=github-actions@github.com \
      commit-tree "$(git rev-parse 'HEAD^{tree}')" -p "$GITHUB_SHA"
)"
poll_interval=30
wait_before_retry() {
  sleep "$poll_interval"
  poll_interval=$((poll_interval * 2))
  if [ "$poll_interval" -gt 300 ]; then
    poll_interval=300
  fi
}

missing_ref_retry=false
holder_response="$(mktemp)"
trap 'rm -f "$holder_response"' EXIT
while :; do
  set +e
  push_output="$(
    git push \
      --force-with-lease="$lock_ref:" \
      origin "${lock_commit}:$lock_ref" 2>&1
  )"
  push_status=$?
  set -e
  if [ "$push_status" -eq 0 ]; then
    break
  fi

  set +e
  git ls-remote --exit-code --refs origin "$lock_ref" >/dev/null
  lock_lookup_status=$?
  set -e
  case "$lock_lookup_status" in
    0)
      missing_ref_retry=false
      ;;
    2)
      if [ "$missing_ref_retry" = "false" ]; then
        missing_ref_retry=true
        continue
      fi
      printf '%s\n' "$push_output" >&2
      echo "The release lock ref is absent, but creating it was rejected." >&2
      exit "$push_status"
      ;;
    *)
      printf '%s\n' "$push_output" >&2
      echo "Could not inspect the release lock after a failed push." >&2
      exit "$lock_lookup_status"
      ;;
  esac

  if ! git fetch --force origin "$lock_ref:$lock_tracking_ref"; then
    wait_before_retry
    continue
  fi
  current_lock_commit="$(git rev-parse "$lock_tracking_ref")"
  holder_run_id="$(
    git show -s --format=%B "$current_lock_commit" \
      | sed -n 's/^run-id: //p'
  )"
  if [[ ! "$holder_run_id" =~ ^[0-9]+$ ]]; then
    echo "The release lock has no valid run id; refusing to replace it." >&2
    exit 1
  fi

  if [ "$holder_run_id" = "$GITHUB_RUN_ID" ]; then
    lock_commit="$current_lock_commit"
    break
  fi

  set +e
  holder_http_status="$(
    curl --silent --show-error --location \
      --output "$holder_response" \
      --write-out '%{http_code}' \
      --header "Accept: application/vnd.github+json" \
      --header "Authorization: Bearer $GH_TOKEN" \
      --header "X-GitHub-Api-Version: 2022-11-28" \
      "$GITHUB_API_URL/repos/$GITHUB_REPOSITORY/actions/runs/$holder_run_id"
  )"
  curl_status=$?
  set -e
  if [ "$curl_status" -ne 0 ]; then
    echo "Could not inspect release-lock holder run $holder_run_id." >&2
    exit "$curl_status"
  fi

  case "$holder_http_status" in
    200)
      holder_status="$(jq -er '.status' "$holder_response")"
      ;;
    404)
      holder_status=completed
      ;;
    *)
      echo "Could not inspect release-lock holder run $holder_run_id: HTTP $holder_http_status." >&2
      exit 1
      ;;
  esac
  if [ "$holder_status" = "completed" ]; then
    if git push \
      --force-with-lease="$lock_ref:$current_lock_commit" \
      origin "${lock_commit}:$lock_ref"; then
      break
    fi
  fi

  echo "Waiting for release lock held by run $holder_run_id."
  wait_before_retry
done

echo "lock_commit=$lock_commit" >> "$GITHUB_OUTPUT"
