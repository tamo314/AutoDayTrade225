"""Reconcile explicitly selected research records without loading market data.

The reviewed JSON is the authority for identity, selection and interpretation.
Directory order, largest suffix and a completion marker never select a result.
Only named documentation and named summary JSON files may be opened. Outputs
are a new immutable audit directory; historical results are never rewritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

SUMMARY_NAMES = {
    "COMPLETED.json", "decision.json", "development_results.json",
    "family_decision.json", "family_decisions.json",
}
INITIAL_CAMPAIGNS = {"20260913T090313-b797c081", "20260913T091013-506b627c"}
FORBIDDEN = re.compile(r"holdout|oos|2025h2|2026-price", re.I)


def read_evidence(root: Path, relative: str) -> bytes:
    """Fail closed on paths outside the reviewed document/summary boundary."""
    path = (root / relative).resolve()
    path.relative_to(root.resolve())
    parts = Path(relative).parts
    document = parts[:2] == ("docs", "strategy") and path.suffix == ".md"
    summary = (
        parts[:2] == ("results", "research")
        and len(parts) == 4
        and path.name in SUMMARY_NAMES
        and (re.match(r"(?:task-)?r\d{3}-", parts[2]) is not None or parts[2] in INITIAL_CAMPAIGNS)
        and not re.match(r"(?:task-)?r065-", parts[2])
        and not FORBIDDEN.search(relative)
    )
    if not (document or summary):
        raise ValueError(f"Evidence path is outside the audit scope: {relative}")
    return path.read_bytes()


def identity(run: str) -> str | None:
    """Preserve the two historically colliding R066 identities."""
    match = re.match(r"(?:task-)?(r\d{3})(?:-([qd]\d{3}))?", run)
    if not match:
        return None
    study = match[1].upper()
    spec = match[2].upper() if match[2] else "BASE"
    if study == "R066":
        study += "-CASH-LONG" if run.startswith("task-") else "-LOWER-TAIL"
    return f"{study}/{spec}"


def build(root: Path, review: Path, out: Path) -> dict[str, Any]:
    """Build the audit and validate all selected links before writing outputs."""
    reviewed = json.loads(review.read_text(encoding="utf-8"))
    records = reviewed["records"]
    keys = [r["key"] for r in records]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate study/spec identity")
    family_ids = {f["family_id"] for f in reviewed["families"]}
    runs = sorted(p for p in (root / "results/research").iterdir() if p.is_dir())
    run_names = {p.name for p in runs}
    inventory: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    selected: dict[str, str] = {}
    for record in records:
        if record["family_id"] not in family_ids:
            raise ValueError(f"Unknown family: {record['key']}")
        for run in record["selected_runs"]:
            if run not in run_names:
                raise ValueError(f"Missing selected run: {run}")
            if run in selected:
                raise ValueError(f"Run selected twice: {run}")
            selected[run] = record["key"]
            mapped = identity(run)
            if mapped and mapped != record["key"]:
                raise ValueError(f"Wrong study/spec association: {run} -> {record['key']}")
        if record["pnl_evidence"] == "NOT_OBTAINED" and record["economics_status"] != "NOT_EVALUATED":
            raise ValueError(f"PnL-free record given economic decision: {record['key']}")
        if record["legacy_decision"] == "INCONCLUSIVE" and record["economics_status"] == "FAIL":
            raise ValueError(f"Legacy INCONCLUSIVE promoted to economic failure: {record['key']}")
        if record["key"].startswith("R065") and record["selected_runs"]:
            raise ValueError("R065 remains an unselected restricted record")
        for source in record["evidence"]:
            relative = source["path"]
            if relative not in sources:
                payload = read_evidence(root, relative)
                sources[relative] = {
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                }
            if source.get("anchor"):
                text = read_evidence(root, relative).decode("utf-8-sig")
                pos = text.find(source["anchor"])
                if pos < 0:
                    raise ValueError(f"Missing source anchor: {relative}: {source['anchor']}")
                source["line"] = text.count("\n", 0, pos) + 1
        for claim in record.get("decision_claims", []):
            payload = json.loads(read_evidence(root, claim["path"]))
            for part in claim["pointer"].strip("/").split("/"):
                payload = payload[part]
            if payload != claim["value"]:
                raise ValueError(f"Decision evidence changed: {record['key']}: {claim}")
    for family in reviewed["families"]:
        actual = {r["key"] for r in records if r["family_id"] == family["family_id"]}
        if actual != set(family["members"]):
            raise ValueError(f"Family membership differs: {family['family_id']}")
        if family["remaining_economic_specs"] != 0 or family["automatic_reopen"]:
            raise ValueError("This closure review authorizes no additional economic search")
    for relation in reviewed["relations"]:
        for endpoint in ("from", "to"):
            if endpoint in relation and relation[endpoint] not in keys:
                raise ValueError(f"Unknown relation endpoint: {relation[endpoint]}")
        for endpoint in ("from_run", "to_run"):
            if endpoint in relation and relation[endpoint] not in run_names:
                raise ValueError(f"Unknown run endpoint: {relation[endpoint]}")
    for path in runs:
        claims = [
            claim for record in records for claim in record.get("decision_claims", [])
            if claim["path"].startswith(f"results/research/{path.name}/")
        ]
        inventory.append({
            "run_id": path.name,
            "key_from_name": identity(path.name),
            "directory": path.relative_to(root).as_posix(),
            "selected_for_record": selected.get(path.name),
            "selection_status": "REVIEWED_REFERENCE" if path.name in selected else "NOT_SELECTED",
            "run_status": "NOT_INFERRED_FROM_FILENAME",
            "reported_markers": claims,
            "root_file_names_only": sorted(p.name for p in path.iterdir() if p.is_file()),
            "note": "未選択は無効・未実行を意味しない。途中版・監査・未解決を含む。",
        })
    represented = {r["key"].split("/")[0].split("-")[0] for r in records}
    expected = {f"R{i:03}" for i in range(1, 104)}
    if represented != expected:
        raise ValueError(f"Study coverage differs: {represented ^ expected}")
    discovered = {identity(p.name) for p in runs} - {None}
    if not discovered.issubset(set(keys)):
        raise ValueError(f"Unmapped run identities: {discovered - set(keys)}")
    validation = {
        "status": "PASS",
        "scope": "identity, explicit references, source hashes, coverage and classification invariants only",
        "study_numbers": len(represented),
        "study_spec_records": len(records),
        "run_directories": len(runs),
        "selected_run_references": len(selected),
        "reason_counts": dict(Counter(r["reason_class"] for r in records)),
        "market_data_reads": 0,
        "trade_ledger_reads": 0,
        "r065_payload_reads": 0,
        "oos_holdout_reads": 0,
        "new_backtests": 0,
        "historical_pnl_recalculation": False,
    }
    out.mkdir(parents=True, exist_ok=False)

    def dump(name: str, value: object) -> None:
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    dump("study_registry.json", records)
    dump("run_registry.json", inventory)
    dump("family_registry.json", reviewed["families"])
    dump("supersession_map.json", reviewed["relations"])
    dump("missing_artifacts.json", reviewed["unresolved"])
    dump("prior_information_seen.json", {
        "development": "REPEATEDLY_USED_2021-01-01_TO_2025-06-30",
        "record_evidence": "All selected documents and saved summaries are known information; see source_inventory.json",
        "independent_hypothesis_count": None,
        "selection_multiple_testing_correction": "NOT_CLAIMED",
        "new_economic_specs_authorized": 0,
    })
    dump("source_inventory.json", sources)
    dump("validation.json", validation)
    dump("audit_manifest.json", {
        "audit_id": reviewed["audit_id"],
        "review_sha256": hashlib.sha256(review.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "input_policy": "explicit document/summary allowlist; directory metadata only otherwise",
        "validation": validation,
    })
    lines = ["# 戦略・仕様別対応表", "", "自動生成。判定は旧記録、分類は今回の整理。主成績の再評価ではない。", "",
             "未選択runを未実行・無効とは扱わない。複数参照は有効版の優先関係が未確定の場合を含む。", "",
             "|研究/仕様|family|旧判定|PnL証拠|整理区分|理由・制約|参照run・根拠|", "|---|---|---|---|---|---|---|"]
    for r in records:
        links = " / ".join(
            f"[{Path(e['path']).name}]({(root / e['path']).as_posix()}"
            + (f":{e['line']}" if "line" in e else "") + ")" for e in r["evidence"]
        )
        run_links = " / ".join(
            f"[{run}]({(root / 'results/research' / run).as_posix()})" for run in r["selected_runs"]
        )
        links = run_links + (" / " if run_links else "") + links
        cells = [r["key"], r["family_id"], r["legacy_decision"], r["pnl_evidence"], r["reason_class"], r["reason"], links]
        lines.append("|" + "|".join(str(x).replace("|", " / ").replace("\n", " ") for x in cells) + "|")
    (out / "study_index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return validation


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.root.resolve(), args.review, args.out), ensure_ascii=False, indent=2))
