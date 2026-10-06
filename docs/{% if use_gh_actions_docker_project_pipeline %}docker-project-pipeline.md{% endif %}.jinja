# Project Docker image pipeline

Select use_docker, use_version_management and use_gh_actions_docker_project_pipeline. Set docker_registry, docker_image_name and DOCKERHUB_TOKEN. This Docker Hub pipeline is exclusive with other publication workflows and the standard Docker quality workflow. Setup, numbering and recovery follow [release.md](release.md).

## Declaration

.github/release.json declares version sources and publication. Ordered images each have a unique name and immutable tag. latest_image selects a listed image, or null disables promotion. Mutable aliases do not participate in number collisions. release_paths describes inputs; it does not suppress publication of classified docs/runtime changes.

The generated release_paths contains the selected runtime's version sources. Add project-owned Dockerfiles and hooks, and a revision file when declaring an upstream-revision version scheme.

For an upstream version with revision and two images, set version.scheme to upstream-revision, revision_path to revision and publication as follows:

```json
{
  "release_tag": "{version}{revision_suffix}",
  "github_release": true,
  "images": [
    {
      "name": "base",
      "repository": "owner/worker",
      "registry": "dockerhub",
      "tag": "{version}-base{revision_suffix}",
      "aliases": []
    },
    {
      "name": "process",
      "repository": "owner/worker",
      "registry": "dockerhub",
      "tag": "{version}-process{revision_suffix}",
      "aliases": []
    }
  ],
  "latest_image": null,
  "release_paths": [
    "version",
    "revision",
    "images/base/Dockerfile",
    "images/process/Dockerfile",
    ".github/scripts/docker-image-project.sh"
  ]
}
```

## Project hook

Add .github/scripts/docker-image-project.sh. It remains project-owned. The current PR hook is used by read-only PR CI; the exact numbering commit's hook is used for release.

| Operation              | Behavior                                                                                        |
| ---------------------- | ----------------------------------------------------------------------------------------------- |
| quality                | Build checks, local build and smoke tests; nonzero on failure. No publication secrets in PR CI. |
| publish NAME IMAGE_REF | Build/push the named image to the supplied reference. Follow declaration order.                 |
| notes                  | Write nonempty release notes to stdout.                                                         |

IMAGE_REPOSITORY is namespace/repository. DOCKER_RELEASE_PLAN points to the resolved JSON containing release_tag, images, latest_image and release_paths. Hooks do not number versions. Example n8n/worker hooks in docs/examples show build arguments and dependent images.

The numbering tag exists before building. Same-commit image ownership markers and registry digests permit partial publication to resume; unknown ownership stops publication. Markers remain after completion for future reruns. A public Release with missing images is an inconsistency, not a request to replace published content.
