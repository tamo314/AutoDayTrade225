"""Run the saved-Development-ledger-only R016-D001 diagnostic."""

from pathlib import Path

from n225m_bt.research.r016 import run_r016

if __name__ == "__main__":
    print(run_r016(Path(__file__).resolve().parents[1]))
