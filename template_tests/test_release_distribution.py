import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_release import m
import test_template
import yaml


class ReleaseDistributionTest(unittest.TestCase):
    def asset(self, name="app-v1.2.3-linux-x86_64.tar.gz", **changes):
        return {
            "name": name, "state": "uploaded", "size": 10,
            "browser_download_url": "https://github.com/team/app/releases/download/v1.2.3/" + name,
            **changes,
        }

    def test_notes_preserve_prose_and_use_actual_assets_and_image_tags(self):
        notes = "## Changes\nHand-written notes.\n"
        images = [
            {"repository": "team/custom", "tag": "1.2.3_build.1"},
            {"repository": "team/custom", "tag": "1.2.3-base-r2"},
        ]
        body = m.distribution_body(notes, [self.asset(), self.asset("pending", state="new")], images)
        self.assertTrue(body.startswith(notes))
        self.assertIn(self.asset()["browser_download_url"], body)
        self.assertNotIn("pending", body)
        self.assertIn("https://hub.docker.com/r/team/custom", body)
        for image in images:
            self.assertIn("docker pull team/custom:" + image["tag"], body)
        self.assertEqual(m.distribution_body(body, [self.asset()], images), body)
        body += "\nManual footer.\n"
        updated = m.distribution_body(body, [], [images[0]])
        self.assertTrue(updated.startswith(notes))
        self.assertTrue(updated.endswith("\nManual footer.\n"))
        self.assertNotIn("linux-x86_64", updated)
        self.assertEqual(updated.count(m.DOWNLOADS_START), 1)

    def test_crlf_notes_and_managed_block_are_preserved_on_rerun(self):
        body = m.distribution_body("Notes\r\n", [self.asset()], []) + "\r\nFooter\r\n"
        self.assertEqual(m.distribution_body(body, [self.asset()], []), body)

    def test_no_distribution_and_ecr_do_not_advertise_docker_hub(self):
        self.assertEqual(m.distribution_body("notes", [], []), "notes")
        body = m.distribution_body("", [], [{
            "registry": "ecr", "repository": "custom", "account": "123456789012",
            "region": "ap-northeast-1", "tag": "1.2.3",
        }])
        self.assertNotIn("hub.docker.com", body)
        self.assertIn("docker pull 123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/custom:1.2.3", body)
        self.assertNotIn("Downloads", body)

    def test_malformed_managed_block_preserves_existing_body(self):
        with self.assertRaises(m.PreparationError):
            m.distribution_body("notes\n" + m.DOWNLOADS_START, [self.asset()], [])

    def test_distribution_checks_images_before_publishing_and_is_idempotent(self):
        plan = {"release_tag": "v1.2.3", "is_prerelease": True, "images": [{
            "name": "main", "repository": "team/app", "tag": "1.2.3-rc.1",
        }]}
        release = {"id": 42, "tag_name": "v1.2.3", "draft": True, "body": "notes", "upload_url": "https://uploads.github.example/releases/42/assets{?name,label}"}
        gh = mock.Mock()
        gh.pages.side_effect = lambda path: [release] if path == "/releases" else [self.asset()]

        def repo(path, **kwargs):
            if kwargs:
                release.update(kwargs["payload"])
            return dict(release)

        gh.repo.side_effect = repo
        with mock.patch.object(m, "image_digest", return_value=None):
            with self.assertRaises(m.PreparationError):
                m.distribution(m.Git(), gh, plan)
        self.assertTrue(release["draft"])
        self.assertEqual(release["body"], "notes")
        with mock.patch.object(m, "image_digest", return_value="sha256:test"):
            m.distribution(m.Git(), gh, plan)
            m.distribution(m.Git(), gh, plan)
        writes = [call for call in gh.repo.call_args_list if call.kwargs]
        self.assertEqual(len(writes), 1)
        self.assertFalse(release["draft"])
        self.assertTrue(release["prerelease"])
        self.assertEqual(release["make_latest"], "false")

    def test_distribution_uses_retained_id_when_release_is_not_listed(self):
        release = {"id": 42, "tag_name": "v1.2.3", "draft": True, "body": "notes", "upload_url": "https://uploads.github.example/releases/42/assets{?name,label}"}
        gh = mock.Mock()
        gh.pages.side_effect = lambda path: [] if path == "/releases" else [self.asset()]
        gh.repo.return_value = release
        with mock.patch.dict(os.environ, {"RELEASE_ID": "42"}):
            m.distribution(m.Git(), gh, {
                "release_tag": "v1.2.3", "is_prerelease": False, "images": [],
            })
        self.assertEqual(gh.repo.call_args.args, ("/releases/42",))
        self.assertFalse(gh.repo.call_args.kwargs["payload"]["draft"])

    def test_creation_retains_response_id_and_exact_plan_metadata(self):
        for draft in (False, True):
            for prerelease in (False, True):
                for tag in ("1.2.3", "v1.2.3", "1.2.3-r1", "1.2.3+build.1"):
                    with self.subTest(draft=draft, prerelease=prerelease, tag=tag):
                        gh = mock.Mock()
                        gh.pages.return_value = []
                        def repo(path, **kwargs):
                            if "/git/ref/tags/" in path:
                                return {"object": {"sha": "a" * 40}}
                            if path == "/releases/generate-notes":
                                return {"body": "Generated notes"}
                            return {"id": 42, **kwargs["payload"]}
                        gh.repo.side_effect = repo
                        with mock.patch.dict(os.environ, {"RELEASE_DRAFT": str(draft).lower(), "RELEASE_NOTES_PATH": ""}):
                            self.assertEqual(m.create_release(gh, {
                                "release_tag": tag, "is_prerelease": prerelease,
                            }), 42)
                        payload = gh.repo.call_args.kwargs["payload"]
                        self.assertEqual(payload, {
                            "tag_name": tag, "name": tag, "body": "Generated notes",
                            "draft": draft, "prerelease": prerelease, "make_latest": "false",
                        })

    def test_creation_reuses_single_existing_release_and_rejects_duplicates_or_missing_tag(self):
        release = {"id": 42, "tag_name": "v1.2.3", "draft": True}
        plan = {"release_tag": "v1.2.3", "is_prerelease": False}
        gh = mock.Mock()
        gh.pages.return_value = [release]
        self.assertEqual(m.create_release(gh, plan), 42)
        gh.repo.assert_not_called()
        gh.pages.return_value = [release, {**release, "id": 43}]
        with self.assertRaises(m.PreparationError):
            m.create_release(gh, plan)
        gh.repo.assert_not_called()
        gh.pages.return_value = []
        gh.repo.return_value = None
        with self.assertRaises(m.PreparationError):
            m.create_release(gh, plan)
        self.assertFalse(any(c.kwargs.get("method") == "POST" for c in gh.repo.call_args_list))

    def test_distribution_rejects_conflicting_or_invalid_retained_identity(self):
        release = {"id": 42, "tag_name": "v1.2.3", "draft": True}
        for release_id, matches, response in [
            ("42", [release, {**release, "id": 43}], release),
            ("42", [{**release, "id": 43}], release),
            ("42", [], {**release, "id": 43}),
            ("42", [], {**release, "tag_name": "other"}),
            ("true", [], release), ("0", [], release),
        ]:
            with self.subTest(release_id=release_id, matches=matches, response=response):
                gh = mock.Mock()
                gh.pages.return_value = matches
                gh.repo.return_value = response
                with mock.patch.dict(os.environ, {"RELEASE_ID": release_id}):
                    with self.assertRaises(m.PreparationError):
                        m.distribution(m.Git(), gh, {
                            "release_tag": "v1.2.3", "is_prerelease": False, "images": [],
                        })
                self.assertFalse(any(c.kwargs for c in gh.repo.call_args_list))

    def test_public_release_classification_is_checked_before_any_side_effect(self):
        for expected in (False, True):
            invalid_metadata = [{}, {"prerelease": not expected}] + [
                {"prerelease": value} for value in (None, "false", "true", 0, 1)
            ]
            for metadata in invalid_metadata:
                for action in ("create", "listed", "fetched", "retained", "refreshed"):
                    with self.subTest(expected=expected, metadata=metadata, action=action):
                        valid = {"id": 42, "tag_name": "v1.2.3", "draft": False,
                                 "prerelease": expected, "body": "notes"}
                        invalid = {k: v for k, v in valid.items() if k != "prerelease"}
                        invalid.update(metadata)
                        plan = {"release_tag": "v1.2.3", "is_prerelease": expected,
                                "images": [{"name": "main", "repository": "team/app", "tag": "1.2.3"}]}
                        gh = mock.Mock()
                        def pages(path):
                            if path != "/releases":
                                return [self.asset()]
                            if action == "retained":
                                return []
                            return [valid if action in ("fetched", "refreshed") else invalid]
                        gh.pages.side_effect = pages
                        gh.repo.side_effect = lambda path, **kwargs: dict(
                            valid if action == "refreshed" and gh.repo.call_count == 1 else invalid
                        )
                        with (
                            mock.patch.dict(os.environ, {"RELEASE_ID": "" if action == "listed" else "42", "BUILD_RUST_BINARY": "true"}),
                            mock.patch.object(m, "rust_asset") as rust_asset,
                            mock.patch.object(m, "image_digest", return_value="sha256:test") as digest,
                        ):
                            with self.assertRaisesRegex(m.PreparationError, "classification"):
                                if action == "create":
                                    m.create_release(gh, plan)
                                else:
                                    m.distribution(m.Git(), gh, plan)
                        self.assertFalse(any(c.kwargs for c in gh.repo.call_args_list))
                        if action != "refreshed":
                            rust_asset.assert_not_called()
                            digest.assert_not_called()
                        self.assertEqual(invalid.get("body"), "notes")

    def test_matching_public_release_is_reused_without_changing_classification_or_assets(self):
        for prerelease in (False, True):
            with self.subTest(prerelease=prerelease):
                release = {"id": 42, "tag_name": "v1.2.3", "draft": False,
                           "prerelease": prerelease, "body": "notes"}
                plan = {"release_tag": "v1.2.3", "is_prerelease": prerelease, "images": []}
                gh = mock.Mock()
                gh.pages.side_effect = lambda path: [release] if path == "/releases" else [self.asset()]
                def repo(path, **kwargs):
                    if kwargs:
                        self.assertEqual(set(kwargs["payload"]), {"body"})
                        release.update(kwargs["payload"])
                    return dict(release)
                gh.repo.side_effect = repo
                self.assertEqual(m.create_release(gh, plan), 42)
                gh.repo.assert_not_called()
                with mock.patch.dict(os.environ, {"RELEASE_ID": "", "BUILD_RUST_BINARY": ""}):
                    m.distribution(m.Git(), gh, plan)
                    m.distribution(m.Git(), gh, plan)
                self.assertEqual(release["prerelease"], prerelease)
                self.assertFalse(release["draft"])
                self.assertIn(self.asset()["browser_download_url"], release["body"])
                self.assertEqual(len([c for c in gh.repo.call_args_list if c.kwargs]), 1)

    def test_draft_classification_is_corrected_only_when_distribution_completes(self):
        for prerelease in (False, True):
            with self.subTest(prerelease=prerelease):
                release = {"id": 42, "tag_name": "v1.2.3", "draft": True,
                           "prerelease": not prerelease, "body": "notes"}
                plan = {"release_tag": "v1.2.3", "is_prerelease": prerelease, "images": []}
                gh = mock.Mock()
                gh.pages.side_effect = lambda path: [release] if path == "/releases" else []
                gh.repo.return_value = release
                self.assertEqual(m.create_release(gh, plan), 42)
                gh.repo.assert_not_called()
                m.distribution(m.Git(), gh, plan)
                self.assertEqual(gh.repo.call_args.kwargs["payload"], {
                    "draft": False, "prerelease": prerelease, "make_latest": "false",
                })

    def test_generated_release_paths_call_distribution_once(self):
        helper = test_template.TemplateTest(methodName="runTest")
        self.addCleanup(helper.doCleanups)
        for answers, workflow, rust in [
            (("use_gh_actions_release=true", "use_rust=true"), "release.yml", True),
            (("use_docker=true", "use_gh_actions_docker_release=true", "use_rust=true"), "docker-release.yml", True),
            (("use_python=true", "use_docker=true", "use_gh_actions_docker_release=true"), "docker-release.yml", False),
            (("use_docker=true", "use_gh_actions_docker_project_pipeline=true"), "docker-project-release.yml", False),
        ]:
            with self.subTest(workflow=workflow, rust=rust):
                result, root = helper.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)
                source = (root / ".github/workflows" / workflow).read_text()
                self.assertEqual(source.count("release.py distribution"), 1)
                self.assertEqual('BUILD_RUST_BINARY: "true"' in source, rust)
                self.assertEqual('RELEASE_DRAFT: "true"' in source, rust or workflow == "docker-release.yml")
                self.assertFalse((root / "_release_distribution.yml").exists())

    def test_generated_non_rust_docker_failures_keep_draft_and_resume_with_same_id(self):
        helper = test_template.TemplateTest(methodName="runTest")
        self.addCleanup(helper.doCleanups)
        for ecr in (False, True):
            copied, root = helper.copy_template(
                "use_docker=true", "use_gh_actions_docker_release=true",
                "use_aws_ecr=" + str(ecr).lower(),
            )
            self.assertEqual(copied.returncode, 0, copied.stdout)
            steps = {s["name"]: s for s in yaml.safe_load(
                (root / ".github/workflows/docker-release.yml").read_text()
            )["jobs"]["release"]["steps"]}
            spec = importlib.util.spec_from_file_location("docker_release", root / ".github/scripts/release.py")
            controller = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(controller)
            for prerelease in (False, True):
                for failure in ("missing-image", "registry-error", "body-patch"):
                    with self.subTest(ecr=ecr, prerelease=prerelease, failure=failure):
                        image = {"name": "main", "repository": "team/app", "tag": "1.2.3"}
                        if ecr:
                            image.update(registry="ecr", account="123456789012", region="ap-northeast-1")
                        plan = {"release_tag": "v1.2.3", "is_prerelease": prerelease, "images": [image]}
                        release = {}
                        gh = mock.Mock()
                        # The created Release is not visible in the list yet.
                        gh.pages.side_effect = lambda path: []
                        failing = True
                        def repo(path, **kwargs):
                            if "/git/ref/tags/" in path:
                                return {"object": {"sha": "a" * 40}}
                            if path == "/releases/generate-notes":
                                return {"body": "Generated notes"}
                            if path == "/releases" and kwargs.get("method") == "POST":
                                release.update(id=42, **kwargs["payload"])
                            if kwargs.get("method") == "PATCH":
                                if failing and failure == "body-patch":
                                    raise controller.PreparationError("Body PATCH failed")
                                release.update(kwargs["payload"])
                            return dict(release)
                        gh.repo.side_effect = repo
                        env = {**steps["Create GitHub Release"]["env"], "RELEASE_NOTES_PATH": ""}
                        with mock.patch.dict(os.environ, env):
                            release_id = controller.create_release(gh, plan)
                        self.assertEqual(release_id, 42)
                        self.assertTrue(release["draft"])
                        self.assertEqual(release["prerelease"], prerelease)
                        digest = mock.Mock(return_value=None if failure == "missing-image" else "sha256:test")
                        if failure == "registry-error":
                            digest.side_effect = controller.PreparationError("Registry lookup failed")
                        with (
                            mock.patch.dict(os.environ, {"RELEASE_ID": str(release_id), "BUILD_RUST_BINARY": ""}),
                            mock.patch.object(controller, "image_digest", digest),
                        ):
                            with self.assertRaises(controller.PreparationError):
                                controller.distribution(controller.Git(root), gh, plan)
                        self.assertTrue(release["draft"])
                        self.assertEqual(release["body"], "Generated notes")
                        self.assertEqual(release["make_latest"], "false")
                        failing = False
                        gh.pages.side_effect = lambda path: [release] if path == "/releases" else []
                        self.assertEqual(controller.create_release(gh, plan), release_id)
                        with (
                            mock.patch.dict(os.environ, {"RELEASE_ID": str(release_id), "BUILD_RUST_BINARY": ""}),
                            mock.patch.object(controller, "image_digest", return_value="sha256:test"),
                        ):
                            controller.distribution(controller.Git(root), gh, plan)
                            before = dict(release)
                            controller.distribution(controller.Git(root), gh, plan)
                        self.assertFalse(release["draft"])
                        self.assertEqual(release["prerelease"], prerelease)
                        self.assertEqual(release["make_latest"], "false")
                        self.assertIn("docker pull", release["body"])
                        self.assertEqual(release, before)
                        self.assertEqual(len([c for c in gh.repo.call_args_list
                                              if c.args[0] == "/releases" and c.kwargs]), 1)
