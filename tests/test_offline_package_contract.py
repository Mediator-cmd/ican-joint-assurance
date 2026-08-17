from pathlib import Path

from backend.app.runtime_paths import project_root
from scripts import offline_launcher


PROJECT_ROOT = Path(__file__).parents[1]


def test_offline_environment_is_local_and_secret_free(monkeypatch, tmp_path) -> None:
    for name in offline_launcher.AI_ENVIRONMENT_NAMES:
        monkeypatch.setenv(name, "must-be-removed")

    content = tmp_path / "content"
    writable = tmp_path / "writable"
    offline_launcher.configure_offline_environment(content, writable, 8012)

    for name in offline_launcher.AI_ENVIRONMENT_NAMES:
        assert name not in offline_launcher.os.environ
    assert offline_launcher.os.environ["APP_CONTENT_ROOT"] == str(content)
    assert offline_launcher.os.environ["APP_FRONTEND_DIST_PATH"] == str(
        content / "frontend" / "dist"
    )
    assert offline_launcher.os.environ["APP_RUNTIME_DATABASE_PATH"] == str(
        writable / ".runtime" / "runtime-sessions.sqlite3"
    )
    assert offline_launcher.os.environ["PORT"] == "8012"


def test_packaged_content_root_can_be_overridden(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_CONTENT_ROOT", str(tmp_path))

    assert project_root() == tmp_path.resolve()


def test_windows_launch_templates_use_crlf_and_ascii_commands() -> None:
    template_root = PROJECT_ROOT / "scripts" / "offline_package"
    for command_file in template_root.glob("*.cmd"):
        payload = command_file.read_bytes()
        assert b"\r\n" in payload
        assert payload.count(b"\n") == payload.count(b"\r\n")
    start_command = (template_root / "启动联保智调离线版.cmd").read_text(
        encoding="ascii"
    )
    assert 'start "Joint Assurance Offline"' in start_command


def test_release_workflow_publishes_stable_download_names() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "offline-release.yml").read_text(
        encoding="utf-8"
    )

    assert 'tags: ["offline-v*"]' in workflow
    assert "contents: write" in workflow
    assert "LianBaoZhiDiao-Offline-Windows-x64.zip" in workflow
    assert "SHA256SUMS.txt" in workflow
    assert "Set-Content -LiteralPath $checksumPath -Encoding utf8" in workflow
    assert "gh release create" in workflow

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert (
        "releases/latest/download/LianBaoZhiDiao-Offline-Windows-x64.zip"
        in readme
    )
