#!/usr/bin/env bash
set -euo pipefail

read_tags() {
  local raw_version raw_revision
  raw_version="$(cat version)"
  raw_revision=""
  if [ -f revision ]; then
    raw_revision="$(cat revision)"
  fi
  case "$raw_version:$raw_revision" in
    *$'\n'*) echo "version and revision must each contain one line." >&2; exit 1 ;;
  esac
  version="$(printf '%s' "$raw_version" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  revision="$(printf '%s' "$raw_revision" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  if [[ ! "$version" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || [ "$version" = latest ]; then
    echo "version must be a nonempty immutable Docker tag." >&2
    exit 1
  fi
  if [ -n "$revision" ] && [[ ! "$revision" =~ ^r[0-9]+$ ]]; then
    echo "revision must be empty or match r[0-9]+." >&2
    exit 1
  fi
}

case "$1" in
  quality)
    read_tags
    shellcheck .github/scripts/*.sh
    docker buildx build --check --build-arg "N8N_VERSION=$version" .
    docker build --build-arg "N8N_VERSION=$version" --tag n8n-extended:pr .
    docker run --rm --entrypoint sh n8n-extended:pr -eu -c \
      'docker --version && ffmpeg -version && n8n --version'
    ;;
  publish)
    [ "$2" = extended ] || { echo "Unknown image: $2" >&2; exit 2; }
    read_tags
    docker build --build-arg "N8N_VERSION=$version" \
      --platform linux/amd64 --tag "$3" .
    docker push "$3"
    ;;
  notes)
    image_tag="$(jq -r '.images[0].tag' "$DOCKER_RELEASE_PLAN")"
    printf '## Docker Image\n\nこの GitHub Release に対応する Extended Image です。\n\n'
    printf -- "- Extended Image: \`%s:%s\`\n\n" "$IMAGE_REPOSITORY" "$image_tag"
    printf 'Docker Hub: https://hub.docker.com/r/%s\n' "$IMAGE_REPOSITORY"
    ;;
  *)
    echo "Usage: $0 {quality|publish|notes}" >&2
    exit 2
    ;;
esac
