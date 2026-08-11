"""Import-guard: pl-pii-bench is a clean-room harness and must never import
the proprietary product code. Walks every `.py` file in the repo (excluding
.venv/ and other virtualenv-ish directories) and asserts none of them
`import`/`from`-import `anonymize`, `products`, or `toolkit`.

The Anonimator adapter (adapters/anonimator.py) shells out to the installed
CLI via `subprocess` — a string, not an import — which is exactly why this
check passes for it and would fail if anyone "helpfully" swapped that for
a direct import.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories that may contain third-party or environment code, never ours.
EXCLUDED_DIR_NAMES = {".venv", "venv", "site-packages", ".git", "__pycache__", "build", "dist", ".mypy_cache", ".pytest_cache"}

FORBIDDEN_MODULES = {"anonymize", "products", "toolkit"}


def _iter_repo_python_files():
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        yield path


def _forbidden_imports_in_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                if top_level in FORBIDDEN_MODULES:
                    hits.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                top_level = node.module.split(".")[0]
                if top_level in FORBIDDEN_MODULES:
                    hits.append(f"from {node.module} import ...")
    return hits


def test_no_file_imports_product_code():
    violations: dict[str, list[str]] = {}
    files = list(_iter_repo_python_files())
    assert files, "expected to find .py files under the repo"

    for path in files:
        hits = _forbidden_imports_in_file(path)
        if hits:
            violations[str(path.relative_to(REPO_ROOT))] = hits

    assert not violations, (
        "clean-room violation: the following files import product code "
        f"(anonymize/products/toolkit): {violations}"
    )


def test_anonimator_adapter_uses_subprocess_not_import():
    adapter = REPO_ROOT / "adapters" / "anonimator.py"
    source = adapter.read_text(encoding="utf-8")
    assert "subprocess" in source
    assert "import anonymize" not in source
