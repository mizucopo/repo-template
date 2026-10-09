import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_release import m
import test_template


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
        release = {"id": 42, "tag_name": "v1.2.3", "draft": True, "body": "notes"}
        gh = mock.Mock()
        gh.pages.side_effect = lambda path: [release] if path == "/releases" else [self.asset()]

        def repo(path, **kwargs):
            if kwargs:
                release.update(kwargs["payload"])
            return dict(release)

        gh.repo.side_effect = repo
        with mock.patch.object(m, "image_digest", return_value=None):
            with self.assertRaises(m.PreparationError):
                m.distribution(gh, plan)
        self.assertTrue(release["draft"])
        self.assertEqual(release["body"], "notes")
        with mock.patch.object(m, "image_digest", return_value="sha256:test"):
            m.distribution(gh, plan)
            m.distribution(gh, plan)
        writes = [call for call in gh.repo.call_args_list if call.kwargs]
        self.assertEqual(len(writes), 1)
        self.assertFalse(release["draft"])
        self.assertTrue(release["prerelease"])
        self.assertEqual(release["make_latest"], "false")

    def test_rust_upload_packs_cargo_executables_and_skips_completed_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "custom-cli"
            binary.write_bytes(b"executable")
            binary.chmod(0o755)
            metadata = {"packages": [{"name": "app", "targets": [{"kind": ["bin"]}]}]}
            artifact = {"reason": "compiler-artifact", "target": {"kind": ["bin"]},
                        "executable": str(binary)}
            calls = []

            def run(*args):
                calls.append(args)
                if args[:2] == ("cargo", "metadata"):
                    return json.dumps(metadata).encode()
                if args[:2] == ("cargo", "build"):
                    return json.dumps(artifact).encode()
                self.assertEqual(args[:3], ("gh", "release", "upload"))
                with tarfile.open(args[4]) as archive:
                    self.assertEqual(archive.getnames(), ["custom-cli"])
                    self.assertEqual(archive.extractfile("custom-cli").read(), b"executable")
                    self.assertTrue(archive.getmember("custom-cli").mode & 0o111)
                return b""

            gh = mock.Mock()
            gh.pages.return_value = [self.asset()]
            with (
                mock.patch.object(Path, "read_text", return_value='[package]\nname="app"'),
                mock.patch.object(m, "run", side_effect=run),
            ):
                m.rust_asset(gh, {"id": 42, "draft": True}, "v1.2.3", [])
                m.rust_asset(gh, {"id": 42, "draft": False}, "v1.2.3", [self.asset()])
                with self.assertRaises(m.PreparationError):
                    m.rust_asset(gh, {"id": 42, "draft": False}, "v1.2.3", [])
            builds = [call for call in calls if call[:2] == ("cargo", "build")]
            self.assertEqual(len(builds), 1)
            self.assertIn("x86_64-unknown-linux-gnu", builds[0])
            self.assertIn("--locked", builds[0])

    def test_rust_library_has_no_binary_link_or_upload(self):
        metadata = {"packages": [{"name": "app", "targets": [{"kind": ["lib"]}]}]}
        with (
            mock.patch.object(Path, "read_text", return_value='[package]\nname="app"'),
            mock.patch.object(m, "run", return_value=json.dumps(metadata).encode()) as run,
        ):
            m.rust_asset(mock.Mock(), {"draft": True}, "v1.2.3", [])
        self.assertEqual(run.call_count, 1)

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
                self.assertEqual("release_args+=(--draft)" in source, rust)
                self.assertFalse((root / "_release_distribution.yml").exists())
