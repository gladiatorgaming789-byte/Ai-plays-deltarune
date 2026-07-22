"""External Deltarune agent controller."""

from pathlib import Path

__version__ = "0.1.0"

from .safe_bootstrap import install_safe_bootstrap
from .telemetry_compat import install_telemetry_compatibility_guard

install_telemetry_compatibility_guard()
install_safe_bootstrap(Path(__file__).resolve().parent.parent)
