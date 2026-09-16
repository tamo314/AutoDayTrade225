"""Execute R057-Q001.  The implementation is intentionally isolated in its module."""

from pathlib import Path
from sys import path as sys_path

sys_path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from n225m_bt.research.r057_runner import main

if __name__ == "__main__":
    from n225m_bt.research.execution import require_entry
    require_entry("script:scripts/run_r057_q001_tse_opening_gap_acceptance.py")

    main()
