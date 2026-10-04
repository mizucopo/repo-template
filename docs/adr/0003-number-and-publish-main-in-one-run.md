# Number and publish main in one Actions run

Normal read-only CI precedes squash merge. One serialized release run batches pending PRs, atomically pushes main version metadata and the same commit's tag with GITHUB_TOKEN, then builds, validates and publishes that commit. Release commit trailers replace the signed PR plan and identify reruns. Dedicated merge preparation, CI admission, App authentication, bootstrap and dependency auto-classification are removed. Destination settings must permit direct main/tag pushes. Existing published objects remain immutable.
