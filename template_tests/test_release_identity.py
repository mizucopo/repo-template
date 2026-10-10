"""Exercise generated release steps with real gh and a loopback-only API."""
import json
import os
import shutil
import subprocess
import sys
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import test_template
from test_release import m
import yaml


class ReleaseIdentityTest(unittest.TestCase):
    def setUp(self):
        self.harness = test_template.TemplateTest()
        self.addCleanup(self.harness.doCleanups)
        copied, self.project = self.harness.copy_template(
            "use_python=false", "use_chrome_extension=true",
            "use_gh_actions_chrome_extension_release=true",
        )
        self.assertEqual(copied.returncode, 0, copied.stdout)
        self.harness.commit_repository(self.project, "Source")
        self.source = self.git("rev-parse", "HEAD")
        self.git("commit", "--allow-empty", "-m", f"Numbered\n\nRepo-Template-Release: 1\n"
                 f"Release-Source: {self.source}\nRelease-Run: 1\n"
                 "Release-Tag: 0.1.0\nRelease-Version: 0.1.0\nRelease-PRs: 1\n")
        self.git("tag", "0.1.0")
        origin = self.project.parent / "origin.git"
        self.git("init", "--bare", str(origin))
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "origin", "HEAD:main", "--tags")
        self.calls = []
        self.releases = []
        self.hide_created = True
        self.response_changes = {}
        self.fail_upload = False
        self.fail_create = False
        self.fail_publish = False
        self.fail_list = False
        self.expected_name = "project-0.1.0.zip"
        self.zip_bytes = b"distribution ZIP"
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, value, status=200, headers=None):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.calls.append(("GET", self.path, None))
                url = urlsplit(self.path)
                if "/git/ref/tags/" in url.path:
                    return self.respond({"object": {"sha": outer.source}})
                if url.path.endswith("/releases"):
                    if outer.fail_list:
                        return self.respond({}, 500)
                    # Matching releases are deliberately on the second page.
                    page = parse_qs(url.query).get("page", ["1"])[0]
                    if page == "1":
                        headers = {"Link": f'<{outer.base}/repos/owner/project/releases?per_page=100&page=2>; rel="next"'}
                        return self.respond([{"id": 100 + n, "tag_name": f"old-{n}", "draft": False}
                                             for n in range(100)], headers=headers)
                    return self.respond([] if outer.hide_created else outer.releases)
                release = outer.releases[0] if outer.releases else None
                if "/releases/tags/" in url.path:
                    return self.respond(release if release and not release["draft"] else {},
                                        200 if release and not release["draft"] else 404)
                if url.path.endswith("/releases/41") and release:
                    return self.respond({**release, **outer.response_changes})
                if url.path.endswith("/releases/41/assets") and release:
                    return self.respond(release["assets"])
                return self.respond({}, 404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                url = urlsplit(self.path)
                payload = body if "/assets" in url.path else json.loads(body)
                outer.calls.append(("POST", self.path, payload))
                if url.path.endswith("/generate-notes"):
                    return self.respond({"body": "Generated notes"})
                if url.path.endswith("/releases"):
                    release = {"id": 41, **payload, "assets": [],
                               "upload_url": outer.base + "/uploads/releases/41/assets{?name,label}"}
                    outer.releases.append(release)
                    if outer.fail_create:
                        outer.fail_create = False
                        return self.respond({}, 500)
                    return self.respond({**release, **outer.response_changes}, 201)
                if url.path.endswith("/assets"):
                    asset = {"id": 10, "name": parse_qs(url.query)["name"][0],
                             "state": "uploaded", "size": len(body),
                             "browser_download_url": outer.base + "/download.zip"}
                    if outer.fail_upload:
                        outer.fail_upload = False
                        outer.releases[0]["assets"].append({**asset, "state": "starter", "size": 0})
                        return self.respond({}, 500)
                    outer.releases[0]["assets"].append(asset)
                    return self.respond(asset, 201)
                return self.respond({}, 404)

            def do_DELETE(self):
                outer.calls.append(("DELETE", self.path, None))
                asset_id = int(self.path.rsplit("/", 1)[-1])
                outer.releases[0]["assets"] = [a for a in outer.releases[0]["assets"] if a["id"] != asset_id]
                return self.respond(None)

            def do_PATCH(self):
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                outer.calls.append(("PATCH", self.path, payload))
                outer.releases[0].update(payload)
                if outer.fail_publish:
                    outer.fail_publish = False
                    return self.respond({}, 500)
                return self.respond(outer.releases[0])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.output = self.project.parent / "outputs"
        self.notes = self.project.parent / "notes.md"
        self.notes.write_text("Hand-written notes\n")
        self.zip_path = self.project.parent / self.expected_name
        self.zip_path.write_bytes(self.zip_bytes)
        real_gh = shutil.which("gh")
        if real_gh is None:
            self.skipTest("gh is required for the loopback API test")
        tools = self.project.parent / "wire-bin"
        tools.mkdir()
        wrapper = tools / "gh"
        wrapper.write_text(f"#!{sys.executable}\n" + '''
import os, subprocess, sys
from urllib.parse import urlsplit
args = sys.argv[1:]
assert args[0] == "api", "Only gh api is permitted"
positions = [i for i, a in enumerate(args) if a.startswith(("/repos/", "http://", "https://"))]
assert len(positions) == 1
index = positions[0]
url = urlsplit(args[index])
assert not url.scheme or url.hostname == "127.0.0.1", "External endpoint blocked"
args[index] = os.environ["WIRE_BASE"] + url.path + ("?" + url.query if url.query else "")
raise SystemExit(subprocess.call([os.environ["REAL_GH"], *args]))
''')
        wrapper.chmod(0o755)
        self.env = {
            **os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "REAL_GH": real_gh, "WIRE_BASE": self.base,
            "GH_TOKEN": "dummy-loopback-token", "GH_CONFIG_DIR": str(tools / "empty-config"),
            "GITHUB_API_URL": self.base, "GITHUB_REPOSITORY": "owner/project",
            "GITHUB_REF": "refs/heads/main", "RELEASE_SHA": self.git("rev-parse", "HEAD"),
            "GITHUB_OUTPUT": str(self.output), "TAG": "0.1.0",
            "INSPECT_DRAFT_RELEASES": "true", "RELEASE_DRAFT": "true",
            "RELEASE_NOTES_PATH": str(self.notes), "RELEASE_ASSET_NAME": self.expected_name,
            "ZIP_PATH": str(self.zip_path),
        }
        self.steps = {s["name"]: s for s in yaml.safe_load(
            (self.project / ".github/workflows/chrome-extension-release.yml").read_text()
        )["jobs"]["release"]["steps"]}

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.project, stderr=subprocess.DEVNULL, text=True).strip()

    def run_step(self, name):
        self.output.unlink(missing_ok=True)
        result = subprocess.run(["bash", "-e", "-o", "pipefail"], input=self.steps[name]["run"],
                                cwd=self.project, env=self.env, text=True, capture_output=True)
        if self.output.exists():
            return result, dict(line.split("=", 1) for line in self.output.read_text().splitlines())
        return result, {}

    def succeeded(self, name):
        result, values = self.run_step(name)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return values

    def complete(self):
        state = self.succeeded("Inspect release state")
        if state["release_exists"] != "true":
            self.env["RELEASE_ID"] = self.succeeded("Create draft")["release_id"]
        else:
            self.env["RELEASE_ID"] = state["release_id"]
        if state["release_asset_exists"] != "true":
            self.succeeded("Upload missing distribution")
        if state["release_is_draft"] == "true" or state["release_exists"] != "true":
            self.succeeded("Publish complete draft")

    def test_hidden_created_release_completes_and_published_rerun_preserves_assets(self):
        self.complete()
        self.assertFalse(self.releases[0]["draft"])
        self.assertEqual(self.releases[0]["body"], self.notes.read_text())
        self.assertEqual(self.releases[0]["make_latest"], "false")
        self.assertEqual(next(payload for method, path, payload in self.calls if "/uploads/" in path), self.zip_bytes)
        self.hide_created = False
        before = json.loads(json.dumps(self.releases))
        self.calls.clear()
        self.complete()
        self.assertEqual(self.releases, before)
        self.assertFalse(any(method != "GET" for method, _, _ in self.calls))

    def test_response_loss_and_upload_interruption_resume_without_duplicates(self):
        for failure, step in [("fail_create", "Create draft"), ("fail_upload", "Upload missing distribution"),
                              ("fail_publish", "Publish complete draft")]:
            with self.subTest(failure=failure):
                self.releases.clear()
                self.calls.clear()
                self.env.pop("RELEASE_ID", None)
                setattr(self, failure, True)
                if step != "Create draft":
                    self.env["RELEASE_ID"] = self.succeeded("Create draft")["release_id"]
                if step == "Publish complete draft":
                    self.succeeded("Upload missing distribution")
                result, _ = self.run_step(step)
                self.assertNotEqual(result.returncode, 0)
                self.hide_created = False
                self.env.pop("RELEASE_ID", None)
                self.complete()
                self.assertEqual(len(self.releases), 1)
                self.assertFalse(self.releases[0]["draft"])
                self.assertEqual(len(self.releases[0]["assets"]), 1)
                self.hide_created = True

    def test_retained_id_still_checks_all_pages_and_complete_asset(self):
        self.env["RELEASE_ID"] = self.succeeded("Create draft")["release_id"]
        self.succeeded("Upload missing distribution")
        for changes in ({"id": 42}, {"tag_name": "other"}, {"draft": False}, {"assets": []}):
            with self.subTest(changes=changes):
                self.response_changes = changes
                result, _ = self.run_step("Publish complete draft")
                self.assertNotEqual(result.returncode, 0)
        self.response_changes = {}
        self.hide_created = False
        self.releases.append({**self.releases[0], "id": 42})
        result, _ = self.run_step("Publish complete draft")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(method == "PATCH" for method, _, _ in self.calls))
        self.releases.pop()
        self.fail_list = True
        result, _ = self.run_step("Publish complete draft")
        self.assertNotEqual(result.returncode, 0)

    def test_invalid_create_response_stops_before_upload(self):
        for changes in ({"id": True}, {"id": 0}, {"id": "41"}, {"tag_name": "other"},
                        {"draft": False}, {"prerelease": True}):
            with self.subTest(changes=changes):
                self.releases.clear()
                self.response_changes = changes
                result, values = self.run_step("Create draft")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("release_id", values)
        self.assertFalse(any("/uploads/" in path for _, path, _ in self.calls))

    def test_common_distribution_keeps_created_id_and_published_assets(self):
        self.env["RELEASE_DRAFT"] = "false"
        self.env.pop("RELEASE_NOTES_PATH")
        self.env["RELEASE_ID"] = self.succeeded("Create draft")["release_id"]
        self.assertEqual(self.releases[0]["body"], "Generated notes")
        self.releases[0]["assets"] = [{"id": 10, "name": self.expected_name,
            "state": "uploaded", "size": 10, "browser_download_url": self.base + "/download.zip"}]
        plan = {"release_tag": "0.1.0", "is_prerelease": False, "images": []}
        with mock.patch.dict(os.environ, self.env):
            m.distribution(m.Git(self.project), m.GitHub(), plan)
            before = json.loads(json.dumps(self.releases))
            self.calls.clear()
            m.distribution(m.Git(self.project), m.GitHub(), plan)
        self.assertEqual(self.releases, before)
        self.assertFalse(any(method != "GET" for method, _, _ in self.calls))
        self.assertIn(self.base + "/download.zip", self.releases[0]["body"])

    def test_rust_upload_uses_created_id_and_preserves_completed_archive(self):
        self.env["RELEASE_ID"] = self.succeeded("Create draft")["release_id"]
        (self.project / "Cargo.toml").write_text('[package]\nname="app"\n')
        binary = self.project / "custom-cli"
        binary.write_bytes(b"executable")
        binary.chmod(0o755)
        original_run = m.run
        def run(*args, **kwargs):
            if args[:2] == ("cargo", "metadata"):
                return json.dumps({"packages": [{"name": "app", "targets": [{"kind": ["bin"]}]}]}).encode()
            if args[:2] == ("cargo", "build"):
                return json.dumps({"reason": "compiler-artifact", "target": {"kind": ["bin"]},
                                   "executable": str(binary)}).encode()
            return original_run(*args, **kwargs)
        plan = {"release_tag": "0.1.0", "is_prerelease": False, "images": []}
        with mock.patch.dict(os.environ, {**self.env, "BUILD_RUST_BINARY": "true"}), mock.patch.object(m, "run", side_effect=run):
            m.distribution(m.Git(self.project), m.GitHub(), plan)
            before = json.loads(json.dumps(self.releases))
            self.calls.clear()
            m.distribution(m.Git(self.project), m.GitHub(), plan)
        self.assertEqual(self.releases, before)
        self.assertFalse(self.releases[0]["draft"])
        self.assertEqual(self.releases[0]["assets"][0]["name"], "app-0.1.0-linux-x86_64.tar.gz")
        self.assertFalse(any(method != "GET" for method, _, _ in self.calls))
