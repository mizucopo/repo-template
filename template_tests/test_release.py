import atexit
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
            "merge_commit_sha": sha,
            "labels": [{"name": f"release:{level}"}],
            "body": f"Release {level}: intended project change",
        }
        return sha

    def sync(self):
        command(self.root, "fetch", "origin", "main", "--tags")
        command(self.root, "reset", "--hard", "FETCH_HEAD")

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
