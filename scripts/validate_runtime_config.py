"""Validate Long-term Beta runtime configuration without printing values."""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lab_agent.utils import (  # noqa: E402
    RuntimeConfigurationError,
    load_project_dotenv,
)
from lab_agent.utils.runtime_validation import (  # noqa: E402
    REQUIRED_RUNTIME_FIELDS,
    validate_long_term_beta_runtime,
)


def main() -> int:
    try:
        source = load_project_dotenv()
    except RuntimeConfigurationError as exc:
        print(f"Runtime configuration error: {exc}", file=sys.stderr)
        return 1

    print(f"Runtime config source: {source}")
    statuses = validate_long_term_beta_runtime()
    for name in REQUIRED_RUNTIME_FIELDS:
        print(f"{name:<40} {statuses[name]}")
    if "CONFIG" in statuses:
        print(f"{'CONFIG':<40} {statuses['CONFIG']}")

    return 0 if all(status == "SET" for status in statuses.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
