"""Run the saved-artifact-only R018-D001 economic upper-bound diagnostic."""

from pathlib import Path

from n225m_bt.research.r018 import run_r018

if __name__ == "__main__":
    print(run_r018(Path(__file__).resolve().parents[1]))
