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


class FakeGitHub:
    def __init__(self, remote):
        self.remote = remote
        self.prs = {}
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

    def pages(self, path):
        if path == "/releases":
            return self.releases
        if path.startswith("/commits/"):
            pr = self.prs.get(path.split("/")[2])
            return [pr] if pr else []
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
        command(self.root, "init", "-b", "main")
        command(self.root, "config", "user.name", "Test")
        command(self.root, "config", "user.email", "test@example.invalid")
        subprocess.run(
            ["git", "init", "--bare", str(self.remote)], check=True, capture_output=True
        )
        command(self.root, "remote", "add", "origin", str(self.remote))
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

    def commit(self, message):
        command(self.root, "add", ".")
        command(self.root, "commit", "--allow-empty", "-m", message)
        command(self.root, "push", "origin", "HEAD:main")
        return command(self.root, "rev-parse", "HEAD")

    def merge_pr(self, number, level="patch"):
        (self.root / f"change-{number}").write_text(str(number))
        sha = self.commit(f"change (#{number})")
        self.gh.prs[sha] = {
            "number": number,
            "merged_at": "now",
            "base": {"ref": "main"},
            "labels": [{"name": f"release:{level}"}],
            "body": f"Release {level}: intended project change",
        }
        self.gh.timelines[number] = [{"event": "merged", "commit_id": sha}]
        return sha

    def sync(self):
        command(self.root, "fetch", "origin", "main", "--tags")
        command(self.root, "reset", "--hard", "FETCH_HEAD")

    def adopt_version_sources(self, sources, before, after, level="patch"):
        self.root = Path(tempfile.mkdtemp(dir=self.temp.name))
        self.remote = self.root.with_suffix(".git")
        command(self.root, "init", "-b", "main")
        command(self.root, "config", "user.name", "Test")
        command(self.root, "config", "user.email", "test@example.invalid")
        subprocess.run(
            ["git", "init", "--bare", str(self.remote)], check=True, capture_output=True
        )
        command(self.root, "remote", "add", "origin", str(self.remote))
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

    def test_initial_version_is_reserved_on_main_and_tag(self):
        result = m.prepare(self.git, self.gh, "1")
        self.assertEqual(result["version"], "0.1.0")
        self.assertEqual(
            command(self.remote, "rev-parse", "main"), result["release_sha"]
        )
        self.assertEqual(
            command(self.remote, "rev-parse", "0.1.0^{commit}"), result["release_sha"]
        )
        self.assertEqual(command(self.remote, "rev-parse", "main^"), self.seed)

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
                command(self.root, "init", "-b", "main")
                command(self.root, "config", "user.name", "Test")
                command(self.root, "config", "user.email", "test@example.invalid")
                subprocess.run(
                    ["git", "init", "--bare", str(self.remote)],
                    check=True,
                    capture_output=True,
                )
                command(self.root, "remote", "add", "origin", str(self.remote))
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

    def test_latest_main_batches_prs_and_uses_maximum_classification(self):
        self.merge_pr(1, "patch")
        source = self.merge_pr(2, "major")
        result = m.prepare(self.git, self.gh, "2")
        self.assertEqual(result["version"], "1.0.0")
        self.assertEqual(command(self.remote, "show", "main:version"), "1.0.0")
        self.assertEqual(command(self.remote, "rev-parse", "main^"), source)
        self.assertEqual(m.record_at(self.git, result["release_sha"])["PRs"], "1,2")
        self.assertEqual(
            set(
                command(
                    self.remote, "diff", "--name-only", "main^", "main"
                ).splitlines()
            ),
            {"version"},
        )

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
        original = self.gh.pages

        def pages(path):
            if path == f"/commits/{source}/pulls":
                return candidates
            return original(path)

        with mock.patch.object(self.gh, "pages", side_effect=pages):
            with self.assertRaisesRegex(m.PreparationError, "squash-merged PR"):
                m.prepare(self.git, self.gh, "32")
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
        original = self.gh.pages

        def pages(path):
            if path == f"/commits/{source}/pulls":
                return [merged, {**merged, "number": 2}]
            return original(path)

        with mock.patch.object(self.gh, "pages", side_effect=pages):
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
        with self.assertRaisesRegex(m.PreparationError, "squash-merged PR"):
            m.prepare(self.git, self.gh, "34")
        self.assertEqual(command(self.remote, "rev-parse", "main"), last)
        self.assertEqual(command(self.remote, "tag"), "")

    def test_missing_or_mismatched_merge_event_stops(self):
        source = self.merge_pr(1)
        for events in [
            [],
            [{"event": "merged", "commit_id": self.seed}],
            [{"event": "merged", "commit_id": None}],
            [{"event": "referenced", "commit_id": source}],
        ]:
            self.gh.timelines[1] = events
            with (
                self.subTest(events=events),
                self.assertRaisesRegex(m.PreparationError, "squash-merged PR"),
            ):
                m.prepare(self.git, self.gh, "35")
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

    def test_rerun_reuses_commit_after_new_pr_and_does_not_renumber(self):
        self.merge_pr(1)
        first = m.prepare(self.git, self.gh, "3")
        self.sync()
        newer = self.merge_pr(2, "minor")
        self.assertEqual(m.prepare(self.git, self.gh, "3"), first)
        self.assertEqual(command(self.remote, "rev-parse", "main"), newer)
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

    def test_missing_multiple_unknown_labels_and_direct_push_stop(self):
        source = self.merge_pr(1)
        for labels in [[], ["release:patch", "release:minor"], ["release:none"]]:
            self.gh.prs[source]["labels"] = [{"name": label} for label in labels]
            with (
                self.subTest(labels=labels),
                self.assertRaisesRegex(m.PreparationError, "exactly one"),
            ):
                m.prepare(self.git, self.gh, "9")
        self.gh.prs.clear()
        with self.assertRaisesRegex(m.PreparationError, "squash-merged PR"):
            m.prepare(self.git, self.gh, "9")
        self.assertEqual(command(self.remote, "tag"), "")

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
        result = m.prepare(self.git, self.gh, "19")
        owner = next(
            ROOT.joinpath(".github").rglob("manage-docker-image-owner.sh.jinja")
        )
        env = {**os.environ, "RELEASE_SHA": result["release_sha"]}
        for action in ["record", "record", "verify"]:
            subprocess.run(
                ["bash", str(owner), action, "image-0.1.0"],
                cwd=self.root,
                env=env,
                check=True,
                capture_output=True,
            )
        ref = "refs/heads/automation/docker-images/image-0.1.0"
        self.assertEqual(command(self.remote, "rev-parse", ref), result["release_sha"])
        env["RELEASE_SHA"] = self.seed
        for action in ["record", "verify"]:
            response = subprocess.run(
                ["bash", str(owner), action, "image-0.1.0"],
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


class VersionDataTest(unittest.TestCase):
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
