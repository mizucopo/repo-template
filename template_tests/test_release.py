import atexit
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_template
import yaml

ROOT = Path(__file__).resolve().parents[1]
RENDERED = tempfile.TemporaryDirectory()
atexit.register(RENDERED.cleanup)
subprocess.run(
    [
        "copier",
        "copy",
        "--trust",
        "--defaults",
        "-d",
        "use_gh_actions_release=true",
        str(ROOT),
        str(Path(RENDERED.name) / "project"),
    ],
    check=True,
    capture_output=True,
)
SOURCE = Path(RENDERED.name) / "project/.github/scripts/release.py"
spec = importlib.util.spec_from_file_location("numbering", SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

POLICY = {
    "version": {
        "scheme": "semver",
        "sources": [{"path": "version", "format": "text"}],
        "locks": [],
    },
    "publication": {
        "release_tag": "{version}",
        "github_release": True,
        "images": [],
        "latest_image": None,
        "release_paths": ["version"],
    },
}


def command(root, *args):
    result = subprocess.run(
        ["git", "--git-dir", str(root), *args]
        if root.name.endswith(".git")
        else ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class FakeGitHub(m.GitHub):
    def __init__(self, remote):
        self.remote = remote
        self.repository = "owner/project"
        self.prs = {}
        self.associations = {}
        self.graphql_connections = {}
        self.timelines = {}
        self.releases = []

    def repo(self, path="", **kwargs):
        if not path:
            return {"default_branch": "main"}
        if path.startswith("/pulls/"):
            return next(
                p
                for p in self.prs.values()
                if p["number"] == int(path.rsplit("/", 1)[1])
            )
        if path.startswith("/git/ref/tags/"):
            tag = path.rsplit("/", 1)[1]
            result = subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(self.remote),
                    "rev-parse",
                    "--verify",
                    f"refs/tags/{tag}",
                ],
                capture_output=True,
            )
            return (
                None
                if result.returncode
                else {"object": {"sha": result.stdout.decode().strip()}}
            )
        raise AssertionError(path)

    def pulls_at(self, sha):
        if sha in self.associations:
            return self.associations[sha]
        pr = self.prs.get(sha)
        return [pr] if pr else []

    def api(self, path, method="GET", payload=None, **kwargs):
        assert path == "/graphql" and method == "POST"
        repository = {}
        for alias, sha in payload["variables"].items():
            if not alias.startswith("c") or alias.endswith("After"):
                continue
            nodes = [
                {"number": pr["number"], "merged": bool(pr.get("merged_at")),
                 "baseRefName": pr["base"]["ref"]}
                for pr in self.pulls_at(sha)
            ]
            connection = {
                "nodes": nodes,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
            if sha in self.graphql_connections:
                connection = json.loads(json.dumps(self.graphql_connections[sha]))
            repository[alias] = {"associatedPullRequests": connection}
        return {"data": {"repository": repository}}

    def pages(self, path):
        if path == "/releases":
            return self.releases
        if path.startswith("/commits/"):
            sha = path.split("/")[2]
            return self.pulls_at(sha)
        if path.startswith("/issues/") and path.endswith("/timeline"):
            return self.timelines.get(int(path.split("/")[2]), [])
        raise AssertionError(path)


class NumberMainTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.remote = Path(self.temp.name) / "origin.git"
        self.root.mkdir()
        self.init_repositories()
        (self.root / ".github").mkdir()
        (self.root / m.POLICY).write_text(json.dumps(POLICY))
        (self.root / "version").write_text("0.1.0\n")
        self.seed = self.commit("initial project")
        self.git = m.Git(self.root)
        self.gh = FakeGitHub(self.remote)
        self.env = mock.patch.dict(
            os.environ,
            {
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.invalid",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def init_repositories(self):
        command(self.root, "init", "-b", "main")
        command(self.root, "config", "user.name", "Test")
        command(self.root, "config", "user.email", "test@example.invalid")
        subprocess.run(
            ["git", "init", "--bare", str(self.remote)], check=True, capture_output=True
        )
        for repository in (self.root, self.remote):
            # Detached writers can outlive subprocess.run and race temp cleanup.
            command(repository, "config", "gc.autoDetach", "false")
            command(repository, "config", "maintenance.autoDetach", "false")
        command(self.root, "remote", "add", "origin", str(self.remote))

    def commit(self, message):
        command(self.root, "add", ".")
        command(self.root, "commit", "--allow-empty", "-m", message)
        command(self.root, "push", "origin", "HEAD:main")
        return command(self.root, "rev-parse", "HEAD")

    def merge_pr(self, number, level="patch"):
        base = command(self.root, "rev-parse", "HEAD")
        (self.root / f"change-{number}").write_text(str(number))
        sha = self.commit(f"change (#{number})")
        self.gh.prs[sha] = {
            "number": number,
            "merged_at": "now",
            "base": {"ref": "main", "sha": base},
            "labels": [{"name": f"release:{level}"}],
            "body": f"Release {level}: intended project change",
        }
        self.gh.timelines[number] = [{"event": "merged", "commit_id": sha}]
        return sha

    def sync(self):
        command(self.root, "fetch", "origin", "main", "--tags")
        command(self.root, "reset", "--hard", "FETCH_HEAD")

    def replay_merge_history(self, name, introduce_policy=False):
        fixture = json.loads(
            (ROOT / "template_tests/fixtures/github_merge_histories.json").read_text()
        )["cases"][name]
        pr = fixture["pr"]
        base = command(self.root, "rev-parse", "HEAD")
        mapped = {pr["base"]["sha"]: base}
        for index, entry in enumerate(fixture["history"]):
            parents = []
            for parent in entry["parents"]:
                if parent not in mapped:
                    tree = self.git.patch_tree(base, {"side-branch": b"PR head\n"})
                    mapped[parent] = self.git.commit(tree, base, "original PR head")
                parents.append(mapped[parent])
            changes = {f"fixture-change-{index}": str(index).encode()}
            if introduce_policy and index == len(fixture["history"]) - 1:
                changes[m.POLICY] = json.dumps(POLICY).encode()
            tree = self.git.patch_tree(parents[0], changes)
            parent_args = [arg for parent in parents for arg in ("-p", parent)]
            sha = self.git.command(
                "commit-tree", tree, *parent_args, data=f"fixture commit {index}".encode()
            ).decode().strip()
            mapped[entry["sha"]] = sha
            self.gh.associations[sha] = [
                {**p, "base": {**p["base"], "ref": "main"}}
                for p in entry["pulls"]
            ]
            connection = entry["graphql"]
            for node in connection["nodes"]:
                node["baseRefName"] = "main"
            self.gh.graphql_connections[sha] = connection
        command(self.root, "reset", "--hard", sha)
        command(self.root, "push", "origin", "HEAD:main")
        self.gh.prs[sha] = {
            **pr,
            "base": {"ref": "main", "sha": base},
            "labels": [{"name": "release:patch"}],
            "body": "Intended patch release",
        }
        self.gh.timelines[pr["number"]] = [
            {**event, "commit_id": mapped[event["commit_id"]]}
            for event in fixture["timeline"]
        ]
        return sha

    def adopt_version_sources(self, sources, before, after, level="patch"):
        self.root = Path(tempfile.mkdtemp(dir=self.temp.name))
        self.remote = self.root.with_suffix(".git")
        self.init_repositories()
        for path, text in before.items():
            (self.root / path).write_text(text)
        self.commit("existing project before release adoption")
        policy = json.loads(json.dumps(POLICY))
        policy["version"]["sources"] = sources
        (self.root / ".github").mkdir()
        (self.root / m.POLICY).write_text(json.dumps(policy))
        for path, text in after.items():
            (self.root / path).write_text(text)
        self.git = m.Git(self.root)
        self.gh = FakeGitHub(self.remote)
        return self.merge_pr(1, level)

    def test_without_merged_pr_does_not_reserve_number_or_tag(self):
        self.assertEqual(m.prepare(self.git, self.gh, "1"), {"publish": "false"})
        self.assertEqual(command(self.remote, "rev-parse", "main"), self.seed)
        self.assertEqual(command(self.remote, "show", "main:version"), "0.1.0")
        self.assertEqual(command(self.remote, "tag"), "")

    def test_automatic_maintenance_finishes_before_git_returns(self):
        for repository in (self.root, self.remote):
            command(repository, "config", "maintenance.auto", "true")
            command(repository, "config", "maintenance.loose-objects.enabled", "true")
            command(repository, "config", "maintenance.loose-objects.auto", "-1")
        # Legacy receive-pack uses gc; the next push must exceed its pack limit.
        for key in ("gc.auto", "gc.autoPackLimit", "receive.unpackLimit"):
            command(self.remote, "config", key, "1")
        command(self.remote, "config", "receive.autoGC", "true")
        command(self.remote, "repack", "-d")
        for operation in (("fetch", "origin"), ("push", "origin", "HEAD:main")):
            with self.subTest(operation=operation):
                if operation[0] == "push":
                    command(self.root, "-c", "maintenance.auto=false", "commit",
                            "--allow-empty", "-m", "maintenance fixture")
                trace = Path(self.temp.name) / f"{operation[0]}-trace.jsonl"
                with mock.patch.dict(os.environ, {"GIT_TRACE2_EVENT": str(trace)}):
                    command(self.root, *operation)
                events = [json.loads(line) for line in trace.read_text().splitlines()]
                caller = next(event["sid"] for event in events if event["event"] == "start")
                returned = next(
                    event["time"] for event in events
                    if event["event"] == "exit" and event["sid"] == caller
                )
                maintenance = [
                    event for event in events
                    if event["event"] == "start" and "maintenance" in event.get("argv", [])
                ]
                if not maintenance:
                    legacy_gc = [
                        event for event in events
                        if event["event"] == "start" and "gc" in event.get("argv", [])
                        and "--auto" in event["argv"]
                    ]
                    self.assertTrue(legacy_gc)
                    for process in legacy_gc:
                        repacks = {
                            event["child_id"] for event in events
                            if event["sid"] == process["sid"]
                            and event["event"] == "child_start"
                            and "repack" in event.get("argv", [])
                        }
                        self.assertTrue(repacks)
                        completed = [
                            event for event in events
                            if event["sid"] == process["sid"]
                            and event["event"] == "child_exit"
                            and event["child_id"] in repacks and event["code"] == 0
                        ]
                        self.assertEqual(len(completed), len(repacks))
                        # A daemon's early parent exit is not its task completion.
                        finished = [
                            event for event in events
                            if event["sid"] == process["sid"] and event["event"] == "exit"
                            and event["time"] > max(task["time"] for task in completed)
                        ]
                        self.assertTrue(finished)
                        self.assertTrue(all(event["time"] < returned for event in finished))
                for process in maintenance:
                    finished = [
                        event for event in events
                        if event["sid"] == process["sid"]
                        and event["event"] == "region_leave"
                        and event.get("category") == "maintenance"
                        and event.get("label") == "loose-objects"
                    ]
                    self.assertTrue(finished)
                    self.assertTrue(all(event["time"] < returned for event in finished))

    def test_first_adoption_uses_parent_version_and_explicit_minimum(self):
        cases = [
            ("semver", "0.1.0", "1.0.0", "major", None, None, "1.0.0"),
            ("chrome", "1.2.3", "1.3.0", "minor", None, None, "1.3.0"),
            ("upstream-revision", "1.2.3", "1.2.3", "patch", "r2", "r3", "1.2.3-r3"),
        ]
        for scheme, before, after, level, old_revision, revision, tag in cases:
            with self.subTest(scheme=scheme):
                self.root = Path(self.temp.name) / scheme
                self.remote = Path(self.temp.name) / f"{scheme}.git"
                self.root.mkdir()
                self.init_repositories()
                (self.root / "version").write_text(before + "\n")
                if old_revision:
                    (self.root / "revision").write_text(old_revision + "\n")
                self.commit("existing project before release adoption")
                policy = json.loads(json.dumps(POLICY))
                policy["version"]["scheme"] = scheme
                if revision:
                    policy["version"]["revision_path"] = "revision"
                    policy["publication"]["release_tag"] = "{version}{revision_suffix}"
                    (self.root / "revision").write_text(revision + "\n")
                (self.root / ".github").mkdir()
                (self.root / m.POLICY).write_text(json.dumps(policy))
                (self.root / "version").write_text(after + "\n")
                self.git = m.Git(self.root)
                self.gh = FakeGitHub(self.remote)
                self.merge_pr(1, level)
                result = m.prepare(self.git, self.gh, "30")
                self.assertEqual(result["version"], after)
                self.assertEqual(result["release_tag"], tag)
                self.assertEqual(
                    command(self.remote, "rev-parse", tag + "^{commit}"),
                    result["release_sha"],
                )

    def test_first_adoption_uses_current_version_for_new_field(self):
        cases = [
            ("toml", ["tool", "release", "version"], "[tool.other]\nenabled = true\n"),
            (
                "toml",
                ["tool", "release", "version"],
                "[tool.release]\nenabled = true\n",
            ),
            ("json", ["tool", "release", "version"], '{"tool": {}}'),
            ("json", ["tool", "release", "version"], '{"tool": {"release": {}}}'),
            ("json", ["items", 0, "version"], '{"items": []}'),
            ("json", ["items", 0, "version"], '{"items": [{}]}'),
        ]
        for fmt, key, before in cases:
            with self.subTest(format=fmt, before=before):
                path = "pyproject.toml" if fmt == "toml" else "package.json"
                if fmt == "toml":
                    after = '[tool.other]\nenabled = true\n[tool.release]\nversion = "0.1.0"\n'
                elif key[0] == "items":
                    after = json.dumps({"items": [{"version": "0.1.0"}]})
                else:
                    after = json.dumps({"tool": {"release": {"version": "0.1.0"}}})
                source = self.adopt_version_sources(
                    [{"path": path, "format": fmt, "key": key}],
                    {path: before},
                    {path: after},
                )
                result = m.prepare(self.git, self.gh, "37")
                self.assertEqual(result["version"], "0.1.1")
                self.assertEqual(command(self.remote, "rev-parse", "main^"), source)
                self.assertEqual(
                    command(self.remote, "rev-parse", "0.1.1^{commit}"),
                    result["release_sha"],
                )

    def test_first_adoption_validates_all_present_sources_before_fallback(self):
        cases = [
            ("0.1.0", None),
            ("broken", "Invalid SemVer"),
            (None, "Invalid SemVer"),
            ([], "Invalid SemVer"),
        ]
        for version, error in cases:
            for reverse in [False, True]:
                with self.subTest(version=version, reverse=reverse):
                    sources = [
                        {"path": "new.json", "format": "json", "key": ["version"]},
                        {"path": "old.json", "format": "json", "key": ["version"]},
                    ]
                    if reverse:
                        sources.reverse()
                    source = self.adopt_version_sources(
                        sources,
                        {
                            "new.json": "{}",
                            "old.json": json.dumps({"version": version}),
                        },
                        {
                            path: '{"version": "0.1.0"}'
                            for path in ["new.json", "old.json"]
                        },
                    )
                    if error:
                        with self.assertRaisesRegex(m.PreparationError, error):
                            m.prepare(self.git, self.gh, "38")
                        self.assertEqual(
                            command(self.remote, "rev-parse", "main"), source
                        )
                        self.assertEqual(command(self.remote, "tag"), "")
                    else:
                        result = m.prepare(self.git, self.gh, "38")
                        self.assertEqual(result["version"], "0.1.1")

    def test_deferred_adoption_preserves_new_source_version_as_baseline(self):
        for before in [{}, {"package.json": "{}"}]:
            with self.subTest(before=before):
                adoption = self.adopt_version_sources(
                    [{"path": "package.json", "format": "json", "key": ["version"]}],
                    before,
                    {"package.json": '{"version": "0.1.0"}'},
                )
                self.gh.prs[adoption]["labels"] = []
                self.assertEqual(m.prepare(self.git, self.gh, "70"), {"publish": "false"})
                (self.root / "package.json").write_text('{"version": "0.2.0"}')
                skipped = self.merge_pr(2)
                self.gh.prs[skipped]["labels"] = []
                self.assertEqual(m.prepare(self.git, self.gh, "71"), {"publish": "false"})
                source = self.merge_pr(3)
                result = m.prepare(self.git, self.gh, "72")
                self.assertEqual(result["version"], "0.2.0")
                self.assertEqual(command(self.remote, "rev-parse", "main^"), source)

    def test_deferred_adoption_preserves_baseline_when_declared_sources_change(self):
        adoption = self.adopt_version_sources(
            [{"path": "old.json", "format": "json", "key": ["version"]}],
            {},
            {"old.json": '{"version": "0.1.0"}'},
        )
        self.gh.prs[adoption]["labels"] = []
        self.assertEqual(m.prepare(self.git, self.gh, "73"), {"publish": "false"})
        policy = json.loads((self.root / m.POLICY).read_text())
        policy["version"]["sources"].append(
            {"path": "new.json", "format": "json", "key": ["version"]}
        )
        (self.root / m.POLICY).write_text(json.dumps(policy))
        for path in ["old.json", "new.json"]:
            (self.root / path).write_text('{"version": "0.2.0"}')
        skipped = self.merge_pr(2)
        self.gh.prs[skipped]["labels"] = []
        self.assertEqual(m.prepare(self.git, self.gh, "74"), {"publish": "false"})
        source = self.merge_pr(3)
        result = m.prepare(self.git, self.gh, "75")
        self.assertEqual(result["version"], "0.2.0")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), source)

    def test_first_adoption_rejects_malformed_parent_data(self):
        cases = [
            ("toml", "[tool.release", ValueError),
            ("json", '{"tool":', ValueError),
            ("toml", "[tool]\nrelease = 1\n", m.PreparationError),
            ("json", '{"tool": {"release": null}}', m.PreparationError),
            ("json", '{"tool": {"release": ""}}', m.PreparationError),
            ("toml", '[tool.release]\nversion = "broken"\n', m.PreparationError),
            ("json", '{"tool": {"release": {"version": 1}}}', m.PreparationError),
        ]
        for fmt, before, error in cases:
            with self.subTest(format=fmt, before=before):
                path = "pyproject.toml" if fmt == "toml" else "package.json"
                after = (
                    '[tool.release]\nversion = "0.1.0"\n'
                    if fmt == "toml"
                    else '{"tool": {"release": {"version": "0.1.0"}}}'
                )
                source = self.adopt_version_sources(
                    [
                        {
                            "path": path,
                            "format": fmt,
                            "key": ["tool", "release", "version"],
                        }
                    ],
                    {path: before},
                    {path: after},
                )
                with self.assertRaises(error):
                    m.prepare(self.git, self.gh, "39")
                self.assertEqual(command(self.remote, "rev-parse", "main"), source)
                self.assertEqual(command(self.remote, "tag"), "")

    def test_first_adoption_missing_file_still_validates_present_sources(self):
        for versions, error in [
            (["0.1.0", "0.1.0"], None),
            (["0.1.0", "broken"], "Invalid SemVer"),
            (["0.1.0", "0.2.0"], "Declared version sources disagree"),
        ]:
            with self.subTest(versions=versions):
                paths = ["new.json", "old.json", "other.json"]
                source = self.adopt_version_sources(
                    [
                        {"path": path, "format": "json", "key": ["version"]}
                        for path in paths
                    ],
                    {
                        path: json.dumps({"version": version})
                        for path, version in zip(paths[1:], versions)
                    },
                    {path: '{"version": "0.1.0"}' for path in paths},
                )
                if error:
                    with self.assertRaisesRegex(m.PreparationError, error):
                        m.prepare(self.git, self.gh, "41")
                    self.assertEqual(command(self.remote, "rev-parse", "main"), source)
                    self.assertEqual(command(self.remote, "tag"), "")
                else:
                    result = m.prepare(self.git, self.gh, "41")
                    self.assertEqual(result["version"], "0.1.1")

    def test_first_adoption_rejects_object_in_place_of_array(self):
        source = self.adopt_version_sources(
            [
                {
                    "path": "package.json",
                    "format": "json",
                    "key": ["items", 0, "version"],
                }
            ],
            {"package.json": '{"items": {}}'},
            {"package.json": '{"items": [{"version": "0.1.0"}]}'},
        )
        with self.assertRaises(m.PreparationError):
            m.prepare(self.git, self.gh, "44")
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), "")

    def test_new_field_after_numbering_does_not_fallback(self):
        (self.root / "package.json").write_text("{}")
        self.merge_pr(1)
        first = m.prepare(self.git, self.gh, "42")
        self.sync()
        policy = json.loads(json.dumps(POLICY))
        policy["version"]["sources"].append(
            {"path": "package.json", "format": "json", "key": ["version"]}
        )
        (self.root / m.POLICY).write_text(json.dumps(policy))
        (self.root / "package.json").write_text(
            json.dumps({"version": first["version"]})
        )
        source = self.merge_pr(2)
        with self.assertRaisesRegex(KeyError, "version"):
            m.prepare(self.git, self.gh, "43")
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), first["release_tag"])

    def test_first_adoption_existing_field_preserves_baseline_and_minimum(self):
        for fmt in ["toml", "json"]:
            for after_version, level, expected in [
                ("0.1.0", "patch", "0.1.1"),
                ("2.0.0", "major", "2.0.0"),
                ("0.1.1", "major", None),
            ]:
                with self.subTest(format=fmt, after=after_version, level=level):
                    path = "pyproject.toml" if fmt == "toml" else "package.json"

                    def content(version):
                        return (
                            f'[tool.release]\nversion = "{version}"\n'
                            if fmt == "toml"
                            else json.dumps({"tool": {"release": {"version": version}}})
                        )

                    source = self.adopt_version_sources(
                        [
                            {
                                "path": path,
                                "format": fmt,
                                "key": ["tool", "release", "version"],
                            }
                        ],
                        {path: content("0.1.0")},
                        {path: content(after_version)},
                        level,
                    )
                    if expected:
                        result = m.prepare(self.git, self.gh, "40")
                        self.assertEqual(result["version"], expected)
                    else:
                        with self.assertRaisesRegex(
                            m.PreparationError, "Explicit minimum"
                        ):
                            m.prepare(self.git, self.gh, "40")
                        self.assertEqual(
                            command(self.remote, "rev-parse", "main"), source
                        )
                        self.assertEqual(command(self.remote, "tag"), "")

    def test_latest_merged_pr_alone_classifies_current_main(self):
        older = self.merge_pr(20, "major")
        source = self.merge_pr(2, "patch")
        self.gh.prs[older]["created_at"] = "2026-10-05T00:00:00Z"
        self.gh.prs[source]["created_at"] = "2026-10-01T00:00:00Z"
        result = m.prepare(self.git, self.gh, "2")
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(command(self.remote, "show", "main:version"), "0.1.1")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), source)
        self.assertEqual(m.record_at(self.git, result["release_sha"])["PRs"], "2")
        self.assertEqual(command(self.remote, "show", "main:change-20"), "20")
        self.assertEqual(command(self.remote, "show", "main:change-2"), "2")
        self.assertEqual(
            command(self.remote, "rev-parse", "0.1.1^{commit}"), result["release_sha"]
        )
        self.assertEqual(
            set(
                command(
                    self.remote, "diff", "--name-only", "main^", "main"
                ).splitlines()
            ),
            {"version"},
        )

    def test_older_unclassified_prs_do_not_block_first_or_later_release(self):
        for first_release in [True, False]:
            with self.subTest(first_release=first_release):
                if not first_release:
                    self.sync()
                older = self.merge_pr(len(self.gh.prs) + 1)
                self.gh.prs[older]["labels"] = []
                self.gh.prs[older]["body"] = None
                source = self.merge_pr(len(self.gh.prs) + 1, "minor")
                original = self.gh.pages

                def pages(path):
                    self.assertNotEqual(path, f"/commits/{older}/pulls")
                    return original(path)

                with mock.patch.object(self.gh, "pages", side_effect=pages):
                    result = m.prepare(self.git, self.gh, str(50 + first_release))
                self.assertEqual(
                    result["version"], "0.2.0" if first_release else "0.3.0"
                )
                self.assertEqual(command(self.remote, "rev-parse", "main^"), source)
                self.assertEqual(
                    m.record_at(self.git, result["release_sha"])["PRs"],
                    str(self.gh.prs[source]["number"]),
                )

    def test_newer_unmerged_association_is_skipped_and_latest_main_is_published(self):
        source = self.merge_pr(1, "minor")
        (self.root / "later-change").write_text("current main")
        current = self.commit("later main commit")
        self.gh.prs[current] = {
            **self.gh.prs[source],
            "number": 2,
            "merged_at": None,
            "labels": [],
            "body": None,
        }
        result = m.prepare(self.git, self.gh, "52")
        self.assertEqual(result["version"], "0.2.0")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), current)
        self.assertEqual(
            command(self.remote, "show", "main:later-change"), "current main"
        )
        self.assertEqual(m.record_at(self.git, result["release_sha"])["PRs"], "1")

    def test_no_new_merged_pr_does_not_renumber_after_direct_commit(self):
        self.merge_pr(1)
        first = m.prepare(self.git, self.gh, "53")
        self.sync()
        (self.root / "later-change").write_text("current main")
        current = self.commit("later main commit")
        self.assertEqual(m.prepare(self.git, self.gh, "54"), {"publish": "false"})
        self.assertEqual(command(self.remote, "rev-parse", "main"), current)
        self.assertEqual(command(self.remote, "tag"), first["release_tag"])

    def test_numbering_commit_is_not_a_classification_target(self):
        self.merge_pr(1)
        first = m.prepare(self.git, self.gh, "55")
        with mock.patch.object(
            self.gh, "pages", side_effect=AssertionError("No PR lookup")
        ):
            self.assertEqual(m.prepare(self.git, self.gh, "56"), {"publish": "false"})
            self.assertEqual(m.prepare(self.git, self.gh, "55"), first)
        self.assertEqual(command(self.remote, "rev-parse", "main"), first["release_sha"])
        self.assertEqual(command(self.remote, "tag"), first["release_tag"])

    def test_commit_association_without_merge_commit_sha_numbers_merged_pr(self):
        source = self.merge_pr(1)
        self.assertNotIn("merge_commit_sha", self.gh.prs[source])
        result = m.prepare(self.git, self.gh, "31")
        self.assertEqual(result["publish"], "true")
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(m.record_at(self.git, result["release_sha"])["PRs"], "1")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), source)

    def test_commit_association_ignores_unmerged_and_other_base_prs(self):
        source = self.merge_pr(1)
        merged = self.gh.prs[source]
        candidates = [
            {**merged, "number": 2, "merged_at": None},
            {**merged, "number": 3, "base": {"ref": "develop"}},
        ]
        self.gh.associations[source] = candidates
        self.assertEqual(m.prepare(self.git, self.gh, "32"), {"publish": "false"})
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), "")
        candidates.append(merged)
        result = m.prepare(self.git, self.gh, "32")
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(m.record_at(self.git, result["release_sha"])["PRs"], "1")

    def test_commit_association_with_multiple_merged_prs_stops(self):
        source = self.merge_pr(1)
        merged = self.gh.prs[source]
        self.gh.timelines[2] = [{"event": "merged", "commit_id": source}]
        self.gh.associations[source] = [merged, {**merged, "number": 2}]
        with self.assertRaisesRegex(m.PreparationError, "squash-merged PR"):
            m.prepare(self.git, self.gh, "33")
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), "")

    def test_rebase_merge_intermediate_commit_stops(self):
        first = self.merge_pr(1)
        (self.root / "second-change").write_text("second commit from PR #1")
        last = self.commit("second change (#1)")
        self.gh.prs[last] = self.gh.prs[first]
        self.gh.timelines[1] = [{"event": "merged", "commit_id": last}]
        with self.assertRaisesRegex(m.PreparationError, "Use squash merge only"):
            m.prepare(self.git, self.gh, "34")
        self.assertEqual(command(self.remote, "rev-parse", "main"), last)
        self.assertEqual(command(self.remote, "tag"), "")

    def test_github_multiple_commit_squash_numbers_and_reruns(self):
        source = self.replay_merge_history("squash")
        self.assertEqual(self.gh.prs[source]["commits"], 3)
        self.assertEqual(m.intent(self.git, self.gh, "71"), {"publish": "true"})
        result = m.prepare(self.git, self.gh, "71")
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), source)
        self.assertEqual(m.record_at(self.git, result["release_sha"])["PRs"], "147")
        self.assertEqual(m.prepare(self.git, self.gh, "71"), result)
        self.assertEqual(m.prepare(self.git, self.gh, "72"), {"publish": "false"})

    def test_github_squash_accepts_unrelated_updates_after_recorded_base(self):
        self.merge_pr(1, "major")
        source = self.replay_merge_history("squash_after_base_advanced")
        result = m.prepare(self.git, self.gh, "73")
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), source)
        self.assertEqual(m.record_at(self.git, result["release_sha"])["PRs"], "64598")
        for index in range(3):
            self.assertEqual(
                command(self.remote, "show", f"main:fixture-change-{index}"), str(index)
            )
        self.assertEqual(command(self.remote, "show", "main:change-1"), "1")

    def test_github_rebase_and_merge_commit_stop_before_numbering(self):
        for name in ["rebase", "merge_commit"]:
            with self.subTest(merge=name):
                source = self.replay_merge_history(name)
                for operation in [m.intent, m.prepare]:
                    with self.assertRaisesRegex(m.PreparationError, "Use squash merge only"):
                        operation(self.git, self.gh, "74")
                    self.assertEqual(command(self.remote, "rev-parse", "main"), source)
                    self.assertEqual(command(self.remote, "show", "main:version"), "0.1.0")
                    self.assertEqual(command(self.remote, "tag"), "")

    def test_first_adoption_inside_rebase_still_checks_earlier_commits(self):
        tree = self.git.patch_tree(self.seed, {m.POLICY: None})
        base = self.git.command("commit-tree", tree, data=b"existing project").decode().strip()
        command(self.root, "reset", "--hard", base)
        # Replace only the disposable test remote's seed to start without policy.
        command(self.remote, "fetch", str(self.root), base)
        command(self.remote, "update-ref", "refs/heads/main", base)
        source = self.replay_merge_history("rebase", introduce_policy=True)
        for operation in [m.intent, m.prepare]:
            with self.assertRaisesRegex(m.PreparationError, "Use squash merge only"):
                operation(self.git, self.gh, "75")
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), "")

    def test_original_rerun_ignores_later_rebase_but_new_run_rejects_it(self):
        self.replay_merge_history("squash")
        result = m.prepare(self.git, self.gh, "76")
        self.sync()
        source = self.replay_merge_history("rebase")
        with mock.patch.object(self.gh, "pages", side_effect=AssertionError("No PR lookup")):
            self.assertEqual(m.prepare(self.git, self.gh, "76"), result)
            self.assertEqual(m.intent(self.git, self.gh, "76"), result)
        with self.assertRaisesRegex(m.PreparationError, "Use squash merge only"):
            m.prepare(self.git, self.gh, "77")
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), result["release_tag"])

    def test_latest_squash_does_not_revalidate_older_rebase(self):
        self.replay_merge_history("rebase")
        source = self.merge_pr(1)
        result = m.prepare(self.git, self.gh, "78")
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), source)
        self.assertEqual(m.record_at(self.git, result["release_sha"])["PRs"], "1")

    def test_older_associations_at_or_before_base_are_not_rebase_evidence(self):
        older = self.merge_pr(1)
        source = self.merge_pr(2)
        self.gh.prs[older] = self.gh.prs[source]
        self.gh.prs[self.seed] = self.gh.prs[source]
        result = m.prepare(self.git, self.gh, "79")
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), source)
        self.assertEqual(m.record_at(self.git, result["release_sha"])["PRs"], "2")

    def test_unverifiable_base_stops_without_using_older_classification(self):
        self.merge_pr(1, "major")
        source = self.merge_pr(2)
        for base in [None, "invalid", "0" * 40, source]:
            self.gh.prs[source]["base"]["sha"] = base
            with (
                self.subTest(base=base),
                self.assertRaisesRegex(m.PreparationError, "verifiable main base SHA"),
            ):
                m.prepare(self.git, self.gh, "80")
            self.assertEqual(command(self.remote, "rev-parse", "main"), source)
            self.assertEqual(command(self.remote, "tag"), "")

    def test_squash_history_lookup_failure_stops(self):
        source = self.replay_merge_history("squash_after_base_advanced")
        with mock.patch.object(self.gh, "api", side_effect=m.PreparationError("API unavailable")):
            with self.assertRaisesRegex(m.PreparationError, "API unavailable"):
                m.prepare(self.git, self.gh, "81")
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), "")

    def test_old_squash_base_does_not_exhaust_ecr_two_phase_api_budget(self):
        base = self.merge_pr(1, "major")
        tree = command(self.root, "rev-parse", "HEAD^{tree}")
        source = base
        for index in range(500):
            source = self.git.commit(tree, source, f"unrelated main update {index}")
        command(self.root, "reset", "--hard", source)
        command(self.root, "push", "origin", "HEAD:main")
        source = self.merge_pr(2)
        self.gh.prs[source]["base"]["sha"] = base
        with (
            mock.patch.object(self.gh, "pages", wraps=self.gh.pages) as calls,
            mock.patch.object(self.gh, "api", wraps=self.gh.api) as graphql,
        ):
            self.assertEqual(m.intent(self.git, self.gh, "82"), {"publish": "true"})
            result = m.prepare(self.git, self.gh, "82")
        association_requests = sum(
            call.args[0].startswith("/commits/") for call in calls.call_args_list
        ) + graphql.call_count
        self.assertLessEqual(association_requests, 25)
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), source)

    def assert_latest_pr_after_direct_updates(self, two_phase):
        self.merge_pr(1)
        (self.root / "later-change").write_text("current main")
        command(self.root, "add", ".")
        tree = command(self.root, "write-tree")
        source = command(self.root, "rev-parse", "HEAD")
        for index in range(500):
            source = self.git.commit(tree, source, f"direct main update {index}")
        command(self.root, "reset", "--hard", source)
        command(self.root, "push", "origin", "HEAD:main")
        with (
            mock.patch.object(self.gh, "pages", wraps=self.gh.pages) as calls,
            mock.patch.object(self.gh, "api", wraps=self.gh.api) as graphql,
        ):
            if two_phase:
                self.assertEqual(m.intent(self.git, self.gh, "86"), {"publish": "true"})
            result = m.prepare(self.git, self.gh, "86")
        association_requests = sum(
            call.args[0].startswith("/commits/") for call in calls.call_args_list
        ) + graphql.call_count
        self.assertLessEqual(association_requests, 25 if two_phase else 12)
        self.assertFalse(any(
            call.args[0].startswith("/commits/") for call in calls.call_args_list
        ))
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), source)
        self.assertEqual(command(self.remote, "show", "main:later-change"), "current main")
        self.assertEqual(m.record_at(self.git, result["release_sha"])["PRs"], "1")
        self.assertEqual(
            command(self.remote, "rev-parse", "0.1.1^{commit}"), result["release_sha"]
        )

    def test_direct_updates_do_not_exhaust_ecr_two_phase_api_budget(self):
        self.assert_latest_pr_after_direct_updates(two_phase=True)

    def test_direct_updates_do_not_exhaust_single_phase_api_budget(self):
        self.assert_latest_pr_after_direct_updates(two_phase=False)

    def test_latest_pr_selection_waits_for_all_association_pages(self):
        older = self.merge_pr(1, "major")
        self.gh.prs[older]["labels"] = []
        source = self.merge_pr(2)
        original = self.gh.api

        def api(path, **kwargs):
            result = original(path, **kwargs)
            variables = kwargs["payload"]["variables"]
            if variables["c0After"] is None:
                result["data"]["repository"]["c0"]["associatedPullRequests"] = {
                    "nodes": [
                        {"number": 1, "merged": True, "baseRefName": "main"}
                    ],
                    "pageInfo": {"hasNextPage": True, "endCursor": "latest-page"},
                }
            return result

        with mock.patch.object(self.gh, "api", side_effect=api) as calls:
            result = m.prepare(self.git, self.gh, "87")
        self.assertEqual(calls.call_count, 2)
        variables = calls.call_args_list[1].kwargs["payload"]["variables"]
        self.assertEqual(variables["c0After"], "latest-page")
        self.assertNotIn("c1", variables)
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(m.record_at(self.git, result["release_sha"])["PRs"], "2")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), source)

    def test_latest_pr_ambiguity_on_later_association_page_stops(self):
        source = self.merge_pr(1)
        self.gh.timelines[2] = [{"event": "merged", "commit_id": source}]
        original = self.gh.api

        def api(path, **kwargs):
            result = original(path, **kwargs)
            connection = result["data"]["repository"]["c0"]["associatedPullRequests"]
            if kwargs["payload"]["variables"]["c0After"] is None:
                connection["pageInfo"] = {
                    "hasNextPage": True, "endCursor": "other-merge",
                }
            else:
                connection["nodes"][0]["number"] = 2
            return result

        with mock.patch.object(self.gh, "api", side_effect=api) as calls:
            with self.assertRaisesRegex(m.PreparationError, "squash-merged PR"):
                m.prepare(self.git, self.gh, "88")
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "show", "main:version"), "0.1.0")
        self.assertEqual(command(self.remote, "tag"), "")

    def test_rebase_association_on_later_graphql_page_stops(self):
        source = self.replay_merge_history("rebase")
        earlier = command(self.root, "rev-parse", f"{source}^")
        original = self.gh.api

        def api(path, **kwargs):
            result = original(path, **kwargs)
            variables = kwargs["payload"]["variables"]
            if variables["c0"] == earlier and variables["c0After"] is None:
                result["data"]["repository"]["c0"]["associatedPullRequests"] = {
                    "nodes": [
                        {"number": 1000 + index, "merged": True, "baseRefName": "main"}
                        for index in range(100)
                    ],
                    "pageInfo": {"hasNextPage": True, "endCursor": "next-page"},
                }
            return result

        with mock.patch.object(self.gh, "api", side_effect=api) as calls:
            with self.assertRaisesRegex(m.PreparationError, "Use squash merge only"):
                m.prepare(self.git, self.gh, "83")
        self.assertEqual(calls.call_count, 3)
        variables = calls.call_args_list[2].kwargs["payload"]["variables"]
        self.assertEqual(variables["c0After"], "next-page")
        self.assertNotIn("c1", variables)
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), "")

    def test_incomplete_graphql_evidence_stops_before_numbering(self):
        source = self.replay_merge_history("squash_after_base_advanced")
        original = self.gh.api
        for failure in [
            "errors", "repository", "commit", "connection", "cursor", "nodes",
            "node", "merged", "base", "number", "page", "hasNextPage", "endCursor",
        ]:
            def api(path, **kwargs):
                result = original(path, **kwargs)
                if failure == "errors":
                    result["errors"] = [{"message": "Partial query failure"}]
                elif failure == "repository":
                    result["data"]["repository"] = None
                elif failure == "commit":
                    result["data"]["repository"]["c0"] = None
                elif failure == "connection":
                    result["data"]["repository"]["c0"]["associatedPullRequests"] = None
                elif failure == "cursor":
                    result["data"]["repository"]["c0"]["associatedPullRequests"]["pageInfo"] = {
                        "hasNextPage": True, "endCursor": None,
                    }
                else:
                    connection = result["data"]["repository"]["c0"]["associatedPullRequests"]
                    if failure == "nodes":
                        connection["nodes"] = None
                    elif failure == "node":
                        connection["nodes"] = [None]
                    elif failure in ["merged", "base", "number"]:
                        key = "baseRefName" if failure == "base" else failure
                        connection["nodes"][0].pop(key)
                    elif failure == "page":
                        connection["pageInfo"] = None
                    else:
                        connection["pageInfo"].pop(failure)
                return result

            with (
                self.subTest(failure=failure),
                mock.patch.object(self.gh, "api", side_effect=api),
                self.assertRaises(m.PreparationError),
            ):
                m.prepare(self.git, self.gh, "84")
            self.assertEqual(command(self.remote, "rev-parse", "main"), source)
            self.assertEqual(command(self.remote, "tag"), "")

    def test_graphql_repeated_cursor_stops(self):
        source = self.replay_merge_history("squash_after_base_advanced")
        original = self.gh.api

        def api(path, **kwargs):
            result = original(path, **kwargs)
            result["data"]["repository"]["c0"]["associatedPullRequests"]["pageInfo"] = {
                "hasNextPage": True, "endCursor": "unchanged",
            }
            return result

        with mock.patch.object(self.gh, "api", side_effect=api) as calls:
            with self.assertRaisesRegex(m.PreparationError, "Invalid PR association cursor"):
                m.prepare(self.git, self.gh, "85")
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), "")

    def test_missing_merge_event_stops_and_other_commit_is_not_classified(self):
        source = self.merge_pr(1)
        for events in [
            [],
            [{"event": "merged", "commit_id": None}],
            [{"event": "referenced", "commit_id": source}],
        ]:
            self.gh.timelines[1] = events
            with (
                self.subTest(events=events),
                self.assertRaisesRegex(m.PreparationError, "merge event"),
            ):
                m.prepare(self.git, self.gh, "35")
            self.assertEqual(command(self.remote, "rev-parse", "main"), source)
            self.assertEqual(command(self.remote, "tag"), "")
        self.gh.timelines[1] = [{"event": "merged", "commit_id": self.seed}]
        self.assertEqual(m.prepare(self.git, self.gh, "35"), {"publish": "false"})
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), "")

    def test_merge_event_lookup_failure_stops(self):
        source = self.merge_pr(1)
        original = self.gh.pages

        def pages(path):
            if path == "/issues/1/timeline":
                raise m.PreparationError("API unavailable")
            return original(path)

        with mock.patch.object(self.gh, "pages", side_effect=pages):
            with self.assertRaisesRegex(m.PreparationError, "API unavailable"):
                m.prepare(self.git, self.gh, "36")
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), "")

    def test_unverifiable_newest_merge_event_never_uses_older_classification(self):
        for has_prior_release in [False, True]:
            with self.subTest(has_prior_release=has_prior_release):
                if has_prior_release:
                    m.prepare(self.git, self.gh, "58")
                    self.sync()
                tags = command(self.remote, "tag")
                self.merge_pr(len(self.gh.prs) + 1, "patch")
                source = self.merge_pr(len(self.gh.prs) + 1, "major")
                number = self.gh.prs[source]["number"]
                for events in [
                    [],
                    [{"event": "merged", "commit_id": None}],
                    [{"event": "merged", "commit_id": "invalid"}],
                ]:
                    self.gh.timelines[number] = events
                    with (
                        self.subTest(events=events),
                        self.assertRaisesRegex(m.PreparationError, "merge event"),
                    ):
                        m.prepare(self.git, self.gh, "57")
                    self.assertEqual(command(self.remote, "rev-parse", "main"), source)
                    self.assertEqual(command(self.remote, "tag"), tags)
                self.gh.timelines[number] = [{"event": "merged", "commit_id": source}]

    def test_rerun_reuses_commit_after_new_pr_and_does_not_renumber(self):
        self.merge_pr(1)
        first = m.prepare(self.git, self.gh, "3")
        self.sync()
        newer = self.merge_pr(2, "minor")
        self.gh.prs[newer]["labels"] = []
        self.assertEqual(m.prepare(self.git, self.gh, "3"), first)
        self.assertEqual(command(self.remote, "rev-parse", "main"), newer)
        self.assertEqual(m.prepare(self.git, self.gh, "4"), {"publish": "false"})
        self.gh.prs[newer]["labels"] = [{"name": "release:minor"}]
        second = m.prepare(self.git, self.gh, "4")
        self.assertEqual(second["version"], "0.2.0")
        self.assertEqual(m.prepare(self.git, self.gh, "5"), {"publish": "false"})

    def test_push_conflict_collects_new_pr_and_recalculates(self):
        self.merge_pr(1)
        original = self.git.command
        pushes = []

        def competing_push(*args, **kwargs):
            if args[:2] == ("push", "--atomic"):
                pushes.append(args)
                if len(pushes) == 1:
                    self.merge_pr(2, "minor")
            return original(*args, **kwargs)

        with mock.patch.object(self.git, "command", side_effect=competing_push):
            result = m.prepare(self.git, self.gh, "6")
        self.assertEqual(result["version"], "0.2.0")
        self.assertEqual(len(pushes), 2)
        self.assertEqual(command(self.remote, "tag"), "0.2.0")

    def test_tag_reserved_by_another_actor_during_push_advances_number(self):
        self.merge_pr(1)
        original = self.git.command
        pushes = []

        def collision(*args, **kwargs):
            if args[:2] == ("push", "--atomic"):
                pushes.append(args)
                if len(pushes) == 1:
                    command(self.root, "tag", "0.1.1")
                    command(self.root, "push", "origin", "refs/tags/0.1.1")
            return original(*args, **kwargs)

        with mock.patch.object(self.git, "command", side_effect=collision):
            result = m.prepare(self.git, self.gh, "20")
        self.assertEqual(result["version"], "0.1.2")
        self.assertEqual(len(pushes), 2)
        self.assertEqual(
            command(self.remote, "rev-parse", "main"), result["release_sha"]
        )

    def test_lost_push_response_is_resolved_without_another_push(self):
        self.merge_pr(1)
        original = self.git.command
        pushes = []

        def lost_response(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[:2] == ("push", "--atomic"):
                pushes.append(args)
                raise m.PreparationError("response lost")
            return result

        with mock.patch.object(self.git, "command", side_effect=lost_response):
            result = m.prepare(self.git, self.gh, "7")
        self.assertEqual(len(pushes), 1)
        self.assertEqual(
            result["release_sha"], command(self.remote, "rev-parse", "main")
        )

    def test_rejected_push_does_not_create_remote_commit_or_tag(self):
        source = self.merge_pr(1)
        original = self.git.command

        def rejection(*args, **kwargs):
            if args[:2] == ("push", "--atomic"):
                raise m.PreparationError("protected branch")
            return original(*args, **kwargs)

        with mock.patch.object(self.git, "command", side_effect=rejection):
            with self.assertRaisesRegex(m.PreparationError, "permissions"):
                m.prepare(self.git, self.gh, "8")
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), "")

    def test_multiple_or_unknown_labels_stop_and_no_merged_pr_skips(self):
        source = self.merge_pr(1)
        for labels in [["release:patch", "release:minor"], ["release:none"]]:
            self.gh.prs[source]["labels"] = [{"name": label} for label in labels]
            with (
                self.subTest(labels=labels),
                self.assertRaisesRegex(m.PreparationError, "exactly one"),
            ):
                m.prepare(self.git, self.gh, "9")
        self.gh.prs.clear()
        self.assertEqual(m.prepare(self.git, self.gh, "9"), {"publish": "false"})
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), "")

    def test_unlabelled_latest_pr_skips_until_next_labelled_pr(self):
        older = self.merge_pr(1, "major")
        source = self.merge_pr(2)
        self.gh.prs[source]["labels"] = [{"name": "dependencies"}]
        self.gh.prs[source]["body"] = None
        original = self.gh.pages

        def pages(path):
            self.assertNotEqual(path, f"/commits/{older}/pulls")
            return original(path)

        with (
            mock.patch.object(self.gh, "pages", side_effect=pages),
            mock.patch.object(
                m, "remote_collisions", side_effect=AssertionError("No reservation")
            ),
        ):
            for run_id in ["59", "60"]:
                self.assertEqual(
                    m.prepare(self.git, self.gh, run_id), {"publish": "false"}
                )
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "show", "main:version"), "0.1.0")
        self.assertEqual(command(self.remote, "tag"), "")

        source = self.merge_pr(3, "patch")
        result = m.prepare(self.git, self.gh, "61")
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), source)
        self.assertEqual(m.record_at(self.git, result["release_sha"])["PRs"], "3")
        for number in [1, 2, 3]:
            self.assertEqual(
                command(self.remote, "show", f"main:change-{number}"), str(number)
            )
        self.assertEqual(m.prepare(self.git, self.gh, "61"), result)
        self.assertEqual(m.prepare(self.git, self.gh, "62"), {"publish": "false"})

    def test_unlabelled_pr_after_numbering_skips_without_changing_reservation(self):
        self.merge_pr(1)
        first = m.prepare(self.git, self.gh, "63")
        self.sync()
        source = self.merge_pr(2)
        self.gh.prs[source]["labels"] = []
        self.gh.prs[source]["body"] = None
        for run_id in ["64", "65"]:
            self.assertEqual(m.prepare(self.git, self.gh, run_id), {"publish": "false"})
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), first["release_tag"])
        self.assertEqual(command(self.remote, "show", "main:version"), first["version"])

    def test_first_labelled_pr_uses_initial_version_and_explicit_minimum(self):
        (self.root / "version").write_text("0.2.0\n")
        skipped = self.merge_pr(1)
        self.gh.prs[skipped]["labels"] = []
        self.assertEqual(m.prepare(self.git, self.gh, "66"), {"publish": "false"})
        source = self.merge_pr(2)
        result = m.prepare(self.git, self.gh, "67")
        self.assertEqual(result["version"], "0.2.0")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), source)
        self.assertEqual(command(self.remote, "show", "main:change-1"), "1")

    def test_release_intent_needs_no_registry_access_and_does_not_reserve(self):
        with mock.patch.object(m, "remote_collisions", side_effect=AssertionError("No registry access")):
            self.assertEqual(m.intent(self.git, self.gh, "68"), {"publish": "false"})
            self.merge_pr(1, "major")
            source = self.merge_pr(2)
            self.gh.prs[source]["labels"] = []
            self.gh.prs[source]["body"] = None
            self.assertEqual(m.intent(self.git, self.gh, "68"), {"publish": "false"})
            self.gh.prs[source]["labels"] = [{"name": "release:patch"}]
            self.gh.prs[source]["body"] = "Intended patch release"
            self.assertEqual(m.intent(self.git, self.gh, "68"), {"publish": "true"})
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "show", "main:version"), "0.1.0")
        self.assertEqual(command(self.remote, "tag"), "")
        self.assertEqual(command(self.remote, "show", "main:change-1"), "1")

    def test_intent_rechecks_before_numbering_and_preserves_original_rerun(self):
        source = self.merge_pr(1)
        self.assertEqual(m.intent(self.git, self.gh, "69"), {"publish": "true"})
        self.gh.prs[source]["labels"] = []
        self.assertEqual(m.prepare(self.git, self.gh, "69"), {"publish": "false"})
        self.gh.prs[source]["labels"] = [{"name": "release:patch"}]
        first = m.prepare(self.git, self.gh, "69")
        self.sync()
        source = self.merge_pr(2)
        self.gh.prs[source]["labels"] = []
        self.assertEqual(m.intent(self.git, self.gh, "69"), first)
        self.assertEqual(m.intent(self.git, self.gh, "70"), {"publish": "false"})
        self.assertEqual(command(self.remote, "rev-parse", "main"), source)
        self.assertEqual(command(self.remote, "tag"), first["release_tag"])

    def test_occupied_number_advances_but_lookup_failure_stops(self):
        self.merge_pr(1)
        command(self.root, "tag", "0.1.1")
        command(self.root, "push", "origin", "refs/tags/0.1.1")
        result = m.prepare(self.git, self.gh, "10")
        self.assertEqual(result["version"], "0.1.2")
        self.sync()
        self.merge_pr(2)
        with mock.patch.object(
            self.gh, "pages", side_effect=m.PreparationError("API unavailable")
        ):
            with self.assertRaisesRegex(m.PreparationError, "API unavailable"):
                m.prepare(self.git, self.gh, "11")

    def test_explicit_version_below_required_increment_stops(self):
        self.merge_pr(1)
        first = m.prepare(self.git, self.gh, "21")
        self.sync()
        for value, level in [("0.0.5", "patch"), ("0.2.0", "major")]:
            (self.root / "version").write_text(value + "\n")
            source = self.merge_pr(len(self.gh.prs) + 1, level)
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(m.PreparationError, "Explicit minimum"),
            ):
                m.prepare(self.git, self.gh, "22")
            self.assertEqual(command(self.remote, "rev-parse", "main"), source)
            self.assertEqual(command(self.remote, "tag"), first["release_tag"])

    def test_explicit_prerelease_transitions_number_and_tag_main(self):
        self.merge_pr(6)
        m.prepare(self.git, self.gh, "30")
        self.sync()
        for number, level, version in [
            (1, "patch", "0.1.2-rc.1"),
            (2, "patch", "0.1.2-rc.2"),
            (3, "minor", "0.2.0-rc.1"),
            (4, "major", "1.0.0-rc.1"),
            (5, "patch", "1.0.0"),
        ]:
            with self.subTest(level=level, version=version):
                if number != 2:
                    (self.root / "version").write_text(version + "\n")
                source = self.merge_pr(number, level)
                result = m.prepare(self.git, self.gh, str(30 + number))
                self.assertEqual(result["version"], version)
                self.assertEqual(result["release_tag"], version)
                self.assertEqual(
                    command(self.remote, "rev-parse", f"{version}^{{commit}}"),
                    result["release_sha"],
                )
                self.assertEqual(
                    command(self.remote, "rev-parse", "main^"), source
                )
                self.sync()
                self.assertEqual((self.root / "version").read_text(), version + "\n")

    def test_nonnumeric_prerelease_explicit_transition_numbers_main(self):
        for version in ["1.20.0-beta.1", "1.20.0"]:
            with self.subTest(version=version):
                source = self.adopt_version_sources(
                    [{"path": "version", "format": "text"}],
                    {"version": "1.20.0-alpha\n"},
                    {"version": version + "\n"},
                )
                result = m.prepare(self.git, self.gh, "91")
                self.assertEqual(result["version"], version)
                self.assertEqual(result["release_tag"], version)
                self.assertEqual(
                    command(self.remote, "rev-parse", f"{version}^{{commit}}"),
                    result["release_sha"],
                )
                self.assertEqual(command(self.remote, "rev-parse", "main^"), source)

    def test_old_run_cannot_roll_back_latest(self):
        self.merge_pr(1)
        first = m.prepare(self.git, self.gh, "12")
        self.sync()
        self.merge_pr(2)
        second = m.prepare(self.git, self.gh, "13")
        self.gh.releases = [
            {"tag_name": first["release_tag"], "draft": False},
            {"tag_name": second["release_tag"], "draft": False},
        ]
        self.assertFalse(m.latest(self.git, self.gh, first["release_sha"]))
        self.assertTrue(m.latest(self.git, self.gh, second["release_sha"]))
        self.gh.releases[1]["draft"] = True
        self.assertTrue(m.latest(self.git, self.gh, first["release_sha"]))

    def test_description_markers_are_not_numbering_records(self):
        for message in [
            "docs: explain releases\n\nRepo-Template-Release: 1",
            "fix: explain a release failure\n\nRepo-Template-Release: 1\n\nMore explanation.",
            "docs: quote the marker\n\n```text\nRepo-Template-Release: 1\n```",
            "chore(release): explain the marker\n\nRepo-Template-Release: 1",
        ]:
            with self.subTest(message=message):
                commit = self.commit(message)
                self.assertIsNone(m.record_at(self.git, commit))
                self.assertEqual(m.records(self.git, commit), [])
                self.assertEqual(m.intent(self.git, self.gh, "71"), {"publish": "false"})
                self.assertEqual(m.prepare(self.git, self.gh, "71"), {"publish": "false"})
                with mock.patch.dict(os.environ, {"RELEASE_SHA": commit}):
                    with self.assertRaisesRegex(m.PreparationError, "not a numbering commit"):
                        m.checkout_plan(self.git)
        self.assertEqual(command(self.remote, "tag"), "")
        with self.assertRaisesRegex(m.PreparationError, "No completed release exists on main"):
            m.latest(self.git, self.gh, commit)

    def test_numbering_reruns_and_latest_survive_description_markers_in_history(self):
        self.commit("docs: explain releases\n\nRepo-Template-Release: 1")
        self.merge_pr(1)
        self.assertEqual(m.intent(self.git, self.gh, "72"), {"publish": "true"})
        first = m.prepare(self.git, self.gh, "72")
        self.assertEqual(first["version"], "0.1.1")
        self.sync()
        self.commit("docs: explain reruns\n\nRepo-Template-Release: 1\n\nMore explanation.")
        following = self.commit("docs: follow up")
        self.gh.releases = [{"tag_name": first["release_tag"], "draft": False}]
        self.assertEqual(m.intent(self.git, self.gh, "72"), first)
        self.assertEqual(m.prepare(self.git, self.gh, "72"), first)
        self.assertEqual(m.prepare(self.git, self.gh, "73"), {"publish": "false"})
        self.assertEqual(command(self.remote, "rev-parse", "main"), following)
        self.assertTrue(m.latest(self.git, self.gh, first["release_sha"]))

        source = self.merge_pr(2)
        self.assertEqual(m.intent(self.git, self.gh, "73"), {"publish": "true"})
        second = m.prepare(self.git, self.gh, "73")
        self.assertEqual(second["version"], "0.1.2")
        self.sync()
        self.commit("docs: quote a marker\n\n```text\nRepo-Template-Release: 1\n```")
        self.commit("docs: another follow up")
        self.assertEqual(m.prepare(self.git, self.gh, "73"), second)
        self.assertEqual(m.prepare(self.git, self.gh, "72"), first)
        self.assertEqual(
            [r["commit"] for r in m.records(self.git, self.git.fetch_main())],
            [second["release_sha"], first["release_sha"]],
        )
        self.assertEqual(m.record_at(self.git, second["release_sha"])["Source"], source)
        self.assertEqual(command(self.remote, "tag"), "0.1.1\n0.1.2")
        self.assertTrue(m.latest(self.git, self.gh, first["release_sha"]))
        self.gh.releases.append({"tag_name": second["release_tag"], "draft": False})
        self.assertFalse(m.latest(self.git, self.gh, first["release_sha"]))
        self.assertTrue(m.latest(self.git, self.gh, second["release_sha"]))

    def test_malformed_numbering_records_still_stop_numbering_and_latest(self):
        self.merge_pr(1)
        first = m.prepare(self.git, self.gh, "74")
        self.sync()
        self.gh.releases = [{"tag_name": first["release_tag"], "draft": False}]
        original = command(self.root, "show", "-s", "--format=%B", first["release_sha"])
        source = m.record_at(self.git, first["release_sha"])["Source"]
        for corruption, error in [
            ("source", "Release source must be the sole parent"),
            ("version", "Release version differs from its record"),
            ("tag", "Release tag differs from its record"),
            ("missing", "Invalid release commit record"),
            ("truncated", "Invalid release commit record"),
            ("duplicate", "Invalid release commit trailers"),
            ("trailer", "Invalid release commit trailers"),
            ("run", "Invalid release source/run"),
            ("files", "Release commit changes files outside version metadata"),
        ]:
            with self.subTest(corruption=corruption):
                parent = command(self.root, "rev-parse", "HEAD")
                message = original.replace(f"Release-Source: {source}", f"Release-Source: {parent}")
                if corruption == "source":
                    message = message.replace(f"Release-Source: {parent}", f"Release-Source: {self.seed}")
                elif corruption == "version":
                    message = message.replace("Release-Version: 0.1.1", "Release-Version: 0.1.2")
                elif corruption == "tag":
                    message = message.replace("Release-Tag: 0.1.1", "Release-Tag: 0.1.2")
                elif corruption == "missing":
                    message = message.replace("Release-Version: 0.1.1\n", "")
                elif corruption == "truncated":
                    message = message.split("Release-Run:")[0]
                elif corruption == "duplicate":
                    message += "\nRelease-Version: 0.1.1\n"
                elif corruption == "trailer":
                    message = message.replace("Release-Version: 0.1.1", "Release-Version 0.1.1")
                elif corruption == "run":
                    message = message.replace("Release-Run: 74", "Release-Run: invalid")
                else:
                    (self.root / "unrelated").write_text("Not version metadata\n")
                invalid = self.commit(message)
                with self.assertRaisesRegex(m.PreparationError, error):
                    m.record_at(self.git, invalid)
                for action in [m.intent, m.prepare, m.latest]:
                    argument = first["release_sha"] if action is m.latest else "75"
                    with self.assertRaisesRegex(m.PreparationError, error):
                        action(self.git, self.gh, argument)
                self.assertEqual(command(self.remote, "rev-parse", "main"), invalid)
                self.assertEqual(command(self.remote, "tag"), first["release_tag"])
                command(self.remote, "update-ref", "refs/heads/main", first["release_sha"])
                self.sync()

    def test_chrome_draft_recovery_preserves_newer_latest(self):
        helper = test_template.TemplateTest(methodName="runTest")
        self.addCleanup(helper.doCleanups)
        result, project = helper.copy_template(
            "use_python=false",
            "use_chrome_extension=true",
            "use_gh_actions_chrome_extension_release=true",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        jobs = yaml.safe_load(
            (project / ".github/workflows/chrome-extension-release.yml").read_text()
        )["jobs"]
        publish = next(
            step["run"] for step in jobs["release"]["steps"]
            if step["name"] == "Publish complete draft"
        )
        promote = next(
            step for step in jobs["promote-latest"]["steps"]
            if step["name"] == "Mark GitHub Release as latest"
        )
        self.assertEqual(promote["if"], "steps.latest-state.outputs.promote_latest == 'true'")

        policy = json.loads((self.root / m.POLICY).read_text())
        policy["version"]["scheme"] = "chrome"
        (self.root / m.POLICY).write_text(json.dumps(policy))
        self.merge_pr(1)
        first = m.prepare(self.git, self.gh, "12")
        self.sync()
        self.merge_pr(2)
        second = m.prepare(self.git, self.gh, "13")
        state_path = project / "releases.json"
        state_path.write_text(json.dumps({
            "latest": None,
            "releases": [
                {"id": index, "tag_name": release["release_tag"], "draft": True,
                 "assets": [{"name": "extension.zip", "state": "uploaded", "size": 10}]}
                for index, release in enumerate((first, second), 1)
            ],
        }))
        mock_bin = project / "mock-bin"
        mock_bin.mkdir()
        api_mock = f"#!{sys.executable}\n" + '''
import json, os, sys
from pathlib import Path

path = Path(os.environ["MOCK_RELEASES"])
state = json.loads(path.read_text())
args = sys.argv[1:]
if Path(sys.argv[0]).name == "curl":
    tag = args[-1].rsplit("/", 1)[1]
    release = next(r for r in state["releases"] if r["tag_name"] == tag)
    public = not release["draft"]
    Path(args[args.index("--output") + 1]).write_text(json.dumps(release if public else {}))
    print("200" if public else "404", end="")
elif args == ["api", "--paginate", "--slurp", "/repos/owner/extension/releases?per_page=100"]:
    print(json.dumps([state["releases"]]))
elif args[:2] == ["release", "edit"]:
    release = next(r for r in state["releases"] if r["tag_name"] == args[2])
    if "--draft=false" in args:
        release["draft"] = False
        # Publishing defaults to Latest unless the caller explicitly disables it.
        if "--latest=false" not in args:
            state["latest"] = args[2]
    elif args[3:] == ["--latest"]:
        assert not release["draft"]
        state["latest"] = args[2]
    else:
        raise AssertionError(args)
    path.write_text(json.dumps(state))
else:
    raise AssertionError(args)
'''
        for name in ("gh", "curl"):
            executable = mock_bin / name
            executable.write_text(api_mock)
            executable.chmod(0o755)
        env = {
            **os.environ,
            "PATH": str(mock_bin) + os.pathsep + os.environ["PATH"],
            "GH_TOKEN": "test-token",
            "GITHUB_API_URL": "https://api.github.invalid",
            "GITHUB_REPOSITORY": "owner/extension",
            "GITHUB_OUTPUT": str(project / "outputs"),
            "RELEASE_ASSET_NAME": "extension.zip",
            "INSPECT_DRAFT_RELEASES": "true",
            "MOCK_RELEASES": str(state_path),
        }

        # Leave the older draft unfinished, publish the newer release, then recover it.
        for release in (second, first):
            with self.subTest(tag=release["release_tag"]):
                command(self.root, "checkout", "--detach", release["release_sha"])
                env["TAG"] = release["release_tag"]
                result = subprocess.run(
                    ["bash", "-e", "-o", "pipefail"], input=publish,
                    cwd=self.root, env=env, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.gh.releases = json.loads(state_path.read_text())["releases"]
                if m.latest(self.git, self.gh, release["release_sha"]):
                    result = subprocess.run(
                        ["bash", "-e"], input=promote["run"], cwd=self.root,
                        env=env, capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                state = json.loads(state_path.read_text())
                self.assertEqual(state["latest"], second["release_tag"])
        self.assertTrue(all(not release["draft"] for release in state["releases"]))

    def test_new_control_definition_is_left_for_its_own_run(self):
        control = self.seed
        (self.root / ".github/workflows").mkdir()
        (self.root / ".github/workflows/release.yml").write_text("updated workflow")
        self.merge_pr(1)
        self.assertEqual(
            m.prepare(self.git, self.gh, "15", control), {"publish": "false"}
        )
        self.assertEqual(command(self.remote, "tag"), "")
        source = command(self.root, "rev-parse", "HEAD")
        result = m.prepare(self.git, self.gh, "16", source)
        self.assertEqual(result["version"], "0.1.1")
        self.assertEqual(
            command(self.root, "show", "-s", "--format=%an", result["release_sha"]),
            "Test",
        )

    def test_later_prerelease_does_not_block_stable_latest(self):
        self.merge_pr(1)
        first = m.prepare(self.git, self.gh, "17")
        self.sync()
        (self.root / "version").write_text("0.2.0-rc.1\n")
        self.merge_pr(2)
        second = m.prepare(self.git, self.gh, "18")
        self.gh.releases = [
            {"tag_name": first["release_tag"], "draft": False},
            {"tag_name": second["release_tag"], "draft": False, "prerelease": True},
        ]
        self.assertTrue(m.latest(self.git, self.gh, first["release_sha"]))
        self.gh.releases.append(dict(self.gh.releases[0]))
        with self.assertRaisesRegex(m.PreparationError, "Ambiguous"):
            m.latest(self.git, self.gh, first["release_sha"])

    def test_image_ownership_reservation_is_retained_and_cannot_be_stolen(self):
        self.merge_pr(1)
        result = m.prepare(self.git, self.gh, "19")
        image = "image-" + result["release_tag"]
        owner = next(
            ROOT.joinpath(".github").rglob("manage-docker-image-owner.sh.jinja")
        )
        env = {**os.environ, "RELEASE_SHA": result["release_sha"]}
        for action in ["record", "record", "verify"]:
            subprocess.run(
                ["bash", str(owner), action, image],
                cwd=self.root,
                env=env,
                check=True,
                capture_output=True,
            )
        ref = "refs/heads/automation/docker-images/" + image
        self.assertEqual(command(self.remote, "rev-parse", ref), result["release_sha"])
        env["RELEASE_SHA"] = self.seed
        for action in ["record", "verify"]:
            response = subprocess.run(
                ["bash", str(owner), action, image],
                cwd=self.root,
                env=env,
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(response.returncode, 0)
        self.assertEqual(command(self.remote, "rev-parse", ref), result["release_sha"])

    def test_checkout_must_use_numbered_sha_and_tag(self):
        self.merge_pr(1)
        result = m.prepare(self.git, self.gh, "14")
        with mock.patch.dict(os.environ, {"RELEASE_SHA": result["release_sha"]}):
            with self.assertRaisesRegex(m.PreparationError, "prepared release SHA"):
                m.checkout_plan(self.git)
            self.sync()
            plan = m.checkout_plan(self.git)
        self.assertEqual(plan["generated_version"], result["version"])
        self.assertEqual(plan["publication"]["release_tag"], result["release_tag"])


class ClassificationTest(unittest.TestCase):
    def test_untouched_template_and_hidden_comments_are_not_classification_reasons(self):
        template = (SOURCE.parents[1] / "pull_request_template.md").read_text()
        for level in ["patch", "minor", "major"]:
            for body in [template, "<!-- hidden reason -->", "<!-- unfinished reason"]:
                pr = {"number": 1, "labels": [{"name": "release:" + level}], "body": body}
                with self.subTest(level=level, body=body):
                    with self.assertRaisesRegex(m.PreparationError, "reason"):
                        m.classification(pr)
                    pr["body"] = "Visible classification reason\n" + body
                    self.assertEqual(m.classification(pr), level)

    def test_unlabelled_prs_skip_and_valid_classifications_are_accepted(self):
        for author in ["contributor", "dependabot[bot]"]:
            for labels, expected in [
                ([], None),
                (["dependencies"], None),
                (["release:patch"], "patch"),
                (["dependencies", "release:minor"], "minor"),
                (["release:major"], "major"),
            ]:
                with self.subTest(author=author, labels=labels):
                    pr = {
                        "number": 1,
                        "user": {"login": author},
                        "labels": [{"name": label} for label in labels],
                        "body": "Intended release impact" if expected else None,
                    }
                    self.assertEqual(m.classification(pr), expected)

    def test_unknown_or_multiple_classifications_fail(self):
        for author in ["contributor", "dependabot[bot]"]:
            for labels in [
                ["release:none"],
                ["release:patch", "release:minor"],
                ["release:patch", "release:unknown"],
            ]:
                with self.subTest(author=author, labels=labels):
                    with self.assertRaisesRegex(m.PreparationError, "exactly one"):
                        m.classification(
                            {
                                "number": 1,
                                "user": {"login": author},
                                "labels": [{"name": label} for label in labels],
                                "body": "Intended release impact",
                            }
                        )

    def test_labelled_pr_still_requires_a_reason(self):
        with self.assertRaisesRegex(m.PreparationError, "reason"):
            m.classification(
                {"number": 1, "labels": [{"name": "release:patch"}], "body": None}
            )

    def test_classification_action_succeeds_without_labels_or_release_context(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps({"number": 1}))
            gh = mock.Mock()
            gh.repo.return_value = {"number": 1, "labels": [], "body": None}
            with (
                mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event)}),
                mock.patch.object(sys, "argv", [str(SOURCE), "classification"]),
                mock.patch.object(m, "GitHub", return_value=gh),
            ):
                m.main()
            gh.repo.assert_called_once_with("/pulls/1")


class VersionDataTest(unittest.TestCase):
    def test_semver_identifiers_and_build_metadata(self):
        for version, expected in [
            ("0.0.0", ((0, 0, 0), 1, ())),
            ("1.2.3+001.build-7", ((1, 2, 3), 1, ())),
            ("1.2.3-0.10", ((1, 2, 3), 0, ((0, 0), (0, 10)))),
            (
                "1.2.3-00alpha.01-.--.a0+001.build-7",
                ((1, 2, 3), 0, ((1, "00alpha"), (1, "01-"), (1, "--"), (1, "a0"))),
            ),
        ]:
            with self.subTest(version=version):
                self.assertEqual(m.version_key(version, "semver"), expected)
        for version in [
            "01.2.3", "1.02.3", "1.2.03", "1.2", "v1.2.3",
            "1.2.3-00", "1.2.3-alpha.01", "1.2.3-", "1.2.3-alpha.",
            "1.2.3-alpha..1", "1.2.3+", "1.2.3+build.", "1.2.3+build..7",
            "1.2.3-alpha_1", "1.2.3-\u03b1", "1.2.3+\u0661", "1.2.3\n", None,
        ]:
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(m.PreparationError, "Invalid SemVer"),
            ):
                m.version_key(version, "semver")
        self.assertEqual(
            m.bump("1.2.3-00alpha.--.9+001", "patch", "semver"),
            "1.2.3-00alpha.--.10",
        )
        self.assertEqual(m.bump("1.2.3+001", "patch", "semver"), "1.2.4")
        with self.assertRaisesRegex(m.PreparationError, "numeric channel sequence"):
            m.bump("1.2.3-alpha", "patch", "semver")

    def test_semver_prerelease_precedence(self):
        versions = [
            "1.0.0-0", "1.0.0-2", "1.0.0-10", "1.0.0-alpha",
            "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
            "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0",
        ]
        keys = [m.version_key(version, "semver") for version in versions]
        for before, after in zip(keys, keys[1:]):
            self.assertLess(before, after)
        for version in versions:
            with self.subTest(version=version):
                self.assertEqual(
                    m.version_key(version + "+001.build", "semver"),
                    m.version_key(version, "semver"),
                )

    def test_malformed_semver_rejection_has_bounded_runtime(self):
        code = """
import runpy
import sys
m = runpy.run_path(sys.argv[1])
valid = "0.0.0-0." + "--." * 1000 + "1+001"
assert m["version_key"](valid, "semver")[2][-1] == (0, 1)
assert m["bump"](valid, "patch", "semver") == valid.removesuffix("1+001") + "2"
versions = [
    "0.0.0-0." + "--." * 24,
    "0.0.0-0." + "--." * 1000,
    "0.0.0-" + "aa." * 1000 + "!",
    "0.0.0-" + "a" * 100000 + "!",
    "0.0.0-" + "0" * 100000 + ".",
    "0.0.0+" + "--." * 1000,
]
for version in versions:
    assert m["SEMVER"].fullmatch(version) is None
    for validate in [
        lambda: m["version_key"](version, "semver"),
        lambda: m["bump"](version, "patch", "semver"),
    ]:
        try:
            validate()
        except m["PreparationError"] as exc:
            assert str(exc).startswith("Invalid SemVer:")
        else:
            raise AssertionError("Malformed SemVer was accepted")
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(SOURCE)],
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_explicit_prerelease_can_start_at_required_core(self):
        for level, core in [
            ("patch", "0.2.4"),
            ("minor", "0.3.0"),
            ("major", "1.0.0"),
        ]:
            with self.subTest(level=level):
                version = f"{core}-rc.1"
                chosen, revision, plan = m.choose(
                    POLICY, "0.2.3", level, version, lambda _: []
                )
                self.assertEqual(chosen, version)
                self.assertIsNone(revision)
                self.assertEqual(plan["release_tag"], version)

    def test_explicit_prerelease_below_required_core_stops(self):
        for level, version in [
            ("patch", "0.2.3-rc.99"),
            ("minor", "0.2.99-rc.1"),
            ("major", "0.99.99-rc.1"),
        ]:
            with (
                self.subTest(level=level),
                self.assertRaisesRegex(m.PreparationError, "Explicit minimum"),
            ):
                m.choose(POLICY, "0.2.3", level, version, lambda _: [])

    def test_existing_prerelease_sequence_cannot_rewind(self):
        for version in ["0.2.4-rc.1", "0.2.4-rc.2", "0.2.4-beta.99"]:
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(m.PreparationError, "Explicit minimum"),
            ):
                m.choose(POLICY, "0.2.4-rc.2", "patch", version, lambda _: [])

    def test_semver_automatic_numbering_is_unchanged(self):
        for base, level, expected in [
            ("0.2.3", "patch", "0.2.4"),
            ("0.2.3", "minor", "0.3.0"),
            ("0.2.3", "major", "1.0.0"),
            ("0.2.4-rc.2", "patch", "0.2.4-rc.3"),
        ]:
            with self.subTest(base=base, level=level):
                self.assertEqual(
                    m.choose(POLICY, base, level, None, lambda _: [])[0], expected
                )
        for level in ["minor", "major"]:
            with (
                self.subTest(level=level),
                self.assertRaisesRegex(m.PreparationError, "explicit version"),
            ):
                m.choose(POLICY, "0.2.4-rc.2", level, None, lambda _: [])

    def test_explicit_semver_transitions_preserve_increment_floor(self):
        for base, level, version in [
            ("0.2.3", "minor", "0.3.0"),
            ("0.2.3", "minor", "0.3.1-rc.1"),
            ("0.2.4-rc.2", "patch", "0.2.4-rc.3"),
            ("0.2.4-rc.2", "patch", "0.2.4-rc.9"),
            ("0.2.4-alpha.2", "patch", "0.2.4-beta.1"),
            ("0.2.4-rc.2", "patch", "0.2.4"),
            ("0.2.4-rc.2", "minor", "0.3.0-rc.1"),
            ("0.2.4-rc.2", "major", "1.0.0-rc.1"),
        ]:
            with self.subTest(base=base, level=level, version=version):
                self.assertEqual(
                    m.choose(POLICY, base, level, version, lambda _: [])[0], version
                )

    def test_occupied_explicit_prerelease_advances_within_series(self):
        for level, core in [
            ("patch", "0.2.4"),
            ("minor", "0.3.0"),
            ("major", "1.0.0"),
        ]:
            with self.subTest(level=level):
                occupied = mock.Mock(side_effect=[["release_tag"], []])
                version, _, plan = m.choose(
                    POLICY, "0.2.3", level, f"{core}-rc.1", occupied
                )
                self.assertEqual(version, f"{core}-rc.2")
                self.assertEqual(plan["release_tag"], version)
                self.assertEqual(
                    [call.args[0]["release_tag"] for call in occupied.call_args_list],
                    [f"{core}-rc.1", f"{core}-rc.2"],
                )

    def test_nonnumeric_prerelease_accepts_higher_explicit_versions(self):
        for version in ["1.20.0-alpha.1", "1.20.0-beta.1", "1.20.0", "1.20.1-rc.1"]:
            with self.subTest(version=version):
                self.assertEqual(
                    m.choose(POLICY, "1.20.0-alpha", "patch", version, lambda _: [])[0],
                    version,
                )

    def test_nonnumeric_prerelease_rejects_missing_same_or_lower_versions(self):
        for version in [None, "1.20.0-alpha", "1.20.0-alpha+build-1", "1.20.0-0", "1.19.9"]:
            occupied = mock.Mock(return_value=[])
            with self.subTest(version=version), self.assertRaises(m.PreparationError):
                m.choose(POLICY, "1.20.0-alpha", "patch", version, occupied)
            occupied.assert_not_called()

    def test_nonnumeric_prerelease_explicit_collisions_remain_safe(self):
        for minimum, expected in [
            ("1.20.0-beta.1", "1.20.0-beta.2"),
            ("1.20.0", "1.20.1"),
        ]:
            with self.subTest(minimum=minimum):
                occupied = mock.Mock(side_effect=[["release_tag"], []])
                self.assertEqual(
                    m.choose(POLICY, "1.20.0-alpha", "patch", minimum, occupied)[0],
                    expected,
                )
        with self.assertRaisesRegex(m.PreparationError, "numeric channel sequence"):
            m.choose(POLICY, "1.20.0-alpha", "patch", "1.20.0-beta", lambda _: ["release_tag"])

    def test_semver_chrome_and_upstream_revision_rules(self):
        self.assertEqual(m.bump("0.2.3", "major", "semver"), "1.0.0")
        self.assertEqual(m.bump("1.2.3-rc.1", "patch", "semver"), "1.2.3-rc.2")
        self.assertEqual(m.bump("1.2.3.4", "patch", "chrome"), "1.2.3.5")
        with self.assertRaises(m.PreparationError):
            m.bump("1.2.65535", "patch", "chrome")
        policy = json.loads(json.dumps(POLICY))
        policy["version"]["scheme"] = "upstream-revision"
        policy["publication"]["release_tag"] = "{version}{revision_suffix}"
        self.assertEqual(
            m.choose(
                policy,
                "2.3.4",
                "major",
                None,
                lambda _: [],
                base_revision="r2",
                input_version="2.3.4",
            )[:2],
            ("2.3.4", "r3"),
        )
        self.assertEqual(
            m.choose(
                policy,
                "2.3.4",
                "patch",
                None,
                lambda _: [],
                base_revision="r2",
                input_version="2.4.0",
            )[:2],
            ("2.4.0", "r0"),
        )

    def test_json_toml_and_lock_updates_preserve_other_data(self):
        cases = [
            (
                b'{"version":"1.2.3","dependencies":{"x":"9.0"}}',
                {"format": "json", "key": ["version"], "path": "package.json"},
            ),
            (
                b'[project]\nname="app"\nversion="1.2.3"\n[tool.example]\nversion="9"\n',
                {
                    "format": "toml",
                    "key": ["project", "version"],
                    "path": "pyproject.toml",
                },
            ),
            (
                b'[[package]]\nname="app"\nversion="1.2.3"\n[[package]]\nname="other"\nversion="9"\n',
                {"format": "toml-lock", "package": "app", "path": "uv.lock"},
            ),
        ]
        for blob, spec in cases:
            with self.subTest(format=spec["format"]):
                updated = m.write_field(blob, spec, "1.2.4")
                self.assertEqual(m.read_field(updated, spec), "1.2.4")
                self.assertIn(b"9", updated)

    def test_json_updates_preserve_formatting_and_other_versions(self):
        cases = [
            (
                b'{ "version": "1.2.3", "permissions": ["tabs", "storage"] }\n',
                ["version"],
            ),
            (
                b'{"version":"9","packages":{"":{"version":"1.2.3"},"other":{"version":"9"}}}\n',
                ["packages", "", "version"],
            ),
            (
                b'{"items": [{"version": "9"}, {"version": "1.2.3"}]}\n',
                ["items", 1, "version"],
            ),
        ]
        for blob, keys in cases:
            with self.subTest(keys=keys):
                updated = m.write_field(
                    blob,
                    {"format": "json", "path": "manifest.json", "key": keys},
                    "1.2.4",
                )
                self.assertEqual(updated, blob.replace(b'"1.2.3"', b'"1.2.4"'))
        with self.assertRaises(m.PreparationError):
            m.write_field(
                b'{"version":"old","version":"1.2.3"}',
                {"format": "json", "path": "manifest.json", "key": ["version"]},
                "1.2.4",
            )

    def test_registry_auth_and_unverifiable_digest_are_not_absence(self):
        image = {"name": "base", "repository": "owner/image", "tag": "1.2.3"}
        with mock.patch.dict(os.environ, {"DOCKERHUB_TOKEN": ""}):
            with mock.patch.object(
                m, "registry_json", side_effect=m.PreparationError("HTTP 403")
            ):
                with self.assertRaises(m.PreparationError):
                    m.image_digest(image)
            with mock.patch.object(
                m, "registry_json", side_effect=[{}, {"digest": "unknown"}]
            ):
                with self.assertRaisesRegex(m.PreparationError, "verifiable digest"):
                    m.image_digest(image)
            with mock.patch.object(m, "registry_json", side_effect=[{}, None]):
                self.assertIsNone(m.image_digest(image))


if __name__ == "__main__":
    unittest.main()
