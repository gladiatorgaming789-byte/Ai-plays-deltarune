"""Compatibility import for the standalone DeltaMod CSX package builder.

The implementation lives under ``mods.tools`` so release tooling can run with
Python ``-S`` without importing the gameplay package or optional runtime
dependencies.
"""

from mods.tools.deltamod_csx_package_impl import *  # noqa: F401,F403
from mods.tools.deltamod_csx_package_impl import __all__
