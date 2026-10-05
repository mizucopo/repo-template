# GitHub merge histories

`github_merge_histories.json` contains projections of public GitHub REST responses
captured on 2026-10-05 with API version `2026-03-10`. Tests run offline.
Each history entry also contains its GraphQL `associatedPullRequests` response,
captured on the same date through the generated batching client. GraphQL is not
versioned by that REST API header.

| Case | Source | Observed history |
| --- | --- | --- |
| `squash` | [repo-template PR #147](https://github.com/mizucopo/repo-template/pull/147) | Three original PR commits, one main commit. |
| `squash_after_base_advanced` | [TypeScript PR #64598](https://github.com/microsoft/TypeScript/pull/64598) | Three original PR commits, one squash commit after two unrelated main commits following the recorded base. |
| `rebase` | [Zulip PR #40288](https://github.com/zulip/zulip/pull/40288) | Two rewritten PR commits on main after four unrelated commits following the recorded base; the merge event identifies the last rewritten commit. |
| `merge_commit` | [Neovim PR #42237](https://github.com/neovim/neovim/pull/42237) | Four original PR commits, a main merge commit with two parents. |

For each repository and PR, these endpoints supply the recorded fields:

- `GET /repos/{repository}/pulls/{number}`: number, merge time, base ref/SHA, head SHA and original commit count.
- `GET /repos/{repository}/issues/{number}/timeline`: merged event and final commit ID.
- `GET /repos/{repository}/compare/{base}...{merged}`: SHA and parents, narrowed to the first-parent path from base to the merge event.
- `GET /repos/{repository}/commits/{sha}/pulls`: number, merge time and base for every PR that GitHub reports as introducing a main commit.
- `POST /graphql`: `Repository.object(oid:...)` and `Commit.associatedPullRequests(first:100,after:...)`, including number, merged state, base ref and pagination evidence.

The behavior tests reconstruct the captured first-parent topology in a disposable
Git repository and bare remote. They remap recorded SHAs to local commits, rename
Neovim's `master` base to the supported `main`, and add controlled release labels,
bodies and file contents. An unrecorded second-parent branch is represented by a
local side commit. Original source files and branch commit contents are not copied.
`introduce_policy` places the release configuration in the last rebased commit to
exercise first adoption without hiding earlier commits in that PR.

GitHub documents [squash and rebase histories](https://docs.github.com/en/pull-requests/reference/pull-request-merges)
and the [commit-to-PR endpoint](https://docs.github.com/en/rest/commits/commits#list-pull-requests-associated-with-a-commit).
The latter identifies the merged PR that introduced a commit already on the default
branch. No merge method is inferred from a PR's original commit count.
The [GraphQL Commit contract](https://docs.github.com/en/graphql/reference/commits#commit)
provides the same introduction evidence. The generated query was executed against
all four captured cases and a separate batch of 50 actual repo-template main
commits; that batch used one request at one GraphQL point. A regression test runs
intent and prepare with 500 unrelated commits after the recorded base and verifies
that the combined association lookup stays below 25 requests (20 GraphQL batches
and two REST merge-commit lookups).
