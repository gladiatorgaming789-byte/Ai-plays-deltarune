from pathlib import Path
import sys

# Allow this maintainer helper to be run directly from any working directory
# without requiring an editable install of the repository package.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from deltarune_agent.deltamod_package import main


if __name__ == "__main__":
    raise SystemExit(main())
