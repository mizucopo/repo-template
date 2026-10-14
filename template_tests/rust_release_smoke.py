"""Export a generated Rust fixture, then build/extract/start it on each native CI runner."""

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def export_fixture(destination):
    root = Path(__file__).resolve().parents[1]
    subprocess.run([
        "copier", "copy", "--trust", "--defaults", "-d", "use_rust=true",
        "-d", "use_gh_actions_release=true", str(root), str(destination),
    ], check=True)
    (destination / "src/main.rs").write_text('fn main() { println!("Rust release smoke"); }\n')
    (destination / "src/bin").mkdir()
    (destination / "src/bin/helper-cli.rs").write_text('fn main() { println!("Helper CLI smoke"); }\n')
    (destination / "package-extra.py").write_text('''import os
from pathlib import Path
stage = Path(os.environ["RUST_RELEASE_STAGE_DIR"])
(stage / "LICENSES").mkdir()
(stage / "LICENSES/fixture.txt").write_text("Public test fixture license.\\n", encoding="utf-8")
''')
    shutil.copy2(__file__, destination / "rust_release_smoke.py")


def native_smoke(root, target):
    spec = importlib.util.spec_from_file_location("release", root / ".github/scripts/release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    subprocess.run(["cargo", "generate-lockfile"], cwd=root, check=True)
    subprocess.run(["rustup", "target", "add", target], cwd=root, check=True)
    policy = json.loads((root / ".github/release.json").read_text())
    policy["publication"]["rust"] = {
        "targets": [target], "smoke_args": ["--help"],
        "stage_command": [sys.executable, "package-extra.py"],
    }
    plan = module.publication(policy, "0.1.0")
    directory = root / "artifacts"
    sha = "a" * 40
    module.build_rust_distribution(module.Git(root), plan, sha, target, directory)
    (directory / target).rename(directory / ("rust-release-" + target))
    archives = module.rust_deliverables(module.Git(root), plan, sha, directory)
    assert len(archives) == 1, archives
    executable_suffix = ".exe" if "windows" in target else ""
    with tempfile.TemporaryDirectory() as extracted:
        names = module.rust_extract(archives[0], Path(extracted))
        assert set(names) == {"test-project" + executable_suffix,
                              "helper-cli" + executable_suffix, "LICENSES/fixture.txt"}, names
    print(f"Native build, checksum, extraction, two executable startup smokes and product staging hook passed: {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path)
    parser.add_argument("--target")
    args = parser.parse_args()
    if args.export:
        export_fixture(args.export)
    else:
        if not args.target:
            parser.error("--target is required for native smoke")
        native_smoke(Path(__file__).resolve().parent, args.target)


if __name__ == "__main__":
    main()
