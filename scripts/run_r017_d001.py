"""Run the saved-artifact-only R017-D001 sensitivity calibration."""

from pathlib import Path

from n225m_bt.research.r017 import run_r017

if __name__ == "__main__":
    print(run_r017(Path(__file__).resolve().parents[1]))
