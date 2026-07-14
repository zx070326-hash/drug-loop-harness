from __future__ import annotations

import json
import sys


def main() -> None:
    subject_id = sys.argv[1]
    print(
        json.dumps(
            {
                "id": f"obs_wrong_oracle_{subject_id}",
                "subject_id": subject_id,
                "oracle_name": "unexpected_oracle",
                "metrics": {"passed": True},
                "provenance": {
                    "source": "local_command_fixture",
                    "domain": ["small_molecule"],
                },
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
