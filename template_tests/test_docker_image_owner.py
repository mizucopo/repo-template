import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_release import m


class DockerImageOwnerTest(unittest.TestCase):
    def setUp(self):
        self.reset_state()
        self.env = mock.patch.dict(
            os.environ,
            {
                "RELEASE_SHA": self.commit,
                "GITHUB_RUN_ID": "123",
                "GITHUB_ACTIONS": "true",
                "GITHUB_JOB": "cleanup-image-owners",
                "GITHUB_RUN_ATTEMPT": "1",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def reset_state(self):
        self.commit = "a" * 40
        self.plan = {
            "release_tag": "v1.2.3",
            "is_prerelease": False,
            "images": [{"name": "main", "repository": "team/app", "tag": "1.2.3"}],
            "latest_image": "main",
        }
        self.release = {
            "id": 42,
            "tag_name": "v1.2.3",
            "draft": False,
            "prerelease": False,
            "body": "Notes\n",
        }
        self.digest = "sha256:" + "b" * 64
        self.gh = mock.Mock()
        self.gh.pages.side_effect = lambda path, **kwargs: (
            [self.release] if path == "/releases" else []
        )
        self.gh.repo.side_effect = self.repo
        self.release["body"] = m.distribution_body(
            self.release["body"], [], self.plan["images"]
        )

    def repo(self, path, **kwargs):
        if kwargs:
            self.release.update(kwargs["payload"])
        return dict(self.release)

    def receipt(self):
        return {
            "schema": 1,
            "release_sha": self.commit,
            "release_tag": "v1.2.3",
            "images": [{**self.plan["images"][0], "digest": self.digest}],
        }

    def body(self, receipt=None):
        return (
            "Notes\n"
            + m.IMAGE_OWNER_START
            + "\n"
            + json.dumps(self.receipt() if receipt is None else receipt)
            + "\n"
            + m.IMAGE_OWNER_END
        )

    def test_recovery_checks_exact_commit_plan_and_live_digest_before_recording(self):
        self.release["body"] = self.body()
        with (
            mock.patch.object(m, "image_digest", return_value=self.digest),
            mock.patch.object(m, "run") as run,
        ):
            m.restore_image_owner(m.Git(), self.gh, self.plan, "1.2.3")
        self.assertEqual(run.call_args.args[-2:], ("record", "1.2.3"))
        for change in (
            "sha",
            "digest",
            "repository",
            "missing",
            "duplicate",
            "draft",
            "tag",
        ):
            with self.subTest(change=change):
                receipt = copy.deepcopy(self.receipt())
                self.release.update(body=self.body(), draft=False)
                if change == "sha":
                    receipt["release_sha"] = "c" * 40
                if change in {"digest", "repository"}:
                    receipt["images"][0][change] = "wrong"
                if change == "tag":
                    receipt["release_tag"] = "v9"
                self.release["body"] = self.body(receipt)
                if change == "missing":
                    self.release["body"] = "Notes"
                if change == "duplicate":
                    self.release["body"] *= 2
                if change == "draft":
                    self.release["draft"] = True
                with (
                    mock.patch.object(m, "image_digest", return_value=self.digest),
                    mock.patch.object(m, "run") as run,
                ):
                    with self.assertRaises(m.PreparationError):
                        m.restore_image_owner(m.Git(), self.gh, self.plan, "1.2.3")
                    run.assert_not_called()

    def test_malformed_or_unknown_receipt_is_rejected(self):
        for value in ("{", "[]", '{"schema":true}', '{"schema":2}', "null"):
            with self.subTest(value=value):
                with self.assertRaises((m.PreparationError, ValueError)):
                    m.retained_image_receipt(
                        m.IMAGE_OWNER_START + value + m.IMAGE_OWNER_END
                    )
        for body in (
            m.IMAGE_OWNER_START + "{}",
            m.IMAGE_OWNER_END,
            m.IMAGE_OWNER_END + m.IMAGE_OWNER_START,
        ):
            with self.assertRaises(m.PreparationError):
                m.retained_image_receipt(body)

    def test_completion_records_before_deletion_and_keeps_existing_receipt(self):
        git = mock.Mock()
        with (
            mock.patch.object(m, "completed_image_run"),
            mock.patch.object(m, "latest", return_value=False),
            mock.patch.object(m, "image_digest", return_value=self.digest),
            mock.patch.object(m, "run") as run,
        ):
            m.complete_image_owners(git, self.gh, self.plan)
        payload = next(
            c.kwargs["payload"] for c in self.gh.repo.call_args_list if c.kwargs
        )
        self.assertTrue(payload["body"].startswith("Notes\n"))
        self.assertEqual(
            json.loads(
                payload["body"]
                .split(m.IMAGE_OWNER_START)[1]
                .split(m.IMAGE_OWNER_END)[0]
            ),
            self.receipt(),
        )
        self.assertEqual([c.args[-2] for c in run.call_args_list], ["verify", "remove"])

    def test_rust_cleanup_uses_validated_deliverables_including_no_executables(self):
        self.plan["rust"] = {"targets": ["x86_64-unknown-linux-gnu"]}
        for archives in ([], [Path("app-v1.2.3-linux-x86_64.tar.gz")]):
            with (
                self.subTest(archives=archives),
                mock.patch.dict(os.environ, {"RUST_RELEASE_DIR": "native-artifacts"}),
                mock.patch.object(m, "completed_image_run"),
                mock.patch.object(m, "latest", return_value=False),
                mock.patch.object(m, "image_digest", return_value=self.digest),
                mock.patch.object(
                    m, "rust_deliverables", return_value=archives
                ) as deliverables,
                mock.patch.object(m, "run") as run,
            ):
                if archives:
                    with self.assertRaisesRegex(
                        m.PreparationError, "Missing Rust asset"
                    ):
                        m.complete_image_owners(mock.Mock(), self.gh, self.plan)
                    run.assert_not_called()
                else:
                    m.complete_image_owners(mock.Mock(), self.gh, self.plan)
                    self.assertEqual(run.call_args.args[-2], "remove")
                deliverables.assert_called_once()

    def test_failed_or_partial_completion_never_deletes(self):
        for failure in ("run", "image", "receipt", "write", "owner"):
            with self.subTest(failure=failure):
                self.gh.reset_mock()
                self.release["body"] = m.distribution_body(
                    self.body({**self.receipt(), "release_sha": "wrong"})
                    if failure == "receipt"
                    else "Notes",
                    [],
                    self.plan["images"],
                )
                with (
                    mock.patch.object(m, "latest", return_value=False),
                    mock.patch.object(
                        m,
                        "completed_image_run",
                        side_effect=m.PreparationError("run")
                        if failure == "run"
                        else None,
                    ),
                    mock.patch.object(
                        m,
                        "image_digest",
                        return_value=None if failure == "image" else self.digest,
                    ),
                    mock.patch.object(
                        m,
                        "run",
                        side_effect=m.PreparationError("owner")
                        if failure == "owner"
                        else None,
                    ) as run,
                ):
                    self.gh.repo.side_effect = (
                        m.PreparationError("write") if failure == "write" else self.repo
                    )
                    with self.assertRaises(m.PreparationError):
                        m.complete_image_owners(mock.Mock(), self.gh, self.plan)
                    self.assertFalse(
                        any(c.args[-2] == "remove" for c in run.call_args_list)
                    )

    def test_run_gate_checks_current_attempt_and_required_jobs(self):
        git = mock.Mock()
        run = {"id": 123, "run_attempt": 1, "status": "in_progress", "conclusion": None}
        jobs = [
            {"name": name, "conclusion": "success"}
            for name in ("release", "promote-latest")
        ]
        self.gh.repo.side_effect = None
        self.gh.repo.return_value = run
        self.gh.pages.side_effect = None
        self.gh.pages.return_value = jobs
        with mock.patch.object(m, "record_at", return_value={"Run": "123"}):
            m.completed_image_run(git, self.gh, self.plan)
            for name in ("release", "promote-latest"):
                for conclusion in ("failure", "cancelled", None, "skipped"):
                    with self.subTest(name=name, conclusion=conclusion):
                        altered = copy.deepcopy(jobs)
                        next(j for j in altered if j["name"] == name)["conclusion"] = (
                            conclusion
                        )
                        self.gh.pages.return_value = altered
                        with self.assertRaises(m.PreparationError):
                            m.completed_image_run(git, self.gh, self.plan)
            self.gh.pages.return_value = jobs
            run["run_attempt"] = 2
            with self.assertRaises(m.PreparationError):
                m.completed_image_run(git, self.gh, self.plan)

    def test_prerelease_and_no_latest_allow_only_skipped_promotion(self):
        self.gh.repo.side_effect = None
        self.gh.repo.return_value = {
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 3,
        }
        self.gh.pages.side_effect = None
        self.gh.pages.return_value = [
            {"name": "release", "conclusion": "success"},
            {"name": "promote-latest", "conclusion": "skipped"},
        ]
        for prerelease, latest in ((True, "main"), (False, None)):
            with mock.patch.object(m, "record_at", return_value={"Run": "123"}):
                m.completed_image_run(
                    mock.Mock(),
                    self.gh,
                    {**self.plan, "is_prerelease": prerelease, "latest_image": latest},
                )
        for conclusion in ("failure", "cancelled", None):
            self.gh.repo.return_value["conclusion"] = conclusion
            with mock.patch.object(m, "record_at", return_value={"Run": "123"}):
                with self.assertRaises(m.PreparationError):
                    m.completed_image_run(mock.Mock(), self.gh, self.plan)

    def test_multi_image_recovery_checks_all_images_and_destinations(self):
        self.plan["images"].append(
            {
                "name": "worker",
                "registry": "ecr",
                "account": "123456789012",
                "region": "ap-northeast-1",
                "repository": "team/worker",
                "tag": "1.2.3-worker",
            }
        )
        with mock.patch.object(m, "image_digest", return_value=self.digest):
            receipt = m.image_owner_receipt(self.plan)
        self.release["body"] = self.body(receipt)
        for digests in ((self.digest, None), (self.digest, "sha256:" + "c" * 64)):
            with (
                mock.patch.object(m, "image_digest", side_effect=digests),
                mock.patch.object(m, "run") as run,
            ):
                with self.assertRaises(m.PreparationError):
                    m.restore_image_owner(mock.Mock(), self.gh, self.plan, "1.2.3")
                run.assert_not_called()
        with (
            mock.patch.object(m, "image_digest", return_value=self.digest),
            mock.patch.object(m, "run") as run,
        ):
            m.restore_image_owner(mock.Mock(), self.gh, self.plan, "1.2.3-worker")
        self.assertEqual(run.call_args.args[-1], "1.2.3-worker")

    def test_cleanup_keeps_markers_for_incomplete_distribution_latest_or_readback(self):
        for failure in (
            "distribution",
            "github-latest",
            "docker-latest",
            "readback",
            "asset",
        ):
            with self.subTest(failure=failure):
                self.reset_state()
                if failure == "distribution":
                    self.release["body"] = "Notes"
                if failure == "asset":
                    self.plan["rust"] = {"targets": ["x86_64-unknown-linux-gnu"]}
                if failure == "github-latest":
                    self.gh.repo.side_effect = lambda path, **kwargs: {
                        **self.release,
                        "id": 43,
                    }
                if failure == "readback":

                    def repo(path, **kwargs):
                        return (
                            self.repo(path, **kwargs)
                            if kwargs
                            else {**self.release, "body": "Notes"}
                        )

                    self.gh.repo.side_effect = repo
                with (
                    mock.patch.object(m, "completed_image_run"),
                    mock.patch.object(
                        m,
                        "latest",
                        return_value=failure in {"github-latest", "docker-latest"},
                    ),
                    mock.patch.object(m, "rust_package", return_value="app"),
                    mock.patch.object(
                        m,
                        "image_digest",
                        side_effect=[self.digest, "sha256:" + "c" * 64]
                        if failure == "docker-latest"
                        else None,
                        return_value=self.digest,
                    ),
                    mock.patch.object(m, "run") as run,
                ):
                    with self.assertRaises(m.PreparationError):
                        m.complete_image_owners(mock.Mock(), self.gh, self.plan)
                    self.assertFalse(
                        any(c.args[-2] == "remove" for c in run.call_args_list)
                    )


class MarkerRefTest(unittest.TestCase):
    def test_exact_ref_idempotence_and_conflicting_owner(self):
        root = Path(__file__).resolve().parents[1]
        helper = next(root.glob(".github/*/manage-docker-image-owner.sh.jinja"))
        with tempfile.TemporaryDirectory() as directory:
            remote = Path(directory) / "remote.git"
            work = Path(directory) / "work"

            def git(*args, cwd=None):
                return subprocess.run(
                    ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
                ).stdout.strip()

            git("init", "--bare", str(remote))
            git("init", str(work))
            git(
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "--allow-empty",
                "-m",
                "one",
                cwd=work,
            )
            sha = git("rev-parse", "HEAD", cwd=work)
            git("remote", "add", "origin", str(remote), cwd=work)
            env = {**os.environ, "RELEASE_SHA": sha}

            def owner(action, tag="1.2.3"):
                return subprocess.run(
                    ["bash", str(helper), action, tag],
                    check=False,
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                )

            for action in ("record", "verify", "remove", "remove"):
                self.assertEqual(owner(action).returncode, 0)
            self.assertEqual(git("ls-remote", "origin", cwd=work), "")
            owner("record")
            git(
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "--allow-empty",
                "-m",
                "two",
                cwd=work,
            )
            env["RELEASE_SHA"] = git("rev-parse", "HEAD", cwd=work)
            for action in ("record", "verify", "remove"):
                self.assertNotEqual(owner(action).returncode, 0)
            self.assertIn(sha, git("ls-remote", "origin", cwd=work))
            for tag in ("../main", "bad/tag", "latest", "--all"):
                self.assertNotEqual(owner("remove", tag).returncode, 0)

    def test_concurrent_ref_change_is_rejected_by_delete_lease(self):
        root = Path(__file__).resolve().parents[1]
        helper = next(root.glob(".github/*/manage-docker-image-owner.sh.jinja"))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            remote = Path(directory) / "remote.git"

            def git(*args, cwd=work):
                return subprocess.check_output(
                    ["git", *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True
                ).strip()

            git("init", "--bare", str(remote), cwd=Path(directory))
            git("init", str(work), cwd=Path(directory))
            git("remote", "add", "origin", str(remote))
            for message in ("one", "two"):
                git(
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "--allow-empty",
                    "-m",
                    message,
                )
            sha = git("rev-parse", "HEAD~")
            other = git("rev-parse", "HEAD")
            ref = "refs/heads/automation/docker-images/1.2.3"
            git("push", "origin", f"{sha}:{ref}")
            git("push", "origin", f"{other}:refs/heads/main")
            tools = Path(directory) / "bin"
            tools.mkdir()
            wrapper = tools / "git"
            real_git = subprocess.check_output(["which", "git"], text=True).strip()
            wrapper.write_text(
                f'#!/bin/bash\nif [ "$1" = push ]; then "{real_git}" --git-dir="{remote}" update-ref "{ref}" "{other}"; fi\nexec "{real_git}" "$@"\n'
            )
            wrapper.chmod(0o755)
            result = subprocess.run(
                ["bash", str(helper), "remove", "1.2.3"],
                check=False,
                cwd=work,
                env={
                    **os.environ,
                    "RELEASE_SHA": sha,
                    "PATH": str(tools) + os.pathsep + os.environ["PATH"],
                },
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(other, git("ls-remote", "origin", ref))
            self.assertIn(other, git("ls-remote", "origin", "refs/heads/main"))
