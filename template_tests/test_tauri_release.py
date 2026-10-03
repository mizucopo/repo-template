import json
import os
import unittest
from pathlib import Path

from template_tests import test_template


FAKE_GITHUB = '''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

path = Path(os.environ["FAKE_GITHUB_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
state["calls"].append([Path(sys.argv[0]).name, *args])

def finish(value=None, code=0):
    path.write_text(json.dumps(state))
    if value is not None:
        print(json.dumps(value))
    sys.exit(code)

def reject(message):
    print(message, file=sys.stderr)
    finish(code=1)

def release_for_tag(tag):
    return next((r for r in state["releases"] if r["tag_name"] == tag), None)

if Path(sys.argv[0]).name == "curl":
    tag = args[-1].rsplit("/", 1)[-1]
    release = release_for_tag(tag)
    published = release is not None and not release["draft"]
    Path(args[args.index("--output") + 1]).write_text(
        json.dumps(release if published else {"message": "Not Found"})
    )
    print(state.get("http_status", "200" if published else "404"), end="")
    finish(code=state.get("curl_exit", 0))

if args[0] == "api":
    endpoint = next(a for a in args[1:] if a.startswith("/repos/"))
    if endpoint.endswith("/releases?per_page=100"):
        if state.get("list_failure"):
            reject("Draft listing failed")
        if "--paginate" not in args or "--slurp" not in args:
            reject("Release listing must include every page")
        finish([[{"id": 9, "tag_name": "older", "draft": False, "assets": []}],
                state["releases"]])
    release_id = int(endpoint.rsplit("/", 1)[-1])
    release = next((r for r in state["releases"] if r["id"] == release_id), None)
    if release is None:
        reject("Unknown release ID")
    if "PATCH" in args:
        fields = dict(a.split("=", 1) for a in args if "=" in a)
        if fields.get("draft") != "false" or fields.get("make_latest") != "false":
            reject("Publication must explicitly avoid Latest")
        if not all(any(a["name"] == name and a["state"] == "uploaded" and a["size"] > 0
                       for a in release["assets"]) for name in state["expected_assets"]):
            reject("Cannot publish incomplete assets")
        release["draft"] = False
        release["prerelease"] = fields.get("prerelease") == "true"
        if state.pop("fail_publish_response", False):
            reject("Publication succeeded but the response was lost")
    finish(release)

if args[:2] == ["release", "create"]:
    if release_for_tag(args[2]) is not None:
        reject("A draft already exists for this tag")
    state["releases"].append({"id": 41, "tag_name": args[2], "draft": True,
                              "prerelease": "--prerelease" in args, "assets": []})
    if "--draft" not in args:
        reject("Release must be created as a draft")
    if state.pop("fail_create_response", False):
        reject("Draft created but the response was lost")
    finish()

if args[:2] == ["release", "upload"]:
    release = release_for_tag(args[2])
    if release is None or not release["draft"]:
        reject("Only draft assets may be changed")
    asset_path = Path(args[3])
    if not asset_path.is_file():
        reject("Missing local asset")
    existing = next((a for a in release["assets"] if a["name"] == asset_path.name), None)
    if existing is not None:
        if "--clobber" not in args:
            reject("Asset name already exists")
        release["assets"].remove(existing)
    uploaded = sum(a["state"] == "uploaded" for a in release["assets"])
    if state.get("fail_upload_after") == uploaded:
        state.pop("fail_upload_after")
        release["assets"].append({"name": asset_path.name, "state": "starter", "size": 0})
        reject("Upload interrupted")
    release["assets"].append({"name": asset_path.name, "state": "uploaded",
                              "size": asset_path.stat().st_size})
    finish()

reject("Unexpected GitHub command: " + repr(args))
'''


class TauriReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = test_template.TemplateTest()
        self.addCleanup(self.harness.doCleanups)
        copied, self.project = self.harness.copy_template(
            "use_tauri=true", "use_gh_actions_tauri_build=true"
        )
        self.assertEqual(copied.returncode, 0, copied.stdout)
        self.harness.commit_repository(self.project, "Release source")
        origin = self.project.parent / "origin.git"
        for args in (
            ["git", "tag", "0.1.0"],
            ["git", "init", "--bare", str(origin)],
            ["git", "remote", "add", "origin", str(origin)],
        ):
            result = self.harness.run_process(args, self.project)
            self.assertEqual(result.returncode, 0, result.stdout)
        mock_bin = self.project.parent / "mock-bin"
        mock_bin.mkdir()
        for command in ("curl", "gh"):
            mock = mock_bin / command
            mock.write_text(FAKE_GITHUB)
            mock.chmod(0o755)
        self.asset_names = [
            f"project-0.1.0-{platform}.zip"
            for platform in ("windows-x64", "windows-arm64", "macos-arm64")
        ]
        self.output = self.project.parent / "state-output.txt"
        self.state_path = self.project.parent / "github-state.json"
        self.write_state(releases=[])
        self.env = {
            **os.environ,
            "PATH": f"{mock_bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GITHUB_STATE": str(self.state_path),
            "GITHUB_API_URL": "https://api.github.example",
            "GITHUB_REPOSITORY": "owner/project",
            "GITHUB_OUTPUT": str(self.output),
            "GH_TOKEN": "test-token",
            "TAG": "0.1.0",
            "ZIP_PREFIX": "project-0.1.0",
            "IS_PRERELEASE": "false",
            "RELEASE_ASSET_NAMES": "|".join(self.asset_names),
            "INSPECT_DRAFT_RELEASES": "true",
            "RUNNER_TEMP": str(self.project.parent),
        }
        asset_dir = self.project.parent / "release-assets"
        asset_dir.mkdir()
        for name in self.asset_names:
            (asset_dir / name).write_bytes(b"distribution ZIP")

    def write_state(self, **values: object) -> None:
        self.state_path.write_text(json.dumps({
            "calls": [], "expected_assets": self.asset_names, **values,
        }))

    def state(self) -> dict:
        return json.loads(self.state_path.read_text())

    def release(self, count: int, *, draft: bool = True) -> dict:
        return {
            "id": 41, "tag_name": "0.1.0", "draft": draft, "prerelease": False,
            "assets": [
                {"name": name, "state": "uploaded", "size": 100}
                for name in self.asset_names[:count]
            ],
        }

    def run_step(self, name: str, **env: str):
        self.output.unlink(missing_ok=True)
        return self.harness.run_process(
            ["bash", "-e"], self.project, env={**self.env, **env},
            script=self.harness.workflow_step_script(self.project, "tauri-build.yml", name),
        )

    def inspect(self) -> dict[str, str]:
        result = self.run_step("Inspect release state")
        self.assertEqual(result.returncode, 0, result.stdout)
        return dict(line.split("=", 1) for line in self.output.read_text().splitlines())

    def test_inspection_finds_partial_and_complete_drafts(self) -> None:
        for count in (0, 1, 3):
            with self.subTest(asset_count=count):
                self.write_state(releases=[self.release(count)])
                outputs = self.inspect()
                self.assertEqual(outputs["release_exists"], "true")
                self.assertEqual(outputs["release_is_draft"], "true")
                self.assertEqual(outputs["release_asset_exists"], str(count == 3).lower())

    def assert_published(self, *, prerelease: bool = False) -> None:
        releases = self.state()["releases"]
        self.assertEqual(len(releases), 1)
        self.assertFalse(releases[0]["draft"])
        self.assertEqual(releases[0]["prerelease"], prerelease)
        self.assertEqual(
            {a["name"] for a in releases[0]["assets"]}, set(self.asset_names)
        )
        self.assertTrue(all(
            a["state"] == "uploaded" and a["size"] > 0 for a in releases[0]["assets"]
        ))

    def test_partial_draft_uploads_only_missing_or_failed_assets(self) -> None:
        release = self.release(1)
        release["assets"].append({
            "name": self.asset_names[1], "state": "starter", "size": 0,
        })
        self.write_state(releases=[release])
        result = self.run_step("Create GitHub Release")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_published()
        uploads = [c for c in self.state()["calls"] if c[1:3] == ["release", "upload"]]
        self.assertEqual([Path(c[4]).name for c in uploads], self.asset_names[1:])
        self.assertEqual(self.state()["releases"][0]["assets"][0], release["assets"][0])
        self.assertFalse(any(c[1:3] == ["release", "create"] for c in self.state()["calls"]))

    def test_complete_draft_skips_build_and_is_published(self) -> None:
        self.write_state(releases=[self.release(3)])
        outputs = self.inspect()
        decision = self.run_step(
            "Decide whether to build", RELEASE_EXISTS=outputs["release_exists"],
            RELEASE_ASSET_EXISTS=outputs["release_asset_exists"],
        )
        self.assertEqual(decision.returncode, 0, decision.stdout)
        self.assertEqual(self.output.read_text(), "needs_build=false\n")
        for name in self.asset_names:
            (self.project.parent / "release-assets" / name).unlink()
        result = self.run_step("Create GitHub Release")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_published()
        self.assertFalse(any(c[1:3] == ["release", "upload"] for c in self.state()["calls"]))

    def test_uploaded_asset_with_invalid_size_is_replaced(self) -> None:
        for size in (0, "100"):
            with self.subTest(size=size):
                release = self.release(3)
                release["assets"][1]["size"] = size
                self.write_state(releases=[release])
                self.assertEqual(self.inspect()["release_asset_exists"], "false")
                result = self.run_step("Create GitHub Release")
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assert_published()
                uploads = [c for c in self.state()["calls"] if c[1:3] == ["release", "upload"]]
                self.assertEqual([Path(c[4]).name for c in uploads], [self.asset_names[1]])

    def test_new_release_and_rerun_are_idempotent(self) -> None:
        self.assertEqual(self.inspect()["release_exists"], "false")
        first = self.run_step("Create GitHub Release")
        self.assertEqual(first.returncode, 0, first.stdout)
        self.assert_published()
        calls = self.state()["calls"]
        self.assertEqual(sum(c[1:3] == ["release", "create"] for c in calls), 1)
        self.assertEqual(sum(c[1:3] == ["release", "upload"] for c in calls), 3)
        creation = next(c for c in calls if c[1:3] == ["release", "create"])
        self.assertIn("--draft", creation)
        self.assertIn("--verify-tag", creation)
        self.assertIn("--latest=false", creation)
        self.assertEqual(creation[creation.index("--title") + 1], self.env["TAG"])
        second = self.run_step("Create GitHub Release")
        self.assertEqual(second.returncode, 0, second.stdout)
        self.assert_published()
        self.assertEqual(len([c for c in self.state()["calls"] if "PATCH" in c]), 1)

    def test_rerun_recovers_after_upload_interruption(self) -> None:
        self.write_state(releases=[], fail_upload_after=1)
        failed = self.run_step("Create GitHub Release")
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("Upload interrupted", failed.stdout)
        self.assertTrue(self.state()["releases"][0]["draft"])
        self.assertFalse(any("PATCH" in c for c in self.state()["calls"]))
        self.assertEqual(self.inspect()["release_asset_exists"], "false")
        recovered = self.run_step("Create GitHub Release")
        self.assertEqual(recovered.returncode, 0, recovered.stdout)
        self.assert_published()
        uploads = [c for c in self.state()["calls"] if c[1:3] == ["release", "upload"]]
        self.assertEqual(sum(Path(c[4]).name == self.asset_names[0] for c in uploads), 1)

    def test_rerun_recovers_after_draft_creation_response_failure(self) -> None:
        self.write_state(releases=[], fail_create_response=True)
        failed = self.run_step("Create GitHub Release")
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("Draft created but the response was lost", failed.stdout)
        self.assertTrue(self.state()["releases"][0]["draft"])
        self.assertEqual(self.state()["releases"][0]["assets"], [])
        recovered = self.run_step("Create GitHub Release")
        self.assertEqual(recovered.returncode, 0, recovered.stdout)
        self.assert_published()
        self.assertEqual(sum(
            c[1:3] == ["release", "create"] for c in self.state()["calls"]
        ), 1)

    def test_rerun_recovers_after_publication_response_failure(self) -> None:
        self.write_state(releases=[], fail_publish_response=True)
        failed = self.run_step("Create GitHub Release")
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("response was lost", failed.stdout)
        self.assert_published()
        self.assertEqual(self.inspect()["release_asset_exists"], "true")
        recovered = self.run_step("Create GitHub Release")
        self.assertEqual(recovered.returncode, 0, recovered.stdout)
        self.assertEqual(len([c for c in self.state()["calls"] if "PATCH" in c]), 1)

    def test_published_missing_assets_is_rejected(self) -> None:
        self.write_state(releases=[self.release(2, draft=False)])
        self.assertEqual(self.inspect()["release_asset_exists"], "false")
        result = self.run_step("Create GitHub Release")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing", result.stdout)
        self.assertFalse(any(c[1] == "release" for c in self.state()["calls"]))

    def test_published_release_with_duplicate_draft_blocks_mutations(self) -> None:
        self.write_state(releases=[
            self.release(3, draft=False), {**self.release(1), "id": 42},
        ])
        for step in ("Inspect release state", "Create GitHub Release"):
            with self.subTest(step=step):
                result = self.run_step(step)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("Multiple GitHub Releases match the tag", result.stdout)
                self.assertFalse(self.output.exists())
                self.assertFalse(any(c[1] == "release" or "PATCH" in c
                                     for c in self.state()["calls"]))

    def test_published_release_listing_failure_blocks_mutations(self) -> None:
        self.write_state(releases=[self.release(3, draft=False)], list_failure=True)
        result = self.run_step("Create GitHub Release")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Could not inspect draft GitHub Releases", result.stdout)
        self.assertFalse(any(c[1] == "release" or "PATCH" in c
                             for c in self.state()["calls"]))

    def test_prerelease_publication_is_explicitly_non_latest(self) -> None:
        self.write_state(releases=[self.release(3)])
        result = self.run_step("Create GitHub Release", IS_PRERELEASE="true")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_published(prerelease=True)
        publication = next(c for c in self.state()["calls"] if "PATCH" in c)
        self.assertIn("make_latest=false", publication)

    def test_new_prerelease_has_correct_creation_metadata(self) -> None:
        result = self.run_step("Create GitHub Release", IS_PRERELEASE="true")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_published(prerelease=True)
        creation = next(c for c in self.state()["calls"] if c[1:3] == ["release", "create"])
        self.assertIn("--prerelease", creation)
        self.assertIn("--draft", creation)
        self.assertIn("--latest=false", creation)
        self.assertEqual(creation[creation.index("--title") + 1], self.env["TAG"])

    def test_draft_without_matching_tag_is_rejected(self) -> None:
        self.write_state(releases=[self.release(3)])
        deleted = self.harness.run_process(["git", "tag", "-d", "0.1.0"], self.project)
        self.assertEqual(deleted.returncode, 0, deleted.stdout)
        result = self.run_step("Inspect release state")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("matching git tag was not found", result.stdout)

    def test_api_failures_and_duplicate_drafts_block_mutations(self) -> None:
        for failure in (
            {"http_status": "500"},
            {"curl_exit": 7},
            {"list_failure": True},
            {"releases": [self.release(1), {**self.release(1), "id": 42}]},
        ):
            with self.subTest(failure=failure):
                self.write_state(**{"releases": [], **failure})
                result = self.run_step("Create GitHub Release")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(c[1] == "release" for c in self.state()["calls"]))
