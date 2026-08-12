from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION_PATH = REPOSITORY_ROOT / "deltarune_agent" / "deltamod_csx_package.py"


def _load_implementation() -> ModuleType:
    """Load the stdlib-only package builder without importing deltarune_agent."""

    spec = importlib.util.spec_from_file_location(
        "_deltarune_agent_deltamod_csx_package_lightweight",
        IMPLEMENTATION_PATH,
    )
    if spec is None or spec.loader is None:
        raise ImportError(
            f"Could not load DeltaMod CSX package implementation: {IMPLEMENTATION_PATH}"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_implementation = _load_implementation()

SUPPORTED_CHAPTERS = _implementation.SUPPORTED_CHAPTERS
build_csx_package = _implementation.build_csx_package
sha256_csx_file = _implementation.sha256_csx_file
sha256_file = _implementation.sha256_file
validate_csx_package = _implementation.validate_csx_package

__all__ = [
    "SUPPORTED_CHAPTERS",
    "build_csx_package",
    "sha256_csx_file",
    "sha256_file",
    "validate_csx_package",
]
