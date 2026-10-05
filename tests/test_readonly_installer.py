"""Exercise installer idempotency, drift protection, and automatic restoration."""

import os
import shutil
import subprocess
from pathlib import Path

from tools.generate_readonly_installer import ADDED, CHANGED, generate


def fixture(tmp: Path) -> tuple[Path, Path, dict[str, bytes]]:
    """Create a small synthetic installation containing preserved controls."""
    root = tmp / "config"
    baseline = root / "custom_components/renogy"
    baseline.mkdir(parents=True)
    source = tmp / "new"
    originals = {}
    for name in CHANGED + ("number.py", "select.py", "hub.py", "manifest.json"):
        p = baseline / name
        p.parent.mkdir(parents=True, exist_ok=True)
        originals[name] = f"original {name}\n".encode()
        p.write_bytes(originals[name])
    for name in CHANGED + ADDED:
        p = source / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"updated {name}\n")
    installer = tmp / "install.sh"
    generate(baseline, source, installer)
    return root, installer, originals


def run(root: Path, installer: Path, *args: str, extra_env=None):
    env = dict(os.environ, RENOGY_CONFIG_DIR=str(root))
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(installer), *args],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_install_preserves_controls_and_cells_and_can_roll_back(tmp_path):
    root, installer, originals = fixture(tmp_path)
    result = run(root, installer)
    assert result.returncode == 0, result.stdout + result.stderr
    target = root / "custom_components/renogy"
    for name in ("number.py", "select.py", "hub.py", "manifest.json"):
        assert (target / name).read_bytes() == originals[name]
    for name in CHANGED + ADDED:
        assert (target / name).read_text() == f"updated {name}\n"
    backups = list(root.glob("renogy-code-before-lcd-diagnostics-*.tar.gz"))
    assert len(backups) == 1
    again = run(root, installer)
    assert again.returncode == 0
    assert "already installed" in again.stdout
    assert list(root.glob("renogy-code-before-lcd-diagnostics-*.tar.gz")) == backups
    restore = run(root, installer, "--rollback", str(backups[0]))
    assert restore.returncode == 0, restore.stdout + restore.stderr
    for name, contents in originals.items():
        assert (target / name).read_bytes() == contents
    assert not any((target / name).exists() for name in ADDED)


def test_changed_baseline_stops_before_any_replacement(tmp_path):
    root, installer, originals = fixture(tmp_path)
    target = root / "custom_components/renogy"
    (target / "select.py").write_text("a newer user control\n")
    result = run(root, installer)
    assert result.returncode != 0
    assert "differs" in result.stderr
    for name in CHANGED:
        assert (target / name).read_bytes() == originals[name]
    assert not any((target / name).exists() for name in ADDED)
    assert not list(root.glob("renogy-code-before-lcd-diagnostics-*.tar.gz"))


def test_mid_install_failure_restores_original_files(tmp_path):
    root, installer, originals = fixture(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    wrapper = fake_bin / "mv"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"config_flow.py.renogy-new"* ]]; then exit 77; fi\n'
        f'exec "{shutil.which("mv")}" "$@"\n'
    )
    wrapper.chmod(0o755)
    result = run(
        root, installer, extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"}
    )
    assert result.returncode != 0
    assert "restoring" in result.stderr
    target = root / "custom_components/renogy"
    for name, contents in originals.items():
        assert (target / name).read_bytes() == contents
    assert not any((target / name).exists() for name in ADDED)
    assert not list(target.rglob("*.renogy-new-*"))
