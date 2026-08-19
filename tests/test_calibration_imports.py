"""Import boundaries and public exports for the calibration package."""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

_HEAVY_MODULES = (
    "lmfit",
    "scipy",
    "reactors_czlab.core.calibration.fitting",
    "reactors_czlab.core.calibration.run",
    "reactors_czlab.core.calibration.storage",
)


@pytest.mark.parametrize(
    "statement",
    [
        "import reactors_czlab.core.data",
        "import reactors_czlab.core.calibration.models",
        "from reactors_czlab.core.calibration import Calibration",
    ],
)
def test_lightweight_imports_do_not_load_calibration_services(statement: str) -> None:
    """Shared and runtime models stay independent of fitting and persistence."""
    code = (
        f"{statement}\n"
        f"for name in {_HEAVY_MODULES!r}:\n"
        "    if any(module == name or module.startswith(name + '.') "
        "for module in sys.modules):\n"
        "        print(name)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", "import sys\n" + code],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == ""


def test_data_does_not_keep_moved_calibration_names() -> None:
    """The old shared-data import path is intentionally unsupported."""
    data = importlib.import_module("reactors_czlab.core.data")

    for name in (
        "Calibration",
        "CALIBRATION_MODELS",
        "MAX_DOSE_SECONDS",
        "MIN_DISPENSE_FLOW",
        "calibration_flow",
    ):
        assert not hasattr(data, name)


def test_every_public_export_resolves_from_its_owner_and_is_cached() -> None:
    """The lazy facade exposes stable object identities for every public name."""
    package = importlib.import_module("reactors_czlab.core.calibration")
    owners = {
        "Calibration": "models",
        "CalibrationFit": "models",
        "MAX_RELATIVE_UNCERTAINTY": "fitting",
        "MIN_POINTS": "fitting",
        "PLOT_SAMPLES": "fitting",
        "fit_models": "fitting",
        "MAX_RUN_SECONDS": "run",
        "MIN_RUN_SECONDS": "run",
        "CalibrationRun": "run",
        "CALIBRATION_ENV": "storage",
        "calibration_dir": "storage",
        "calibration_path": "storage",
        "load_calibration": "storage",
        "load_into": "storage",
        "replacement_reason": "storage",
        "save_calibration": "storage",
    }

    assert set(package.__all__) == set(owners)
    assert set(package.__all__) <= set(dir(package))
    for name, module_name in owners.items():
        owner = importlib.import_module(
            f"reactors_czlab.core.calibration.{module_name}",
        )
        first = getattr(package, name)
        second = getattr(package, name)

        assert first is getattr(owner, name)
        assert second is first
        assert package.__dict__[name] is first


def test_unknown_package_attribute_raises_attribute_error() -> None:
    """Lazy lookup keeps normal module error semantics."""
    package = importlib.import_module("reactors_czlab.core.calibration")
    name = "not_an_export"

    with pytest.raises(AttributeError, match="has no attribute"):
        getattr(package, name)
