"""Execute TASK-R090-Q002 with its single preregistered PnL-free gate revision."""

from __future__ import annotations

from pathlib import Path

from run_r090_q001_lunch_rejection_fade import main

from n225m_bt.research.r090_q002 import feasibility_q002

if __name__ == "__main__":
    from n225m_bt.research.execution import require_entry
    require_entry("script:scripts/run_r090_q002_lunch_rejection_fade.py")

    main(
        run_id="r090-q002-20260915-lunch-rejection-fade-gate-revision-01",
        document=Path("docs/strategy/79_r090_q002_lunch_rejection_fade_gate_revision.md"),
        task_id="TASK-R090-Q002",
        study_id="R090-Q002",
        s2_gate_description="Q001 gate unchanged except the 2025H1 LR minimum is 6 (one average event per month across the partial half-year), rather than 8. Unexplained=0; LR/LA/PR>=90; LR up/down>=30; B1/B2 LR/LA>=25; LR 2022-2024>=15; per-band LR/LA M overlap plus >=10 each; otherwise INCONCLUSIVE before PnL.",
        feasibility_function=feasibility_q002,
        extra_source_files=(
            Path("scripts/run_r090_q002_lunch_rejection_fade.py"),
            Path("src/n225m_bt/research/r090_q002.py"),
        ),
        test_files=(Path("tests/test_r090_q001.py"), Path("tests/test_r090_q002.py")),
        mypy_files=(
            Path("src/n225m_bt/research/r090_lunch_rejection.py"),
            Path("src/n225m_bt/research/r090_q002.py"),
        ),
    )
