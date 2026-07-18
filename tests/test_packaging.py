"""Every module the program imports must actually ship.

This exists because it has already gone wrong twice: sysmind_strings and then
sysmind_platform were added, imported at runtime, and left out of the install
manifest - so the install succeeded and then died with ImportError on first
use. A green test suite did not catch it, because the tests import from the
source tree, not from the installed copy.
"""
import _bootstrap  # noqa: F401
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _shipped_modules():
    import install
    return {s[:-3] for s in install.SCRIPTS}


def _pyproject_modules():
    text = (ROOT / "pyproject.toml").read_text()
    block = re.search(r"py-modules\s*=\s*\[(.*?)\]", text, re.DOTALL).group(1)
    return set(re.findall(r'"([^"]+)"', block))


def _local_imports():
    """Every sysmind_* module imported by any shipped source file."""
    found = set()
    for path in ROOT.glob("sysmind*.py"):
        for m in re.findall(r"^\s*(?:import|from)\s+(sysmind_\w+)",
                            path.read_text(), re.M):
            found.add(m)
    return found


def test_installer_ships_every_imported_module():
    missing = _local_imports() - _shipped_modules()
    assert not missing, f"imported but not in install.SCRIPTS: {sorted(missing)}"


def test_pyproject_ships_every_imported_module():
    missing = _local_imports() - _pyproject_modules()
    assert not missing, f"imported but not in pyproject py-modules: {sorted(missing)}"


def test_scan_backends_ship():
    for name in ("sysmind_scan_linux", "sysmind_scan_windows"):
        assert name in _shipped_modules(), f"{name} missing from install.SCRIPTS"
        assert name in _pyproject_modules(), f"{name} missing from pyproject"


if __name__ == "__main__":
    test_installer_ships_every_imported_module()
    test_pyproject_ships_every_imported_module()
    test_scan_backends_ship()
    print("PASS: packaging")
