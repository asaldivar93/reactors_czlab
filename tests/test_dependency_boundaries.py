"""Regression tests for the generic-core and concrete-driver boundary."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).parents[1] / "reactors_czlab"
OPTIONAL_HARDWARE_MODULES = {
    "adafruit_as7341",
    "adafruit_tca9548a",
    "adafruit_tlc59711",
    "board",
    "busio",
    "librpiplc",
    "pymodbus",
}
HARDWARE_OWNER_MODULES = {
    "adafruit_tca9548a",
    "adafruit_tlc59711",
    "board",
    "busio",
    "librpiplc",
}


def _imports(path: Path) -> set[str]:
    """Return every absolute module named by an import in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def _imports_any(imported: set[str], modules: set[str]) -> bool:
    """Whether an import names a module or one of its children."""
    return any(
        name == module or name.startswith(f"{module}.")
        for name in imported
        for module in modules
    )


def test_generic_core_imports_need_no_drivers_or_optional_hardware() -> None:
    """Generic sensors and actuators stay usable in client-only installs."""
    for name in ("sensor.py", "actuator.py"):
        imported = _imports(PACKAGE / "core" / name)
        assert not _imports_any(imported, OPTIONAL_HARDWARE_MODULES)
        assert not _imports_any(imported, {"reactors_czlab.drivers"})


def test_generic_core_and_drivers_initializer_import_without_server_extra() -> None:
    """Blocked optional imports prove these lightweight modules do not need them."""
    code = """
import builtins

blocked = {
    "adafruit_as7341",
    "adafruit_tca9548a",
    "adafruit_tlc59711",
    "board",
    "busio",
    "librpiplc",
    "pymodbus",
}
real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name.split(".", maxsplit=1)[0] in blocked:
        raise AssertionError(f"optional dependency imported: {name}")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import reactors_czlab.core.actuator
import reactors_czlab.core.sensor
import reactors_czlab.drivers
import reactors_czlab.drivers.hamilton_model
"""
    subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        cwd=PACKAGE.parent,
    )


def test_drivers_initializer_is_docstring_only() -> None:
    """Importing the package never imports every concrete driver."""
    tree = ast.parse(
        (PACKAGE / "drivers" / "__init__.py").read_text(encoding="utf-8"),
    )
    assert len(tree.body) == 1
    assert isinstance(tree.body[0], ast.Expr)
    assert isinstance(tree.body[0].value, ast.Constant)
    assert isinstance(tree.body[0].value.value, str)


def test_hardware_only_imports_live_in_core_hardware() -> None:
    """Board, bus and PLC libraries have exactly one import boundary."""
    owner = PACKAGE / "core" / "hardware.py"
    for path in PACKAGE.rglob("*.py"):
        if path == owner:
            continue
        assert not _imports_any(_imports(path), HARDWARE_OWNER_MODULES), path


def test_hamilton_model_has_no_modbus_dependency() -> None:
    """Register interpretation stays usable without the server extra."""
    imported = _imports(PACKAGE / "drivers" / "hamilton_model.py")
    assert not _imports_any(imported, {"pymodbus"})
