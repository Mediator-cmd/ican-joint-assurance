"""Build the self-contained Windows offline demonstration ZIP."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_TEMPLATE_ROOT = PROJECT_ROOT / "scripts" / "offline_package"
PACKAGE_NAME = "联保智调-离线演示-Windows-x64"
EXECUTABLE_NAME = "joint_assurance_offline"
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Release tag or build label.")
    parser.add_argument("--output", type=Path, required=True, help="Fresh staging directory.")
    parser.add_argument("--archive", type=Path, required=True, help="Destination ZIP file.")
    return parser.parse_args()


def require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Required file is missing: {path}")


def require_directory(path: Path) -> None:
    if not path.is_dir():
        raise NotADirectoryError(f"Required directory is missing: {path}")


def package_zip(package_root: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source_path in sorted(package_root.rglob("*")):
            if source_path.is_file():
                archive.write(source_path, source_path.relative_to(package_root.parent))


def main() -> None:
    args = parse_args()
    if not VERSION_PATTERN.fullmatch(args.version):
        raise ValueError("--version may contain only letters, digits, dots, underscores, and hyphens.")

    frontend_dist = PROJECT_ROOT / "frontend" / "dist"
    launcher = PROJECT_ROOT / "scripts" / "offline_launcher.py"
    require_directory(frontend_dist)
    require_directory(PROJECT_ROOT / "data")
    require_directory(PACKAGE_TEMPLATE_ROOT)
    require_file(launcher)
    if args.output.exists():
        raise FileExistsError(f"--output must be a new directory: {args.output}")
    if args.archive.exists():
        raise FileExistsError(f"--archive already exists: {args.archive}")

    package_root = args.output / PACKAGE_NAME
    app_parent = package_root / "app"
    pyinstaller_work = args.output / ".pyinstaller-work"
    package_root.mkdir(parents=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        EXECUTABLE_NAME,
        "--paths",
        str(PROJECT_ROOT),
        "--add-data",
        f"{PROJECT_ROOT / 'data'};data",
        "--add-data",
        f"{frontend_dist};frontend/dist",
        "--collect-all",
        "ortools",
        "--distpath",
        str(app_parent),
        "--workpath",
        str(pyinstaller_work),
        "--specpath",
        str(args.output),
        str(launcher),
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)

    executable = app_parent / EXECUTABLE_NAME / f"{EXECUTABLE_NAME}.exe"
    require_file(executable)
    for template_file in PACKAGE_TEMPLATE_ROOT.iterdir():
        if template_file.is_file():
            shutil.copy2(template_file, package_root / template_file.name)
    (package_root / "版本.txt").write_text(
        f"联保智调离线演示包\n版本：{args.version}\n",
        encoding="utf-8",
    )
    package_zip(package_root, args.archive)
    print(f"Built offline package: {args.archive}")


if __name__ == "__main__":
    main()
