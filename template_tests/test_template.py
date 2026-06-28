import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class TemplateTest(unittest.TestCase):
    def copy_template_into(
        self, destination: Path, *answers: str
    ) -> subprocess.CompletedProcess[str]:
        command = ["copier", "copy", "--trust", "--defaults"]
        for answer in answers:
            command.extend(["-d", answer])
        command.extend([str(REPO_ROOT), str(destination)])

        return subprocess.run(
            command,
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def recopy_template(self, destination: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["copier", "recopy", "--trust", "-f", str(destination)],
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def copy_template(self, *answers: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        destination_root = tempfile.TemporaryDirectory()
        self.addCleanup(destination_root.cleanup)

        destination = Path(destination_root.name) / "project"
        result = self.copy_template_into(destination, *answers)
        return result, destination

    def test_chrome_manifest_json_values_are_escaped(self) -> None:
        name = 'Quote " Name \\ Test'
        description = 'Description with "quote" and \\ slash'

        result, destination = self.copy_template(
            "use_chrome_extension=true",
            f"chrome_extension_name={name}",
            f"chrome_extension_description={description}",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        manifest = json.loads((destination / "src/manifest.json").read_text())
        package = json.loads((destination / "package.json").read_text())

        self.assertEqual(manifest["name"], name)
        self.assertEqual(manifest["description"], description)
        self.assertEqual(package["description"], description)

    def test_chrome_package_author_is_escaped(self) -> None:
        author_name = 'Quote " Author \\ Name'

        result, destination = self.copy_template(
            "use_chrome_extension=true",
            f"author_name={author_name}",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        package = json.loads((destination / "package.json").read_text())

        self.assertEqual(package["author"], author_name)

    def test_long_chrome_extension_name_is_already_formatted(self) -> None:
        long_name = "Very Long Chrome Extension Name For Formatting"

        result, destination = self.copy_template(
            "use_chrome_extension=true",
            f"chrome_extension_name={long_name}",
        )
        self.assertEqual(result.returncode, 0, result.stdout)

        install_result = subprocess.run(
            ["npm", "install", "--prefix", str(destination)],
            check=False,
            env={**os.environ, "npm_config_cache": "/private/tmp/codex-npm-cache"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.assertEqual(install_result.returncode, 0, install_result.stdout)

        format_result = subprocess.run(
            ["npm", "--prefix", str(destination), "run", "format:check"],
            check=False,
            env={**os.environ, "npm_config_cache": "/private/tmp/codex-npm-cache"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.assertEqual(format_result.returncode, 0, format_result.stdout)

    def test_invalid_chrome_manifest_version_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_chrome_extension=true",
            "chrome_extension_version=1.0.0-beta.1",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Chrome Extension バージョン", result.stdout)

    def test_existing_chrome_extension_adoption_keeps_existing_js_project(
        self,
    ) -> None:
        destination_root = tempfile.TemporaryDirectory()
        self.addCleanup(destination_root.cleanup)

        destination = Path(destination_root.name) / "existing-extension"
        (destination / "src").mkdir(parents=True)
        (destination / "test").mkdir()
        (destination / "tests").mkdir()
        (destination / ".github/workflows").mkdir(parents=True)

        existing_files = {
            "package.json": json.dumps(
                {
                    "name": "voice-live-comment",
                    "version": "1.2.3",
                    "scripts": {"test": "node test/existing.test.js"},
                }
            )
            + "\n",
            "manifest.json": '{"manifest_version":3,"name":"Existing Root Manifest"}\n',
            "options.html": "<!doctype html><main id=\"options\"></main>\n",
            "rollup.config.mjs": "export default { input: 'src/background.js' };\n",
            "vitest.config.js": "export default { test: { environment: 'jsdom' } };\n",
            "src/manifest.json": '{"manifest_version":3,"name":"Existing JS Extension"}\n',
            "src/background.js": "chrome.runtime.onInstalled.addListener(() => {});\n",
            "test/existing.test.js": "console.log('existing test');\n",
            "tests/options.test.js": "console.log('existing options test');\n",
            ".github/workflows/release.yml": "name: Existing Release\n",
        }
        for relative_path, content in existing_files.items():
            (destination / relative_path).write_text(content)

        result = self.copy_template_into(
            destination,
            "use_chrome_extension=true",
            "chrome_extension_mode=adopt_existing",
            "use_mit_license=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        recopy_result = self.recopy_template(destination)
        self.assertEqual(recopy_result.returncode, 0, recopy_result.stdout)

        for relative_path, content in existing_files.items():
            self.assertEqual((destination / relative_path).read_text(), content)
        self.assertTrue((destination / ".node-version").exists())
        self.assertTrue((destination / "AGENTS.md").exists())
        self.assertTrue((destination / "CLAUDE.md").exists())
        self.assertTrue((destination / "LICENSE").exists())

        answers = (destination / ".copier-answers.yml").read_text()
        self.assertIn("use_chrome_extension: true", answers)
        self.assertIn("chrome_extension_mode: adopt_existing", answers)

        for guidance_path in ("AGENTS.md", "CLAUDE.md"):
            guidance = (destination / guidance_path).read_text()
            self.assertIn("existing JavaScript Manifest V3 implementation", guidance)
            self.assertIn("Keep runtime JavaScript in `src/`", guidance)
            self.assertIn("generated output in `dist/`", guidance)
            self.assertIn("Keep Chrome API mocks at entrypoint boundaries", guidance)
            self.assertIn("Prefer the existing project quality gate", guidance)

        starter_paths = [
            "src/background.ts",
            "src/popup.ts",
            "src/popup.html",
            "src/popup.css",
            "src/lib/extension-title.ts",
            "tests/lib/extension-title.test.ts",
            "scripts/copy-extension-assets.mjs",
            "scripts/clean-dist.mjs",
            ".github/workflows/chrome-extension-quality-checks.yml",
            "tsconfig.json",
            "tsconfig.build.json",
            "eslint.config.mjs",
            "vitest.config.ts",
            ".prettierrc.json",
            ".prettierignore",
        ]
        for starter_path in starter_paths:
            self.assertFalse((destination / starter_path).exists(), starter_path)

    def test_chrome_version_source_wins_when_python_and_rust_are_enabled(self) -> None:
        result, destination = self.copy_template(
            "use_python=true",
            "use_rust=true",
            "use_chrome_extension=true",
            "use_gh_actions_release=true",
            "use_gh_actions_pr_tag_check=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        release_workflow = (destination / ".github/workflows/release.yml").read_text()
        tag_check_workflow = (destination / ".github/workflows/pr-tag-check.yml").read_text()

        self.assertIn("package.json", release_workflow)
        self.assertIn("package.json", tag_check_workflow)
        self.assertNotIn("Cargo.toml", release_workflow)
        self.assertNotIn("Cargo.toml", tag_check_workflow)

    def test_python_version_source_wins_when_rust_is_also_enabled(self) -> None:
        result, destination = self.copy_template(
            "use_python=true",
            "use_rust=true",
            "use_gh_actions_release=true",
            "use_gh_actions_pr_tag_check=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        release_workflow = (destination / ".github/workflows/release.yml").read_text()
        tag_check_workflow = (destination / ".github/workflows/pr-tag-check.yml").read_text()

        self.assertIn("pyproject.toml", release_workflow)
        self.assertIn("pyproject.toml", tag_check_workflow)
        self.assertNotIn("Cargo.toml", release_workflow)
        self.assertNotIn("Cargo.toml", tag_check_workflow)

    def test_rust_template_generates_cargo_project(self) -> None:
        result, destination = self.copy_template(
            "use_rust=true",
            "rust_version=1.88.0",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        cargo_toml = (destination / "Cargo.toml").read_text()
        rust_toolchain = (destination / "rust-toolchain.toml").read_text()
        main_rs = (destination / "src/main.rs").read_text()
        rust_workflow = (
            destination / ".github/workflows/rust-quality-checks.yml"
        ).read_text()

        self.assertIn('name = "test-project"', cargo_toml)
        self.assertIn('version = "0.1.0"', cargo_toml)
        self.assertIn('channel = "1.88.0"', rust_toolchain)
        self.assertIn('println!("Hello, world!");', main_rs)
        self.assertIn("cargo fmt --all --check", rust_workflow)
        self.assertIn("cargo clippy --all-targets --all-features", rust_workflow)
        self.assertIn("cargo test --all-targets --all-features", rust_workflow)
        self.assertFalse((destination / "version").exists())

    def test_rust_version_source_is_used_when_no_higher_priority_runtime_exists(
        self,
    ) -> None:
        result, destination = self.copy_template(
            "use_rust=true",
            "use_gh_actions_release=true",
            "use_gh_actions_pr_tag_check=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        release_workflow = (destination / ".github/workflows/release.yml").read_text()
        tag_check_workflow = (destination / ".github/workflows/pr-tag-check.yml").read_text()

        self.assertIn("Cargo.toml", release_workflow)
        self.assertIn("Cargo.toml", tag_check_workflow)
        self.assertNotIn("cat version", release_workflow)
        self.assertNotIn("cat version", tag_check_workflow)

    def test_rust_version_source_is_used_for_docker_release(self) -> None:
        result, destination = self.copy_template(
            "use_rust=true",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        docker_release_workflow = (
            destination / ".github/workflows/docker-release.yml"
        ).read_text()

        self.assertIn("Cargo.toml", docker_release_workflow)
        self.assertNotIn("cat version", docker_release_workflow)

    def test_rust_toolchain_older_than_edition_2024_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_rust=true",
            "rust_version=1.84.1",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Rust ツールチェーン", result.stdout)

    def test_tauri_template_generates_desktop_project(self) -> None:
        result, destination = self.copy_template(
            "use_tauri=true",
            "rust_version=1.88.0",
            "node_version=24",
            "tauri_product_name=Desk App",
            "tauri_identifier=com.example.desk",
            "tauri_version=1.2.3",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        package = json.loads((destination / "package.json").read_text())
        tauri_config = json.loads((destination / "src-tauri/tauri.conf.json").read_text())
        cargo_toml = (destination / "src-tauri/Cargo.toml").read_text()
        rust_toolchain = (destination / "rust-toolchain.toml").read_text()
        workflow = (destination / ".github/workflows/tauri-quality-checks.yml").read_text()

        self.assertEqual(package["version"], "1.2.3")
        self.assertEqual(package["dependencies"]["@tauri-apps/api"], "^2.11.1")
        self.assertEqual(tauri_config["productName"], "Desk App")
        self.assertEqual(tauri_config["identifier"], "com.example.desk")
        self.assertEqual(tauri_config["version"], "1.2.3")
        self.assertIn("icons/icon.png", tauri_config["bundle"]["icon"])
        self.assertIn('version = "1.2.3"', cargo_toml)
        self.assertIn('channel = "1.88.0"', rust_toolchain)
        self.assertIn("npm run cargo:clippy", workflow)
        self.assertIn("libwebkit2gtk-4.1-dev", workflow)
        self.assertIn("libxdo-dev", workflow)
        self.assertTrue((destination / "src-tauri/icons/icon.png").exists())
        self.assertFalse((destination / "Cargo.toml").exists())
        self.assertFalse((destination / "src/main.rs").exists())
        self.assertFalse((destination / "version").exists())

    def test_tauri_values_are_preserved_in_json_outputs(self) -> None:
        product_name = "Desk & App's Name"

        result, destination = self.copy_template(
            "use_tauri=true",
            f"tauri_product_name={product_name}",
            "tauri_identifier=com.example.escaped",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        package = json.loads((destination / "package.json").read_text())
        tauri_config = json.loads((destination / "src-tauri/tauri.conf.json").read_text())

        self.assertEqual(package["description"], product_name)
        self.assertEqual(tauri_config["productName"], product_name)

    def test_tauri_html_product_name_is_escaped(self) -> None:
        product_name = "ACME & Beta"

        result, destination = self.copy_template(
            "use_tauri=true",
            f"tauri_product_name={product_name}",
            "tauri_identifier=com.example.escaped",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        index_html = (destination / "index.html").read_text()

        self.assertIn("ACME &amp; Beta", index_html)
        self.assertNotIn(product_name, index_html)

    def test_invalid_tauri_product_name_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "tauri_product_name=Bad/Name",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri アプリ名", result.stdout)

    def test_tauri_identifier_allows_hyphenated_segments(self) -> None:
        result, destination = self.copy_template(
            "use_tauri=true",
            "tauri_identifier=com.example.my-app",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        tauri_config = json.loads((destination / "src-tauri/tauri.conf.json").read_text())

        self.assertEqual(tauri_config["identifier"], "com.example.my-app")

    def test_invalid_tauri_identifier_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "tauri_identifier=invalid",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri アプリ識別子", result.stdout)

    def test_tauri_identifier_with_underscore_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "tauri_identifier=com.example.my_app",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri アプリ識別子", result.stdout)

    def test_invalid_tauri_version_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "tauri_version=1.0",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri アプリバージョン", result.stdout)

    def test_tauri_version_with_empty_prerelease_segment_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "tauri_version=1.2.3-..",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri アプリバージョン", result.stdout)

    def test_tauri_cannot_be_combined_with_conflicting_runtime_support(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "use_chrome_extension=true",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri と Chrome Extension", result.stdout)

    def test_tauri_cannot_be_combined_with_root_rust_runtime_support(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "use_rust=true",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri は専用の src-tauri", result.stdout)

    def test_tauri_version_source_wins_when_python_is_enabled(self) -> None:
        result, destination = self.copy_template(
            "use_python=true",
            "use_tauri=true",
            "use_gh_actions_release=true",
            "use_gh_actions_pr_tag_check=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        release_workflow = (destination / ".github/workflows/release.yml").read_text()
        tag_check_workflow = (destination / ".github/workflows/pr-tag-check.yml").read_text()

        self.assertIn("package.json", release_workflow)
        self.assertIn("package.json", tag_check_workflow)
        self.assertNotIn("pyproject.toml", release_workflow)
        self.assertNotIn("pyproject.toml", tag_check_workflow)

    def test_tauri_version_source_is_used_for_docker_release(self) -> None:
        result, destination = self.copy_template(
            "use_tauri=true",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        docker_release_workflow = (
            destination / ".github/workflows/docker-release.yml"
        ).read_text()

        self.assertIn("package.json", docker_release_workflow)
        self.assertNotIn("Cargo.toml", docker_release_workflow)
        self.assertNotIn("cat version", docker_release_workflow)

    def test_tauri_quality_workflow_fails_when_any_check_fails(self) -> None:
        result, destination = self.copy_template("use_tauri=true")

        self.assertEqual(result.returncode, 0, result.stdout)

        workflow = (destination / ".github/workflows/tauri-quality-checks.yml").read_text()

        self.assertIn('checkConclusion = "failure";', workflow)
        self.assertIn("Fail on Tauri quality check failure", workflow)

    def test_tauri_eslint_config_allows_node_globals_in_config_files(self) -> None:
        result, destination = self.copy_template("use_tauri=true")

        self.assertEqual(result.returncode, 0, result.stdout)

        eslint_config = (destination / "eslint.config.mjs").read_text()

        self.assertIn('files: ["vite.config.ts", "vitest.config.ts"]', eslint_config)
        self.assertIn("...globals.node", eslint_config)

    def test_chrome_quality_workflow_fails_when_any_check_fails(self) -> None:
        result, destination = self.copy_template("use_chrome_extension=true")

        self.assertEqual(result.returncode, 0, result.stdout)

        workflow = (
            destination / ".github/workflows/chrome-extension-quality-checks.yml"
        ).read_text()

        self.assertIn('checkConclusion = "failure";', workflow)
        self.assertIn("Fail on Chrome extension quality check failure", workflow)

    def test_chrome_distribution_release_workflow_is_generated_for_scaffold(
        self,
    ) -> None:
        result, destination = self.copy_template(
            "use_chrome_extension=true",
            "use_gh_actions_chrome_extension_release=true",
            "chrome_extension_release_package_root_directory=dist",
            "chrome_extension_release_zip_name=browser-build-${version}.zip",
            "chrome_extension_release_title=Browser Build ${version}",
            "chrome_extension_release_notes=Install browser-build-${version}.zip",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        workflow = (
            destination / ".github/workflows/chrome-extension-release.yml"
        ).read_text()

        self.assertIn("name: Chrome Extension Distribution Release", workflow)
        self.assertIn("types:", workflow)
        self.assertIn("- closed", workflow)
        self.assertIn("branches:", workflow)
        self.assertIn("- main", workflow)
        self.assertNotIn("workflow_dispatch", workflow)
        self.assertIn("github.event.pull_request.merged == true", workflow)
        self.assertIn("github.event.pull_request.merge_commit_sha", workflow)
        self.assertIn("git rev-parse HEAD", workflow)
        self.assertIn("Expected a merge commit with at least two parents.", workflow)
        self.assertIn("JSON.parse(readFileSync(\"package.json\", \"utf8\"))", workflow)
        self.assertIn(
            "JSON.parse(readFileSync(\"src/manifest.json\", \"utf8\"))",
            workflow,
        )
        self.assertIn("packageVersion !== manifestVersion", workflow)
        self.assertIn("isChromeManifestVersion", workflow)
        self.assertIn("git fetch --tags --force", workflow)
        self.assertIn("already points to", workflow)
        self.assertIn("npm ci", workflow)
        self.assertIn("npm run check", workflow)
        self.assertIn("npm run build", workflow)
        self.assertIn("PACKAGE_ROOT_DIRECTORY: \"dist\"", workflow)
        self.assertIn('ZIP_NAME_TEMPLATE: "browser-build-${version}.zip"', workflow)
        self.assertIn('RELEASE_TITLE_TEMPLATE: "Browser Build ${version}"', workflow)
        self.assertIn(
            'RELEASE_NOTES_TEMPLATE: "Install browser-build-${version}.zip"',
            workflow,
        )
        self.assertIn("zip -r \"$ASSET_PATH\" .", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("gh release edit", workflow)
        self.assertIn(
            "gh release upload \"$RELEASE_VERSION\" \"$ASSET_PATH\" --clobber",
            workflow,
        )

    def test_chrome_distribution_release_rejects_generic_release_combination(
        self,
    ) -> None:
        result, _destination = self.copy_template(
            "use_chrome_extension=true",
            "use_gh_actions_release=true",
            "use_gh_actions_chrome_extension_release=true",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Chrome Extension distribution release workflow", result.stdout)

    def test_chrome_distribution_release_is_not_generated_for_adoption(
        self,
    ) -> None:
        result, destination = self.copy_template(
            "use_chrome_extension=true",
            "chrome_extension_mode=adopt_existing",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(
            (destination / ".github/workflows/chrome-extension-release.yml").exists()
        )
