import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import yaml
from test_release import m
import test_template


TARGETS = ["x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc",
           "aarch64-pc-windows-msvc", "aarch64-apple-darwin"]
SHA = "a" * 40


class RustReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "Cargo.toml").write_text('[package]\nname="app"\n')
        self.git = m.Git(self.root)
        self.plan = {"release_tag": "v1.2.3", "is_prerelease": False, "images": [],
                     "rust": {"targets": TARGETS, "smoke_args": ["--help"]}}
        self.artifacts = self.root / "artifacts"

    def build(self, target, *, targets=None, features=None, artifact_missing=False, real_smoke=False):
        windows = "windows" in target
        binary = self.root / ("custom-cli.exe" if windows else "custom-cli")
        binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        metadata = {"resolve": {"root": "root-id", "nodes": [{"id": "root-id", "features": features or []}]},
                    "packages": [{"name": "app", "id": "root-id", "features": {},
                                  "targets": targets if targets is not None else [
                                      {"name": "custom-cli", "kind": ["bin"]}]}]}
        artifact = {"reason": "compiler-artifact", "package_id": "root-id",
                    "target": {"kind": ["bin"]}, "executable": str(binary)}
        calls = []
        def run(*args, cwd, **kwargs):
            self.assertEqual(cwd, self.git.root)
            calls.append(args)
            if args[:2] == ("cargo", "metadata"):
                return json.dumps(metadata).encode()
            self.assertEqual(args[:2], ("cargo", "build"))
            self.assertIn("--locked", args)
            self.assertEqual(args[args.index("--target") + 1], target)
            self.assertNotIn("--features", args)
            return b'' if artifact_missing else json.dumps(artifact).encode()
        system = "Windows" if windows else "Darwin" if "darwin" in target else "Linux"
        machine = "ARM64" if "aarch64" in target else "AMD64"
        with (mock.patch.object(m, "run", side_effect=run),
              mock.patch.object(m.platform, "system", return_value=system),
              mock.patch.object(m.platform, "machine", return_value=machine),
              contextlib.ExitStack() as stack):
            if not real_smoke:
                stack.enter_context(mock.patch.object(m, "rust_smoke"))
            m.build_rust_distribution(self.git, self.plan, SHA, target, self.root / "built")
        source = self.root / "built" / target
        self.artifacts.mkdir(exist_ok=True)
        destination = self.artifacts / ("rust-release-" + target)
        shutil.copytree(source, destination, dirs_exist_ok=True)
        return destination, calls

    def seed_artifacts(self):
        for target in TARGETS:
            self.build(target)

    def github(self):
        release = {"id": 42, "tag_name": self.plan["release_tag"], "draft": True,
                   "body": "Hand-written notes.\n", "prerelease": False,
                   "upload_url": "https://uploads.example/releases/42/assets{?name,label}"}
        assets, content = [], {}
        gh = mock.Mock(repository="owner/app")
        gh.pages.side_effect = lambda path: [release] if path == "/releases" else assets.copy()
        def repo(path, **kwargs):
            if kwargs.get("method") == "DELETE":
                assets[:] = [a for a in assets if a["id"] != int(path.rsplit("/", 1)[1])]
            elif kwargs:
                release.update(kwargs["payload"])
            return dict(release)
        gh.repo.side_effect = repo
        uploads = []
        def run(*args, cwd, **kwargs):
            self.assertEqual(cwd, self.git.root)
            self.assertEqual(args[:2], ("gh", "api"))
            if "--input" not in args:
                return content[int(args[2].rsplit("/", 1)[1])]
            path = Path(args[args.index("--input") + 1])
            self.assertTrue(args[4].endswith("?name=" + m.parse.quote(path.name, safe="")))
            uploads.append(path.name)
            payload = path.read_bytes()
            asset_id = len(content) + 1
            content[asset_id] = payload
            assets.append({"id": asset_id, "name": path.name, "state": "uploaded",
                           "size": len(payload), "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                           "browser_download_url": "https://github.com/owner/app/releases/download/" + m.parse.quote(self.plan["release_tag"], safe="") + "/" + m.parse.quote(path.name, safe="")})
            return b""
        return gh, release, assets, content, uploads, run

    def distribute(self, gh, run):
        with (mock.patch.object(self.git, "text", return_value=SHA),
              mock.patch.dict(os.environ, {"BUILD_RUST_BINARY": "true", "RUST_RELEASE_DIR": str(self.artifacts), "RELEASE_ID": "42"}),
              mock.patch.object(m, "run", side_effect=run)):
            m.distribution(self.git, gh, self.plan)

    def test_native_packages_keep_names_exe_modes_checksums_and_start_after_extraction(self):
        names = ["app-v1.2.3-linux-x86_64.tar.gz", "app-v1.2.3-windows-x86_64.zip",
                 "app-v1.2.3-windows-arm64.zip", "app-v1.2.3-macos-arm64.tar.gz"]
        for target, name in zip(TARGETS, names):
            with self.subTest(target=target):
                destination, _ = self.build(target, real_smoke=target == TARGETS[0])
                archive = destination / name
                manifest = json.loads((destination / "manifest.json").read_text())
                executable = "custom-cli.exe" if "windows" in target else "custom-cli"
                self.assertEqual(manifest["binaries"], [executable])
                self.assertEqual(manifest["smoke"], "native")
                self.assertEqual((destination / (name + ".sha256")).read_text(),
                                 f"{m.file_sha256(archive)}  {name}\n")
                if archive.suffix == ".zip":
                    with zipfile.ZipFile(archive) as bundle:
                        self.assertEqual(bundle.namelist(), [executable])
                else:
                    with tarfile.open(archive) as bundle:
                        self.assertEqual(bundle.getnames(), [executable])
                        self.assertTrue(bundle.getmember(executable).mode & 0o111)
        self.assertEqual(len(m.rust_deliverables(self.git, self.plan, SHA, self.artifacts)), 4)

    def test_build_failure_missing_binary_and_foreign_runner_stop_before_packaging(self):
        with self.assertRaisesRegex(m.PreparationError, "all selected"):
            self.build(TARGETS[0], artifact_missing=True)
        with (mock.patch.object(m.platform, "system", return_value="Linux"),
              mock.patch.object(m.platform, "machine", return_value="x86_64"),
              mock.patch.object(m, "run") as run):
            with self.assertRaisesRegex(m.PreparationError, "native runner"):
                m.build_rust_distribution(self.git, self.plan, SHA, TARGETS[2], self.artifacts)
        run.assert_not_called()
        self.assertFalse(self.artifacts.exists())

    def test_library_and_disabled_optional_cli_have_manifests_without_archives(self):
        for targets in ([{"kind": ["lib"]}], [{"kind": ["bin"], "name": "optional", "required-features": ["cli"]}]):
            for target in TARGETS:
                destination, calls = self.build(target, targets=targets)
                self.assertEqual(calls[0][:2], ("cargo", "metadata"))
                self.assertEqual(len(calls), 1)
                self.assertEqual(list(destination.glob("*.tar.gz")), [])
            self.assertEqual(m.rust_deliverables(self.git, self.plan, SHA, self.artifacts), [])

    def test_cargo_resolved_default_features_enable_optional_executables(self):
        destination, calls = self.build(TARGETS[0], targets=[{
            "kind": ["bin"], "name": "custom-cli", "required-features": ["cli"],
        }], features=["default", "cli"])
        self.assertEqual(calls[1][:2], ("cargo", "build"))
        self.assertIsNotNone(json.loads((destination / "manifest.json").read_text())["archive"])

    def test_duplicate_or_unsupported_runtime_targets_are_rejected(self):
        for targets in ([], [TARGETS[0], TARGETS[0]], ["i686-pc-windows-msvc"]):
            with self.subTest(targets=targets):
                with self.assertRaises(m.PreparationError):
                    m.rust_config({"targets": targets})

    def test_missing_target_or_tampered_identity_checksum_and_content_keep_draft(self):
        self.seed_artifacts()
        gh, release, _, _, uploads, run = self.github()
        path = self.artifacts / ("rust-release-" + TARGETS[2]) / "manifest.json"
        original = path.read_bytes()
        for field, value in [("release_sha", "b" * 40), ("release_tag", "v9"),
                             ("target", TARGETS[0]), ("smoke", "cross-build"),
                             ("sha256", "0" * 64), ("binaries", ["missing.exe"])]:
            with self.subTest(field=field):
                changed = json.loads(original)
                changed[field] = value
                path.write_text(json.dumps(changed))
                with self.assertRaises(m.PreparationError):
                    self.distribute(gh, run)
                self.assertTrue(release["draft"])
                self.assertFalse(uploads)
        path.write_bytes(original)
        path.unlink()
        with self.assertRaises(OSError):
            self.distribute(gh, run)
        self.assertFalse(uploads)

    def test_rerun_preserves_retained_archive_bytes_and_links_all_actual_assets(self):
        self.seed_artifacts()
        gh, release, assets, content, uploads, run = self.github()
        self.distribute(gh, run)
        self.assertFalse(release["draft"])
        body = release["body"]
        self.assertTrue(body.startswith("Hand-written notes."))
        for asset in assets:
            self.assertIn(asset["browser_download_url"], body)
        first = dict(content)
        # A fresh rebuild can change compression bytes; uploaded assets are immutable.
        self.seed_artifacts()
        self.distribute(gh, run)
        self.assertEqual(content, first)
        self.assertEqual(len(uploads), 8)
        self.assertEqual(release["body"], body)
        self.assertEqual(len([c for c in gh.repo.call_args_list if c.kwargs.get("method") == "PATCH"]), 1)

    def test_partial_draft_resumes_without_overwriting_and_public_missing_assets_stop(self):
        self.seed_artifacts()
        gh, release, assets, content, uploads, run = self.github()
        self.distribute(gh, run)
        release["draft"] = True
        removed = assets.pop()
        assets.append({"id": 99, "name": removed["name"], "state": "new", "size": 0})
        self.distribute(gh, run)
        self.assertEqual(len(uploads), 9)
        self.assertIn(mock.call("/releases/assets/99", method="DELETE"), gh.repo.call_args_list)
        assets.pop()
        before = len(uploads)
        with self.assertRaisesRegex(m.PreparationError, "Published Rust distribution asset is missing"):
            self.distribute(gh, run)
        self.assertEqual(len(uploads), before)

    def test_wrong_retained_checksum_and_missing_image_prevent_publication(self):
        self.seed_artifacts()
        gh, release, assets, content, uploads, run = self.github()
        self.plan["images"] = [{"name": "main", "tag": "1.2.3", "repository": "owner/app"}]
        with mock.patch.object(m, "image_digest", return_value=None):
            with self.assertRaisesRegex(m.PreparationError, "image is missing"):
                self.distribute(gh, run)
        self.assertTrue(release["draft"])
        self.assertEqual(release["body"], "Hand-written notes.\n")
        checksum = next(a for a in assets if a["name"].endswith(".sha256"))
        expected = content[checksum["id"]]
        content[checksum["id"]] = b"invalid\n"
        with self.assertRaisesRegex(m.PreparationError, "checksum differs"):
            self.distribute(gh, run)
        self.assertTrue(release["draft"])
        content[checksum["id"]] = expected
        with mock.patch.object(m, "image_digest", return_value="sha256:test"):
            self.distribute(gh, run)
        self.assertFalse(release["draft"])
        self.assertEqual(len(uploads), 8)

    def test_archive_upload_failure_and_duplicate_assets_keep_draft(self):
        self.seed_artifacts()
        gh, release, assets, _, uploads, run = self.github()
        def missing(*args, **kwargs):
            value = run(*args, **kwargs)
            if "--input" in args:
                assets.clear()
            return value
        with self.assertRaisesRegex(m.PreparationError, "upload is incomplete"):
            self.distribute(gh, missing)
        self.assertTrue(release["draft"])
        self.distribute(gh, run)
        release["draft"] = True
        assets.append(dict(assets[0], id=99))
        with self.assertRaisesRegex(m.PreparationError, "Ambiguous"):
            self.distribute(gh, run)
        self.assertTrue(release["draft"])

    def test_metadata_and_tag_separator_are_literal_in_names_and_encoded_in_upload_urls(self):
        self.plan["release_tag"] = "releases/1.2.3+build.1"
        self.seed_artifacts()
        gh, release, _, _, uploads, run = self.github()
        self.distribute(gh, run)
        self.assertIn("app-releases%2F1.2.3+build.1-linux-x86_64.tar.gz", uploads)
        self.assertIn("%252F1.2.3%2Bbuild.1", release["body"])

    def test_smoke_failed_process_timeout_and_missing_member_are_errors(self):
        root, _ = self.build(TARGETS[0])
        archive = root / m.rust_name("app", "v1.2.3", TARGETS[0])
        for side_effect in (subprocess.CompletedProcess([], 1), subprocess.TimeoutExpired([], 30)):
            with mock.patch.object(m.subprocess, "run", side_effect=side_effect if isinstance(side_effect, Exception) else None,
                                   return_value=side_effect):
                with self.assertRaisesRegex(m.PreparationError, "startup smoke"):
                    m.rust_smoke(archive, ["custom-cli"], ["--help"])
        with self.assertRaisesRegex(m.PreparationError, "missing executables"):
            m.rust_smoke(archive, ["absent"], [])

    def test_cli_uses_selected_root_and_verifies_numbered_plan(self):
        with (contextlib.chdir(self.root.parent),
              mock.patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main", "RELEASE_SHA": SHA,
                                          "RUST_RELEASE_TARGET": TARGETS[2], "RUST_RELEASE_DIR": str(self.artifacts)}),
              mock.patch.object(sys, "argv", ["release.py", "rust-build", "--root", str(self.root)]),
              mock.patch.object(m, "GitHub"), mock.patch.object(m, "checkout_plan", return_value={"publication": self.plan}) as plan,
              mock.patch.object(m, "run") as run, mock.patch.object(m, "build_rust_distribution") as build):
            m.main()
        self.assertEqual(plan.call_args.args[0].root, str(self.root.resolve()))
        self.assertEqual(build.call_args.args[1:], (self.plan, SHA, TARGETS[2], str(self.artifacts)))
        self.assertEqual(run.call_args.args, ("rustup", "target", "add", TARGETS[2]))


class RustReleaseTemplateTest(unittest.TestCase):
    def setUp(self):
        helper = test_template.TemplateTest(methodName="runTest")
        self.addCleanup(helper.doCleanups)
        for name in ("copy_template", "copy_template_repository", "commit_repository",
                     "create_versioned_project", "update_versioned_project"):
            setattr(self, name, getattr(helper, name))

    def test_generated_normal_and_docker_releases_use_native_matrix_and_publication_barrier(self):
        for docker in (False, True):
            answers = ["use_rust=true", "rust_release_targets=" + json.dumps(TARGETS)]
            answers += ["use_docker=true", "use_gh_actions_docker_release=true"] if docker else ["use_gh_actions_release=true"]
            result, root = self.copy_template(*answers)
            self.assertEqual(result.returncode, 0, result.stdout)
            name = "docker-release.yml" if docker else "release.yml"
            workflow = yaml.safe_load((root / ".github/workflows" / name).read_text())
            policy = json.loads((root / ".github/release.json").read_text())
            self.assertEqual(policy["publication"]["rust"]["targets"], TARGETS)
            jobs = workflow["jobs"]
            self.assertEqual(jobs["release"]["needs"], ["prepare", "rust-builds"])
            builds = jobs["rust-builds"]
            self.assertEqual(builds["permissions"]["contents"], "read")
            self.assertEqual(builds["runs-on"], "${{ matrix.runner }}")
            self.assertIn("rust_matrix", str(builds["strategy"]))
            self.assertTrue(any("release.py rust-build" in s.get("run", "") for s in builds["steps"]))
            self.assertEqual(builds["env"]["RELEASE_SHA"], "${{ needs.prepare.outputs.release_sha }}")
            self.assertEqual(jobs["prepare"]["outputs"]["rust_matrix"], "${{ steps.rust-matrix.outputs.rust_matrix }}")
            for include in ("_release_rust_builds.yml", "_release_distribution.yml"):
                self.assertFalse((root / include).exists())

    def test_defaults_invalid_targets_and_tauri_isolation(self):
        result, root = self.copy_template("use_rust=true", "use_gh_actions_release=true")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads((root / ".github/release.json").read_text())["publication"]["rust"]["targets"], TARGETS[:1])
        for invalid in ([], ["i686-pc-windows-msvc"]):
            result, _ = self.copy_template("use_rust=true", "use_gh_actions_release=true", "rust_release_targets=" + json.dumps(invalid))
            self.assertNotEqual(result.returncode, 0, result.stdout)
        result, root = self.copy_template("use_tauri=true", "use_gh_actions_tauri_build=true")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("rust-builds", (root / ".github/workflows/tauri-build.yml").read_text())
        self.assertNotIn("rust", json.loads((root / ".github/release.json").read_text())["publication"])

    def test_copier_update_adds_targets_and_preserves_product_staging_hook(self):
        template = self.copy_template_repository()
        self.commit_repository(template, "initial template")
        project = self.create_versioned_project(template, "use_rust=true", "use_gh_actions_release=true")
        policy_path = project / ".github/release.json"
        policy_path.write_text(policy_path.read_text().replace(
            '"stage_command": []', '"stage_command": ["python", "scripts/package-extra.py"]'
        ))
        self.commit_repository(project, "project packaging hook")
        (template / "README.md").write_text((template / "README.md").read_text() + "\nTemplate update fixture.\n")
        self.commit_repository(template, "template update")
        result = self.update_versioned_project(project, "rust_release_targets=" + json.dumps(TARGETS))
        self.assertEqual(result.returncode, 0, result.stdout)
        policy = json.loads(policy_path.read_text())
        self.assertEqual(policy["publication"]["rust"]["targets"], TARGETS)
        self.assertEqual(policy["publication"]["rust"]["stage_command"], ["python", "scripts/package-extra.py"])
        self.assertFalse(list(project.rglob("*.rej")))


if __name__ == "__main__":
    unittest.main()
