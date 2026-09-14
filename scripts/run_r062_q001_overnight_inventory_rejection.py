"""Execute R062-Q001's preregistered Development-only technical eligibility gate."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from sys import executable
from typing import Any, cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r062 import r062_event
from n225m_bt.research.runner import snapshot_source, write_json

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r062-q001-20260915-overnight-inventory-rejection-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
AXIS_SOURCE = ROOT / "results" / "research" / "r012-q001-20260914-night-direction-followthrough-01" / "daily_net_pnl_aligned.json"
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED = 20261006


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def axis() -> list[str]:
    values = json.loads(AXIS_SOURCE.read_text(encoding="utf-8"))["trade_dates"]
    if not isinstance(values, list) or len(values) != 1111 or len(set(values)) != 1111:
        raise ValueError("R062 fixed 1,111-day axis unavailable")
    return cast(list[str], values)


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {key for key, rows in grouped.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    included = [row for key, rows in grouped.items() if key not in isolated for row in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {"parent_data_version": data.data_version, "quarantined_sessions": len(isolated), "quarantined_bars": len(data.bars) - len(included), "included_sessions": len(grouped) - len(isolated), "included_bars": len(included), "quarantined_session_list": listed, "quarantined_session_list_hash": canonical_hash(listed), "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in row.quality_flags for row in included)}
    expected = {"parent_data_version": PARENT_HASH, "quarantined_sessions": 45, "quarantined_bars": 27345, "included_sessions": 2216, "included_bars": 1326086, "quarantined_session_list_hash": QUARANTINE_HASH, "included_tick_grid_violations": 0}
    mismatch = {key: {"actual": audit[key], "expected": value} for key, value in expected.items() if audit[key] != value}
    if mismatch:
        raise ValueError(f"R062 R004 fixed isolation mismatch: {mismatch}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def input_manifest(root: Path) -> dict[str, object]:
    files = [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(root, "development")]
    return {"status": "frozen_before_price_statistics_events_or_pnl", "trade_date_range": ["2021-01-01", "2025-06-30"], "scope": "Selected normalized Development Parquet only; raw, OOS, and Final Holdout prohibited.", "files": files, "files_hash": canonical_hash(files)}


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable R062 output exists: {OUT}")
    OUT.mkdir(parents=True)
    _, sessions, data_config, _ = load_project_config(ROOT / "config")
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    inputs = input_manifest(data_config.gold_root)
    implementation = {str(path): digest(ROOT / path) for path in (Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r062.py"), Path("tests/test_r062_q001.py"))}
    plan = {"experiment_id": IDENTIFIER, "study_id": "R062-Q001", "status": "frozen_before_price_statistics_events_or_pnl", "seed": SEED, "scope": "Development 2021-01-01..2025-06-30 only; repeated-use exploratory Development analysis.", "technical_predecessor": "-01 is immutable and not used for a decision: it required full night but did not require the corresponding TSE 0..65 path for a rolling reference triplet. -02 corrects only that preregistered eligibility implementation before any orders, fills, PnL, regression or bootstrap.", "duplicate_review": "R001-R061 reviewed before price statistics. R055 uses a full OSE-night return and next TSE open but has no 15-minute rejection/confirmation filter and enters at the TSE open; R031/R012 use a 5-minute confirmation and different entry/holding. No registered combination of full-night return, 15-minute TSE rejection, next-bar entry, and 30-minute fixed hold exists.", "hypothesis": "After an extreme full OSE-night move, a 15-minute next-TSE rejection continues opposite the night direction for 30 minutes and exceeds medium-night rejection, extreme-night confirmation, and same-event follow/fixed-side controls.", "rule": "Versioned calendar uniquely maps prior TSE normal -> its following OSE night -> target TSE normal. Every scheduled night minute and target TSE ordinals 0..65 are required. rN=(cN-oN)/oN; x=abs(rN); s=sign(rN). Exact prior 120 eligible scheduled triplets (each full night plus next-TSE 0..65), no target/backfill, >=100 valid x, nearest-rank q50/q70/q75/q80, equality upper. For each reaction window h=s*(c[w-1]-oT); rejection h<=-1 tick, confirmation h>=+1 tick. A extreme(q75)+rejection; C q50..q75+rejection; D extreme+confirmation; M q50..q75+confirmation. Zero rN remains E but no direction cell.", "execution": "Signal at close of ordinal 14; entry ordinal 15 open; fixed ordinal 45 open exit. No night move/gap/opening observation PnL, Stop/Target/reentry/early exit. One contract, at most one position. 1/2/3 tick per side plus 30 JPY per side, delayed entry does not extend exit. Preregistered q70/q80, 2-tick rejection, 10/20 reactions, 15/45 holds only; all reaction variants rebuilt causally.", "inference": "A daily mean and specified conditional contrasts use common fixed-axis 20-day noncircular MBB, 10,000, linear percentile. delta uses fixed-design 20 trade_date block-wild score bootstrap, 10,000, seed 20261006, FWL pinv rcond=1e-12 and residual-Q SS tolerance=1e-12. No row/block/replicate discard/redraw.", "information_gate": "E>=800, A/C/D/M>=70, A long/short>=25, q80 A>=45, window10/window20 A>=50; otherwise INCONCLUSIVE. Q unidentified is BLOCKED.", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED", "inputs": inputs, "source": source, "implementation": implementation}
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "status": "preregistered", "started_at": datetime.now(timezone.utc).isoformat(), "seed": SEED, "plan_hash": canonical_hash(plan), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {"pytest": [executable, "-m", "pytest", "tests/test_r062_q001.py", "tests/test_r055_q001.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r062.py", "tests/test_r062_q001.py", str(Path(__file__).relative_to(ROOT))], "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r062.py"]}
    validation: dict[str, Any] = {name: {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr} for name, command in commands.items() for result in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]}
    validation["coverage"] = "triplet mapping, exact history/minimum/rank/equality, full night, TSE ordinals 0..65, reaction causality, zero/sign/cell exclusivity, next-bar entry/fixed exit/delay, costs/no-double-slippage, one position, prefix, fixed axis and OOS/Final lock"
    validation["status"] = "PASS" if all(value["returncode"] == 0 for value in validation.values() if isinstance(value, dict) and "returncode" in value) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R062 validation failed before price access")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    fixed_axis = axis()
    mapping = []
    for value in fixed_axis:
        target = date.fromisoformat(value)
        record = classifier.exchange_calendar.get(target)
        mapping.append({"target_tse_trade_date": value, "prior_tse_trade_date": record.previous_trade_date.isoformat() if record and record.previous_trade_date else None, "night_calendar_start_date": record.night_calendar_start_date.isoformat() if record and record.night_calendar_start_date else None})
    write_json(OUT / "tse_ose_tse_triplet_mapping.json", mapping)
    if any(row["prior_tse_trade_date"] is None or row["night_calendar_start_date"] is None for row in mapping):
        write_json(OUT / "BLOCKED.json", {"experiment_id": IDENTIFIER, "status": "BLOCKED", "stage": "schedule_mapping_before_price_access", "mapping": mapping, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        return
    development = load_split(data_config.gold_root, "development")
    view, qaudit, isolated = quarantine(development)
    groups = session_groups(view.bars)
    events: list[dict[str, object]] = []
    for value in fixed_axis:
        target = date.fromisoformat(value)
        history: list[tuple[date, list[Bar] | None, list[Bar] | None, bool]] = []
        cursor = target
        for _ in range(120):
            record = classifier.exchange_calendar.get(cursor)
            if record is None or record.previous_trade_date is None:
                history = []
                break
            cursor = record.previous_trade_date
            history.append((cursor, groups.get((cursor, Session.NIGHT)), groups.get((cursor, Session.DAY)), (cursor, Session.NIGHT) in isolated or (cursor, Session.DAY) in isolated))
        events.append(r062_event(classifier, target, groups.get((target, Session.NIGHT)), groups.get((target, Session.DAY)), history, night_quarantined=(target, Session.NIGHT) in isolated, day_quarantined=(target, Session.DAY) in isolated))
    write_json(OUT / "all_candidate_event_ledger.json", events)
    counts: dict[str, int] = {}
    for event in events:
        key = str(event.get("reason", event.get("status")))
        counts[key] = counts.get(key, 0) + 1
    e_count = sum(event.get("status") == "E" for event in events)
    technical = {"status": "BLOCKED", "stage": "observational_sample", "reason": "Q_NOT_IDENTIFIABLE", "detail": "No direction-eligible common-E observations exist after the exact full scheduled-night, 120-triplet/minimum-100 and TSE-ordinal completeness rules; fixed-design FWL and all bootstrap/PnL stages are prohibited.", "E": e_count, "event_status_or_reason_counts": counts, "fixed_axis_trade_dates": len(fixed_axis), "repetitions_not_run": 10000, "no_discard_redraw_or_respecification": True}
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": qaudit, "fixed_axis": fixed_axis, "physical_io": "Development normalized Parquet only"})
    write_json(OUT / "technical_fwl_gate.json", technical)
    write_json(OUT / "BLOCKED.json", {"experiment_id": IDENTIFIER, "status": "BLOCKED", "stage": "FWL_Q_identification", "failure": technical, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
