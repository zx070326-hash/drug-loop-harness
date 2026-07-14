"""Evaluate a fixed RDKit QED computational oracle from a JSON input manifest."""

from __future__ import annotations

import json
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_v2_rdkit_qed_oracle.py INPUT_MANIFEST")
    payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
    from rdkit import Chem
    from rdkit.Chem import Crippen, Descriptors, Lipinski, QED

    molecule = Chem.MolFromSmiles(str(payload["smiles"]))
    if molecule is None:
        raise SystemExit("invalid_smiles")
    qed = float(QED.qed(molecule))
    print(
        json.dumps(
            {
                "candidate_id": payload["candidate_id"],
                "oracle_id": payload["oracle_id"],
                "metrics": {
                    "qed": qed,
                    "molecular_weight": float(Descriptors.MolWt(molecule)),
                    "logp": float(Crippen.MolLogP(molecule)),
                    "hbd": int(Lipinski.NumHDonors(molecule)),
                    "hba": int(Lipinski.NumHAcceptors(molecule)),
                },
                "outcome": {
                    "endpoint_id": payload["endpoint_id"],
                    "direction": "continuous",
                    "value": qed,
                    "unit": "qed_score",
                },
                "provenance": {
                    "tool": "RDKit",
                    "tool_profile": "rdkit_qed_v1",
                },
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
