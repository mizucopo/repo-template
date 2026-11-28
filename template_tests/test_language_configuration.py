"""Copier migration tests for the shared Rust and Node language baselines."""

import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class LanguageConfigurationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.template = self.root / "template"
        self.project = self.root / "project"
        shutil.copytree(
            REPO_ROOT,
            self.template,
            ignore=shutil.ignore_patterns(".git", "__pycache__"),
        )
        self.init_repo(self.template)
        self.commit(self.template, "Initial shared language baseline")

    def run_tool(self, cwd: Path, *command: str) -> str:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            env={**os.environ, "NO_COLOR": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def init_repo(self, repo: Path) -> None:
        self.run_tool(repo, "git", "init", "-b", "main")
        self.run_tool(repo, "git", "config", "user.name", "Template Test")
        self.run_tool(repo, "git", "config", "user.email", "template-test@example.com")

    def commit(self, repo: Path, message: str) -> None:
        self.run_tool(repo, "git", "add", "-A")
        self.run_tool(repo, "git", "-c", "commit.gpgsign=false", "commit", "-m", message)

    def copy(self, *answers: str) -> None:
        arguments = [part for answer in answers for part in ("-d", answer)]
        self.run_tool(
            self.root,
            "copier",
            "copy",
            "--trust",
            "--defaults",
            "--vcs-ref=HEAD",
            *arguments,
            str(self.template),
            str(self.project),
        )
        self.assertFalse((self.project / "_language_toolchain").exists())
        self.assertFalse((self.project / "_language_toolchain.jinja").exists())
        self.init_repo(self.project)
        self.commit(self.project, "Generate from template")

    def update(self, *answers: str) -> None:
        arguments = [part for answer in answers for part in ("-d", answer)]
        self.run_tool(
            self.project,
            "copier",
            "update",
            "--trust",
            "--defaults",
            "--vcs-ref=HEAD",
            *arguments,
        )
        self.assertEqual(list(self.project.rglob("*.rej")), [])

    def verify_idempotent_update(self) -> None:
        self.commit(self.project, "Apply standard update")
        self.update()
        self.assertEqual(self.run_tool(self.project, "git", "status", "--porcelain"), "")

    def read_toml(self, path: Path) -> dict:
        return tomllib.loads(path.read_text())

    def read_json(self, path: Path) -> dict:
        return json.loads(path.read_text())

    @staticmethod
    def write_json(path: Path, fields: dict) -> None:
        path.write_text(json.dumps(fields, indent=2) + "\n")

    def test_rust_profile_is_opt_in_and_copier_preserves_existing_project(self) -> None:
        self.copy("use_rust=true", "rust_version=stable")
        manifest = self.project / "Cargo.toml"
        original = manifest.read_text()
        self.assertEqual(self.read_toml(manifest)["package"]["edition"], "2024")
        self.assertNotIn("profile", self.read_toml(manifest))
        self.assertFalse((self.project / "pyproject.toml").exists())
        manifest.write_text(
            original.replace('version = "0.1.0"', 'version = "4.12.7"\nrust-version = "1.85"')
            + 'fixture-helper = { path = "fixture-helper" }\n\n'
            + '[features]\nfixture-feature = []\n\n'
            + '[workspace]\nmembers = ["fixture-helper"]\n'
        )
        for name, contents in {
            "fixture-helper/Cargo.toml": '[package]\nname = "fixture-helper"\nversion = "1.0.0"\nedition = "2024"\n',
            "fixture-helper/src/lib.rs": "pub fn helper() {}\n",
            "build.rs": 'fn main() {\n    println!("cargo:rerun-if-changed=build.rs");\n}\n',
        }.items():
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
        self.run_tool(self.project, "cargo", "generate-lockfile", "--offline")
        lockfile = (self.project / "Cargo.lock").read_bytes()
        build_hook = (self.project / "build.rs").read_bytes()
        self.commit(self.project, "Project version, custom dependencies and build hook")

        baseline = self.template / "_language_toolchain.jinja"
        baseline.write_text(
            baseline.read_text().replace('["rustfmt", "clippy"]', '["clippy", "rustfmt"]')
        )
        self.commit(self.template, "Update common Rust toolchain")
        self.update("rust_cli_release_profile=thin-lto-strip")
        merged = self.read_toml(manifest)
        self.assertEqual(merged["package"]["version"], "4.12.7")
        self.assertEqual(merged["package"]["rust-version"], "1.85")
        self.assertEqual(merged["workspace"]["members"], ["fixture-helper"])
        self.assertEqual(merged["dependencies"], {"fixture-helper": {"path": "fixture-helper"}})
        self.assertEqual(merged["features"], {"fixture-feature": []})
        self.assertEqual(merged["profile"]["release"], {"lto": "thin", "strip": True})
        self.assertEqual(
            self.read_toml(self.project / "rust-toolchain.toml")["toolchain"]["components"],
            ["clippy", "rustfmt"],
        )
        self.assertEqual((self.project / "build.rs").read_bytes(), build_hook)
        self.assertEqual((self.project / "Cargo.lock").read_bytes(), lockfile)
        self.run_tool(self.project, "cargo", "fmt", "--all", "--check")
        self.run_tool(
            self.project, "cargo", "clippy", "--all-targets", "--all-features",
            "--locked", "--offline", "--", "-D", "warnings",
        )
        self.run_tool(
            self.project, "cargo", "test", "--all-targets", "--all-features",
            "--locked", "--offline",
        )
        self.assertEqual((self.project / "Cargo.lock").read_bytes(), lockfile)
        self.verify_idempotent_update()

    def assert_node_update(self, flavor: str) -> None:
        self.copy(f"use_{flavor}=true")
        manifest = self.project / "package.json"
        manifest_value = self.read_json(manifest)
        gate = manifest_value["scripts"]["check"]
        common = (
            "npm run lint && npm run format:check && npm run typecheck "
            "&& npm run test:run && npm run build"
        )
        expected = (
            common + " && npm run cargo:fmt && npm run cargo:clippy && npm run cargo:test"
            if flavor == "tauri"
            else common
        )
        self.assertEqual(gate, expected)
        manifest_value["name"] = "business-product"
        manifest_value["version"] = "4.12.7"
        manifest_value.setdefault("dependencies", {})["business-module"] = "1.2.3"
        manifest_value["devDependencies"]["business-report"] = "^3.0.0"
        manifest_value["scripts"]["build"] += " && node scripts/business-build.mjs"
        manifest_value["scripts"]["precheck"] = "node scripts/business-check.mjs"
        self.write_json(manifest, manifest_value)
        lock_path = self.project / "package-lock.json"
        self.write_json(
            lock_path,
            {
                "name": "business-product",
                "version": "4.12.7",
                "lockfileVersion": 3,
                "packages": {"node_modules/business-module": {"version": "1.2.3"}},
            },
        )
        lockfile = lock_path.read_bytes()
        preserved_paths = {}
        if flavor == "tauri":
            cargo = self.project / "src-tauri/Cargo.toml"
            cargo.write_text(
                cargo.read_text().replace('version = "0.1.0"', 'version = "4.12.7"')
                + 'business-module = { version = "1", features = ["fixture"] }\n'
            )
            self.assertNotIn("profile", self.read_toml(cargo))
            config = self.project / "src-tauri/tauri.conf.json"
            fields = self.read_json(config)
            fields.update({"version": "4.12.7", "identifier": "com.example.business"})
            self.write_json(config, fields)
            preserved_paths = {path: path.read_bytes() for path in (cargo, config)}
        else:
            config = self.project / "src/manifest.json"
            fields = self.read_json(config)
            fields.update({"version": "4.12.7", "name": "Business Product"})
            self.write_json(config, fields)
            preserved_paths = {config: config.read_bytes()}
        self.commit(self.project, "Preserve product metadata, deps, lock and custom hooks")

        baseline = self.template / "_language_toolchain.jinja"
        baseline.write_text(
            baseline.read_text().replace('"prettier --write ."', '"prettier --write src"')
        )
        self.commit(self.template, "Update shared Node format command")
        self.update()
        merged = self.read_json(manifest)
        manifest_value["scripts"]["format"] = "prettier --write src"
        self.assertEqual(merged, manifest_value)
        self.assertEqual(lock_path.read_bytes(), lockfile)
        for path, expected_contents in preserved_paths.items():
            self.assertEqual(path.read_bytes(), expected_contents)
        self.verify_idempotent_update()

    def test_chrome_manifest_update_preserves_project(self) -> None:
        self.assert_node_update("chrome_extension")

    def test_tauri_manifests_update_preserves_project(self) -> None:
        self.assert_node_update("tauri")


if __name__ == "__main__":
    unittest.main()
