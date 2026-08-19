"""Dependency-boundary tests for the autotune subpackage."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

AUTOTUNE_DIR = Path(__file__).parents[1] / "reactors_czlab" / "autotune"
MODULES = ("model", "relay", "runtime", "audit", "simulation")


@pytest.mark.parametrize("module", MODULES)
def test_autotune_module_imports_independently(module: str) -> None:
    """Every owning module imports successfully in a fresh interpreter."""
    subprocess.run(
        [sys.executable, "-c", f"import reactors_czlab.autotune.{module}"],
        check=True,
        capture_output=True,
        text=True,
    )


def test_autotune_dependency_graph_is_acyclic() -> None:
    """Internal imports retain the intended one-way package layering."""
    graph: dict[str, set[str]] = {}
    for module in MODULES:
        tree = ast.parse((AUTOTUNE_DIR / f"{module}.py").read_text(encoding="utf-8"))
        graph[module] = {
            node.module.removeprefix("reactors_czlab.autotune.")
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("reactors_czlab.autotune.")
        }

    assert graph == {
        "model": set(),
        "relay": {"model"},
        "runtime": {"model", "relay"},
        "audit": {"model", "relay", "runtime"},
        "simulation": {"model", "relay"},
    }


def test_autotune_package_initializer_is_docstring_only() -> None:
    """Importing the package itself must not eagerly load its dependencies."""
    tree = ast.parse((AUTOTUNE_DIR / "__init__.py").read_text(encoding="utf-8"))
    assert len(tree.body) == 1
    assert isinstance(tree.body[0], ast.Expr)
    assert isinstance(tree.body[0].value, ast.Constant)
    assert isinstance(tree.body[0].value.value, str)
