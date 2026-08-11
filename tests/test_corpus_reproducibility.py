"""Prove the generated benchmark lanes match their recorded provenance."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
PRODUCT_ROOT = REPO_ROOT.parent / "products" / "anonimator"
CORPUSGEN_ROOT = PRODUCT_ROOT / "bench" / "scripts"
PRODUCT_CORPUS = PRODUCT_ROOT / "bench" / "corpus"

GENERATED_LANES = {
    "public": ("address", "identifiers", "negative", "robustness"),
    "holdout": ("address", "identifiers", "negative"),
}


def _require_product_corpus(scope: str) -> None:
    if not CORPUSGEN_ROOT.is_dir():
        pytest.skip("product corpus generator is not available")
    if scope == "holdout" and not (PRODUCT_CORPUS / scope).is_dir():
        pytest.skip("private holdout corpus is not available")


def _generator():
    """Import the product-owned generator only when it is available locally."""
    scripts_path = str(CORPUSGEN_ROOT)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    from corpusgen import DEFAULT_SEED, HOLDOUT_SEED
    from corpusgen.generate import generate

    return DEFAULT_SEED, HOLDOUT_SEED, generate


@pytest.mark.parametrize(
    ("scope", "lane"),
    [
        (scope, lane)
        for scope, lanes in GENERATED_LANES.items()
        for lane in lanes
    ],
)
def test_generated_lane_matches_committed_corpus(
    scope: str, lane: str, tmp_path: Path
) -> None:
    """Recorded seeds rebuild each generated lane byte-for-byte."""
    _require_product_corpus(scope)
    public_seed, holdout_seed, generate = _generator()
    seed = holdout_seed if scope == "holdout" else public_seed
    expected_dir = PRODUCT_CORPUS / scope / lane
    if not expected_dir.is_dir():
        pytest.skip(f"{scope}/{lane} is not an available corpus lane")

    actual_dir = tmp_path / scope / lane
    generate(lane, scope, seed, actual_dir)

    expected_files = {
        path.relative_to(expected_dir): path.read_bytes()
        for path in expected_dir.rglob("*.json")
    }
    actual_files = {
        path.relative_to(actual_dir): path.read_bytes()
        for path in actual_dir.rglob("*.json")
    }
    assert actual_files == expected_files
