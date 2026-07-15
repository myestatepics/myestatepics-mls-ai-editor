from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "legacy"


@pytest.fixture(scope="session")
def legacy_module():
    sys.path.insert(0, str(LEGACY))
    try:
        spec = importlib.util.spec_from_file_location(
            "legacy_v16", LEGACY / "myestatepics_mls_batch_v1_6_production.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(LEGACY))
