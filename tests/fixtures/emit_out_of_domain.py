from __future__ import annotations

import json
import sys


def main() -> None:
    subject_id = sys.argv[1]
    oracle_name = sys.argv[2]
    print(
        json.dumps(
            {
                "id": f"obs_out_of_domain_{subject_id}",
                "subject_id": subject_id,
                "oracle_name": oracle_name,
                "metrics": {"passed": True},
                "provenance": {
                    "source": "local_command_fixture",
                    "domain": ["protein_ligand"],
                },
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
