from __future__ import annotations

import json
import sys


def main() -> None:
    subject_id = sys.argv[1]
    oracle_name = sys.argv[2]
    print(
        json.dumps(
            {
                "id": f"obs_cmd_{subject_id}",
                "subject_id": subject_id,
                "oracle_name": oracle_name,
                "metrics": {"passed": True},
                "cost_spent": 1.0,
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
