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
  if [[ ! "$version" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || [[ "$version" == prefect-* ]] || [ "$version" = latest ]; then
    echo "version must be an upstream Prefect tag without a prefect- prefix." >&2
    exit 1
  fi
  if [ -n "$revision" ] && [[ ! "$revision" =~ ^r[0-9]+$ ]]; then
    echo "revision must be empty or match r[0-9]+." >&2
    exit 1
  fi
  release_tag="$version"
  base_tag="$version-base"
  process_tag="$version-process"
  if [ -n "$revision" ]; then
    release_tag="$release_tag-$revision"
    base_tag="$base_tag-$revision"
    process_tag="$process_tag-$revision"
  fi
}

case "$1" in
  quality)
    read_tags
    shellcheck scripts/*.sh tests/*.sh .github/scripts/*.sh
    bash "tests/resolve-worker-image-tags.sh"
    docker buildx build --check \
      --build-arg "PREFECT_IMAGE_TAG=$version" \
      --file images/base/Dockerfile .
    docker buildx build --builder default --load \
      --build-arg "PREFECT_IMAGE_TAG=$version" \
      --file images/base/Dockerfile \
      --tag prefect-worker-base:pr .
    bash "tests/base-worker-image-postgresql-clients.sh"
    docker buildx build --builder default --load \
      --build-arg BASE_IMAGE=prefect-worker-base:pr \
      --file images/process/Dockerfile \
      --tag prefect-worker-process:pr .
    docker run --rm --entrypoint docker prefect-worker-process:pr --version
    ;;
  publish)
    read_tags
    case "$2" in
      base)
        docker build --build-arg "PREFECT_IMAGE_TAG=$version" \
          --tag "$3" --file images/base/Dockerfile .
        ;;
      process)
        published_base_tag="$(jq -r '.images[] | select(.name == "base").tag' "$DOCKER_RELEASE_PLAN")"
        docker build --build-arg "BASE_IMAGE=$IMAGE_REPOSITORY:$published_base_tag" \
          --tag "$3" --file images/process/Dockerfile .
        ;;
      *)
        echo "Unknown image: $2" >&2
        exit 2
        ;;
    esac
    docker push "$3"
    ;;
  notes)
    published_base_tag="$(jq -r '.images[] | select(.name == "base").tag' "$DOCKER_RELEASE_PLAN")"
    published_process_tag="$(jq -r '.images[] | select(.name == "process").tag' "$DOCKER_RELEASE_PLAN")"
    printf '## Worker Images\n\nこの GitHub Release に対応する Worker Image です。\n\n'
    printf -- "- Base Worker Image: \`%s:%s\`\n- Process Worker Image: \`%s:%s\`\n\n" \
      "$IMAGE_REPOSITORY" "$published_base_tag" \
      "$IMAGE_REPOSITORY" "$published_process_tag"
    printf 'Docker Hub: https://hub.docker.com/r/%s\n' "$IMAGE_REPOSITORY"
    ;;
  *)
    echo "Usage: $0 {quality|publish|notes}" >&2
    exit 2
    ;;
esac
