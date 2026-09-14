"""Record the fixed-tolerance R053 Q-identification gate without evaluating PnL."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast

import numpy as np

from n225m_bt.research.r053 import R053QNotIdentifiableError, fwl_delta
from n225m_bt.research.runner import write_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "research" / "r053-q001-20260915-tse-afternoon-open-gap-fade-08-q-identification-audit"
PINV_RCOND = 1e-12
RESIDUAL_SS_TOLERANCE = 1e-12


def audit_module() -> Any:
    path = ROOT / "scripts" / "audit_r053_q001_bootstrap_rank.py"
    specification = importlib.util.spec_from_file_location("r053_rank_audit", path)
    if specification is None or specification.loader is None:
        raise ImportError("unable to load R053 rank audit")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable R053 Q-identification audit output exists: {OUT}")
    module = audit_module()
    rows, _ = module.make_design_rows()
    full, names = module.design_matrix(rows)
    q = full[:, 1]
    nuisance = np.delete(full, 1, axis=1)
    try:
        _, residual_ss = fwl_delta(
            q,
            np.zeros_like(q),
            nuisance,
            pinv_rcond=PINV_RCOND,
            residual_ss_tolerance=RESIDUAL_SS_TOLERANCE,
        )
        status = "IDENTIFIABLE"
        error: str | None = None
    except R053QNotIdentifiableError as exc:
        residual_ss = None
        status = "BLOCKED"
        error = str(exc)
    OUT.mkdir(parents=True)
    write_json(
        OUT / "q_identification_audit.json",
        {
            "audit_scope": "Q-versus-nuisance design identification only. y is an all-zero placeholder; no price outcome, orders, fills, trades, PnL, or performance statistic was computed or emitted.",
            "status": status,
            "pinv_rcond": PINV_RCOND,
            "residual_ss_tolerance": RESIDUAL_SS_TOLERANCE,
            "Q_column": names[1],
            "nuisance_columns": [name for index, name in enumerate(names) if index != 1],
            "nuisance_rank": int(np.linalg.matrix_rank(nuisance)),
            "Q_residual_sum_of_squares": residual_ss,
            "error": error,
        },
    )
    if status == "BLOCKED":
        raise R053QNotIdentifiableError(cast(str, error))


if __name__ == "__main__":
    main()
