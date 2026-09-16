"""Execute the preregistered Development-only R059-Q001 experiment."""
# ruff: noqa: E701, E702, E731
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import ceil, floor
from pathlib import Path
from random import Random
from statistics import fmean
from subprocess import run
from sys import executable
from typing import Any, cast

import numpy as np

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r059 import R059QNotIdentifiableError, all_events, fwl_delta
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r059-q001-20260915-session-reopen-gap-reversal-03"
OUT = ROOT / "results" / "research" / IDENTIFIER
AXIS = ROOT / "results" / "research" / "r012-q001-20260914-night-direction-followthrough-01" / "daily_net_pnl_aligned.json"
SEED = 20261005
BASE = ("A", "B", "C", "A_continue", "A_buy", "A_sell")
VARIANTS = {"A2": (2, 0, 90, 30), "A3": (3, 0, 90, 30), "A_delay": (1, 1, 90, 30)}
SENSITIVITIES = {"A85": (1, 0, 85, 30), "A95": (1, 0, 95, 30), "A_h15": (1, 0, 90, 15), "A_h45": (1, 0, 90, 45)}


def digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def axis() -> list[str]:
    values = json.loads(AXIS.read_text(encoding="utf-8"))["trade_dates"]
    if not isinstance(values, list) or len(values) != 1111 or len(set(values)) != 1111:
        raise ValueError("R059 requires the fixed 1,111 trade_date axis")
    return cast(list[str], values)


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {key for key, rows in grouped.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    kept = [bar for key, rows in grouped.items() if key not in isolated for bar in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit = {"quarantined_sessions": len(isolated), "quarantined_bars": len(data.bars)-len(kept), "included_sessions": len(grouped)-len(isolated), "included_bars": len(kept), "quarantined_session_list_hash": canonical_hash(listed), "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in bar.quality_flags for bar in kept)}
    expected = (45, 27345, 2216, 1326086, "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa", 0)
    actual = (audit["quarantined_sessions"], audit["quarantined_bars"], audit["included_sessions"], audit["included_bars"], audit["quarantined_session_list_hash"], audit["included_tick_grid_violations"])
    if actual != expected:
        raise ValueError(f"R004 fixed isolation mismatch: {actual}")
    return ResearchData(kept, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def selected(row: dict[str, object], condition: str, threshold: int) -> bool:
    if row.get("status") != "E" or not row.get("direction_eligible"):
        return False
    x = float(cast(float, row["x"]))
    if condition == "B": return x >= float(cast(float, row["q75"]))
    if condition == "C": return float(cast(float, row["q75"])) <= x < float(cast(float, row["q90"]))
    return x >= float(cast(float, row[f"q{threshold}"]))


def side(row: dict[str, object], condition: str) -> str:
    if condition == "A_continue": return "long" if int(cast(int, row["gap_sign"])) > 0 else "short"
    if condition == "A_buy": return "long"
    if condition == "A_sell": return "short"
    return "short" if int(cast(int, row["gap_sign"])) > 0 else "long"


def run_condition(base: list[dict[str, object]], bars: dict[tuple[date, Session], list[Bar]], engine: BacktestEngine, condition: str, ticks: int, delay: int, threshold: int, holding: int) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    records: list[dict[str, object]] = []; trades: list[Trade] = []; audit: Counter[str] = Counter()
    for original in base:
        row = dict(original); allowed = selected(row, condition, threshold)
        row.update(condition=condition, condition_eligible=allowed, requested_slippage_ticks=ticks, requested_delay_minutes=delay, requested_holding_minutes=holding)
        if not allowed:
            row.update(status="skipped", condition_reason="ZERO_OR_THRESHOLD_NOT_MET"); records.append(row); continue
        target = date.fromisoformat(cast(str, row["trade_date"])); entry = datetime.fromisoformat(cast(str, row["planned_entry_jst"])); exit_time = datetime.fromisoformat(cast(str, row[f"planned_exit{holding}_jst"])); direction = side(row, condition)
        result = engine.run(bars.get((target, Session.NIGHT), []), R049FixedTimeStrategy(f"r059_{condition}", entry, exit_time, direction, delay), canonical_hash({"condition": condition, "ticks": ticks, "delay": delay, "threshold": threshold, "holding": holding}))
        audit["canceled_orders"] += result.canceled_orders
        if len(result.trades) > 1: raise ValueError("R059 maximum one position violated")
        if not result.trades:
            row.update(status="cancelled", condition_reason="ENGINE_NO_FILL_OR_EXIT"); audit["cancelled"] += 1; records.append(row); continue
        trade = result.trades[0]
        row.update(status="filled", side=direction, planned_exit_jst=exit_time.isoformat(), entry_ts_jst=trade.entry_ts.isoformat(), exit_ts_jst=trade.exit_ts.isoformat(), entry_signal_ts_jst=trade.entry_signal_ts.isoformat(), exit_signal_ts_jst=trade.exit_signal_ts.isoformat() if trade.exit_signal_ts else None, exit_reason=trade.exit_reason.value, gross_pnl_jpy=trade.gross_pnl_jpy, fees_jpy=trade.fees_jpy, slippage_cost_jpy=trade.slippage_cost_jpy, net_pnl_jpy=trade.net_pnl_jpy, entry_delay_minutes=round((trade.entry_ts-entry).total_seconds()/60), exit_delay_minutes=round((trade.exit_ts-exit_time).total_seconds()/60))
        records.append(row); trades.append(trade)
    return tuple(sorted(trades, key=lambda item: item.entry_ts)), records, dict(audit)


def regression(base: list[dict[str, object]], bars: dict[tuple[date, Session], list[Bar]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for event in base:
        if not selected(event, "B", 90): continue
        target = date.fromisoformat(cast(str, event["trade_date"])); ref = date.fromisoformat(cast(str, event["tse_reference_trade_date"])); sign = int(cast(int, event["gap_sign"])); entry = datetime.fromisoformat(cast(str, event["planned_entry_jst"])); exit_time = datetime.fromisoformat(cast(str, event["planned_exit30_jst"])); n = {bar.ts_jst: bar for bar in bars[target, Session.NIGHT]}; d = sorted(bars[ref, Session.DAY], key=lambda item: item.ts_jst)
        first, last = n[entry], n[exit_time]; day_open, day_close = d[0], next(item for item in d if item.ts_jst == datetime.fromisoformat(cast(str, event["tse_close_jst"])))
        rows.append({"trade_date": target.isoformat(), "Q": int(float(cast(float,event["x"])) >= float(cast(float,event["q90"]))), "y_gross_jpy": -sign*(last.open-first.open)*100, "gap_abs_bps": float(cast(float,event["x"]))*10000, "last30_adjusted_bps": cast(float,event["tse_last30_adjusted_return_bps"]), "last30_range_bps": cast(float,event["tse_last30_range_bps"]), "session_adjusted_bps": -sign*(day_close.close-day_open.open)/day_open.open*10000, "ose_first_return_bps": -sign*(n[entry-timedelta(minutes=1)].close-n[entry-timedelta(minutes=1)].open)/n[entry-timedelta(minutes=1)].open*10000, "gap_up": int(sign > 0)})
    return rows


def design(rows: list[dict[str, object]]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    years = sorted({date.fromisoformat(cast(str,row["trade_date"])).year for row in rows}); names = ["intercept","Q","gap_abs_bps","last30_adjusted_bps","last30_range_bps","session_adjusted_bps","ose_first_return_bps","gap_up",*[f"calendar_year_{year}" for year in years[1:]]]
    x = np.asarray([[1., float(row["Q"]), float(row["gap_abs_bps"]), float(row["last30_adjusted_bps"]), float(row["last30_range_bps"]), float(row["session_adjusted_bps"]), float(row["ose_first_return_bps"]), float(row["gap_up"]), *[float(date.fromisoformat(cast(str,row["trade_date"])).year == year) for year in years[1:]]] for row in rows]); return x, np.asarray([float(row["y_gross_jpy"]) for row in rows]), names


def draws(values: list[str]) -> list[list[int]]:
    rng = Random(SEED); out=[]
    for _ in range(10000):
        draw=[]
        while len(draw)<len(values):
            start=rng.randrange(len(values)-19); draw.extend(range(start,start+20))
        out.append(draw[:len(values)])
    return out


def percentile(values: list[float], q: float) -> float:
    values=sorted(values); p=(len(values)-1)*q; lo,hi=floor(p),ceil(p); return values[lo] if lo==hi else values[lo]+(values[hi]-values[lo])*(p-lo)


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r059_q001_session_reopen_gap_reversal.py')

    if OUT.exists(): raise FileExistsError(f"immutable R059 output exists: {OUT}")
    OUT.mkdir(parents=True); instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract)!=(1,30): raise ValueError("R059 requires one tick and JPY30 per side")
    files=[Path(__file__).relative_to(ROOT),Path("src/n225m_bt/research/r059.py"),Path("tests/test_r059_q001.py"),Path("src/n225m_bt/strategies/r049_fixed_time.py")]; inputs={"scope":"Development normalized Parquet only; OOS and Final Holdout forbidden.","trade_date_range":["2021-01-01","2025-06-30"],"files":[{"path":str(path.resolve().relative_to(ROOT)),"sha256":digest(path)} for path in partition_paths(data_config.gold_root,"development")]}
    plan={"experiment_id":IDENTIFIER,"study_id":"R059-Q001","status":"frozen_before_price_statistics_events_or_pnl","seed":SEED,"scope":"Development only; repeated Development exploration, not independent reproduction.","duplicate_review":"R001-R058 reviewed before price statistics. R053 is TSE lunch-reopen gap; R039/R058 select continuous final-TSE moves, not the TSE-close/OSE-open discontinuity. No same TSE close, OSE open, 120-pair rolling extremeness, next-bar entry, and 30m hold exists.","hypothesis":"Extreme TSE-close to uniquely mapped OSE-night-open gaps reverse for 30 scheduled minutes from the second OSE bar, exceeding moderate gaps and same-event controls.","rule":"g=(oN-p)/p; target excluded from exact 120 linked pairs, >=100 valid x, nearest-rank q75/q85/q90/q95, equality upper. A q90 fade; B q75 fade; C q75-q90 fade; controls share A event/path. oN is observed signal only; entry=oN+1 and exit=entry+15/30/45 open. No gap or first OSE bar PnL.","ols":"B direction-valid events: 0-tick/pre-fee 30m fade gross; Q, abs-gap bps, last30 adjusted return/range, full-TSE adjusted return, first-OSE adjusted return, gap-up, year FE; fixed nuisance-only FWL rcond/tolerance 1e-12. Observed or any bootstrap nonidentification BLOCKED without redraw/deletion.","correction":"…-02 is immutable and excluded from judgment because it added ln(x/q90), a nuisance not in the registered R059 regression. This run removes only that unrequested column before price statistics/events/PnL; execution, inputs, costs, event construction, seed, bootstrap, and gates are unchanged.","inputs":inputs,"implementation":{str(path):digest(ROOT/path) for path in files},"source":snapshot_source(OUT,ROOT/"config",ROOT/"config"/"local_calendar.yaml"),"oos":"NOT_EVALUATED","final_holdout":"NOT_ACCESSED"}
    write_json(OUT/"input_manifest.json",inputs); write_json(OUT/"preregistration.json",plan); write_json(OUT/"campaign_manifest.json",{"experiment_id":IDENTIFIER,"status":"preregistered","started_at":datetime.now(timezone.utc).isoformat(),"seed":SEED,"plan_hash":canonical_hash(plan)})
    commands={"pytest":[executable,"-m","pytest","tests/test_r059_q001.py","tests/test_exit_after_entry_cutoff.py","-q"],"ruff":[executable,"-m","ruff","check","src/n225m_bt/research/r059.py","tests/test_r059_q001.py",str(Path(__file__).relative_to(ROOT))],"mypy":[executable,"-m","mypy","src/n225m_bt/research/r059.py"]}; validation={name:{"returncode":value.returncode,"stdout":value.stdout,"stderr":value.stderr} for name,command in commands.items() for value in [run(command,cwd=ROOT,capture_output=True,text=True,check=False)]}; validation["status"]="PASS" if all(value["returncode"]==0 for value in validation.values()) else "BLOCKED"; write_json(OUT/"pre_execution_validation.json",validation)
    if validation["status"]!="PASS": raise ValueError("R059 validation failed before price access")
    classifier=CalendarClassifier(sessions,ExchangeCalendar.from_path(ROOT/"config"/"local_calendar.yaml")); dates=axis(); schedule=[{"target_night_trade_date":item,"tse_reference_trade_date":classifier.exchange_calendar.get(date.fromisoformat(item)).previous_trade_date.isoformat() if classifier.exchange_calendar.get(date.fromisoformat(item)) and classifier.exchange_calendar.get(date.fromisoformat(item)).previous_trade_date else None} for item in dates]; write_json(OUT/"tse_ose_schedule_mapping.json",schedule)
    if any(row["tse_reference_trade_date"] is None for row in schedule): write_json(OUT/"BLOCKED.json",{"status":"BLOCKED","stage":"schedule_mapping_before_price_access","mapping":schedule}); return
    development=load_split(data_config.gold_root,"development"); view,qaudit,isolated=quarantine(development); grouped=session_groups(view.bars); base=all_events(classifier,classifier.exchange_calendar,[date.fromisoformat(item) for item in dates],grouped,isolated); write_json(OUT/"all_candidate_event_ledger.json",base); write_json(OUT/"preflight.json",{"status":"PASS_LIMITED","development_input":development.quality,"quarantine":qaudit,"fixed_axis":dates,"physical_io":"Development normalized Parquet only"})
    reg=regression(base,grouped); x,y,names=design(reg); samples=draws(dates); index={cast(str,row["trade_date"]):i for i,row in enumerate(reg)}; technical={"status":"PASS","columns":names,"rows":len(reg)}
    for replicate,draw in enumerate([list(range(len(dates))),*samples]):
        weights=np.zeros(len(reg)); [weights.__setitem__(index[dates[item]],weights[index[dates[item]]]+1) for item in draw if dates[item] in index]; used=weights>0; root=np.sqrt(weights[used])
        try: _, _ss=fwl_delta((x[used]*root[:,None])[:,1],y[used]*root,np.delete(x[used]*root[:,None],1,axis=1))
        except R059QNotIdentifiableError as exc: technical={"status":"BLOCKED","stage":"observed" if replicate==0 else "bootstrap","replicate_number_one_based":replicate,"error":str(exc),"columns":names}; break
    write_json(OUT/"technical_fwl_gate.json",technical)
    if technical["status"]!="PASS": write_json(OUT/"BLOCKED.json",{"status":"BLOCKED","stage":"FWL_Q_identification","failure":technical}); return
    configs={name:(name,1,0,90,30) for name in BASE}|{name:("A",*value) for name,value in VARIANTS.items()}|{name:("A",*value) for name,value in SENSITIVITIES.items()}; records={}; trades={}; daily={}; results={}; target_bars=[bar for rows in grouped.values() for bar in rows]
    for name,(condition,ticks,delay,threshold,holding) in configs.items():
        config=baseline.model_copy(update={"execution":baseline.execution.model_copy(update={"slippage_ticks":ticks})}); filled,ledger,audit=run_condition(base,grouped,BacktestEngine(instrument.instrument.to_spec(),config,classifier),condition,ticks,delay,threshold,holding); series=dict.fromkeys(dates,0)
        for trade in filled: series[trade.trade_date.isoformat()]+=trade.net_pnl_jpy
        folder=OUT/name; folder.mkdir(); metrics=research_metrics(filled,target_bars); write_json(folder/"events.json",ledger); write_json(folder/"daily_net_pnl_aligned.json",series); write_json(folder/"research_metrics.json",metrics); records[name],trades[name],daily[name],results[name]=ledger,filled,series,{"trade_count":len(filled),"metrics":metrics,"audit":audit}
    a_days={cast(str,row["trade_date"]) for row in records["A"] if row.get("status")=="filled"}; checks={"B_equals_A_union_C":all(selected(row,"B",90)==(selected(row,"A",90) or selected(row,"C",90)) for row in base),"A_C_exclusive":all(not(selected(row,"A",90) and selected(row,"C",90)) for row in base),"controls_same_event_entry_exit":all(all(records[name][i].get(field)==records["A"][i].get(field) for field in ("planned_entry_jst","planned_exit_jst")) for name in ("A_continue","A_buy","A_sell") for i,item in enumerate(dates) if item in a_days),"continue_opposite_side":all(records["A"][i].get("side")!=records["A_continue"][i].get("side") for i,item in enumerate(dates) if item in a_days),"event_side_variants":all(records[name][i].get("side")==records["A"][i].get("side") for name in ("A2","A3","A_h15","A_h45","A_delay") for i in range(len(dates)) if records[name][i].get("status")=="filled"),"fixed_axis":all(len(item)==1111 for item in daily.values()),"one_trade_max":all(len({trade.trade_date for trade in item})==len(item) for item in trades.values()),"execution_accounting":all(row.get("exit_reason")==ExitReason.SIGNAL.value and row.get("exit_delay_minutes")==0 and row.get("entry_delay_minutes")==(1 if name=="A_delay" else 0) and int(cast(int,row["net_pnl_jpy"]))==int(cast(int,row["gross_pnl_jpy"]))-int(cast(int,row["fees_jpy"])) for name,rows in records.items() for row in rows if row.get("status")=="filled")}; write_json(OUT/"execution_accounting_audit.json",{"status":"PASS" if all(checks.values()) else "BLOCKED","checks":checks,"accounting":"Gross fill-to-fill includes slippage; Net=Gross-fees without double deduction."})
    if not all(checks.values()): raise ValueError("R059 execution/accounting gate failed")
    filled={name:{cast(str,row["trade_date"]):int(cast(int,row["net_pnl_jpy"])) for row in rows if row.get("status")=="filled"} for name,rows in records.items()}; labels=("A_mean_net_jpy","A_minus_C_conditional_mean_jpy","A_minus_B_conditional_mean_jpy","A_minus_A_continue_conditional_mean_jpy","A_minus_A_buy_conditional_mean_jpy","A_minus_A_sell_conditional_mean_jpy","delta_ols_jpy"); values={label:[] for label in labels}
    for draw in samples:
        picked=[dates[item] for item in draw]; values["A_mean_net_jpy"].append(fmean(daily["A"][item] for item in picked))
        for name in ("C","B","A_continue","A_buy","A_sell"):
            a=[filled["A"][item] for item in picked if item in filled["A"]]; b=[filled[name][item] for item in picked if item in filled[name]]; values[f"A_minus_{name}_conditional_mean_jpy"].append(fmean(a)-fmean(b))
        weights=np.zeros(len(reg)); [weights.__setitem__(index[item],weights[index[item]]+1) for item in picked if item in index]; used=weights>0; root=np.sqrt(weights[used]); values["delta_ols_jpy"].append(fwl_delta((x[used]*root[:,None])[:,1],y[used]*root,np.delete(x[used]*root[:,None],1,axis=1))[0])
    estimates={"A_mean_net_jpy":fmean(daily["A"].values()),**{f"A_minus_{name}_conditional_mean_jpy":fmean(filled["A"].values())-fmean(filled[name].values()) for name in ("C","B","A_continue","A_buy","A_sell")},"delta_ols_jpy":fwl_delta(x[:,1],y,np.delete(x,1,axis=1))[0]}; boot={"method":"20 trade_date noncircular MBB, common index, tail truncate, linear percentile; fixed nuisance-only FWL","repetitions":10000,"seed":SEED,"block_length_trade_dates":20,**{key:{"estimate":estimates[key],"ci95_percentile_linear":[percentile(value,.025),percentile(value,.975)]} for key,value in values.items()}}; write_json(OUT/"bootstrap.json",boot); write_json(OUT/"daily_net_pnl_aligned.json",{"trade_dates":dates,"no_trade_value_jpy":0,"series":daily}); write_json(OUT/"regression_ledger.json",reg)
    metrics=cast(dict[str,Any],results["A"]["metrics"]); years={str(year):sum(daily["A"][item] for item in dates if item.startswith(str(year))) for year in range(2021,2026)}; months={f"{year}-{month:02d}":sum(daily["A"][item] for item in dates if item.startswith(f"{year}-{month:02d}")) for year in range(2021,2026) for month in range(1,13) if (year,month)<=(2025,6)}; directions={value:sum(row.get("status")=="filled" and row.get("side")==value for row in records["A"]) for value in ("long","short")}; info={"E>=800":sum(row.get("status")=="E" for row in base)>=800,"B>=190":len(trades["B"])>=190,"A>=70":len(trades["A"])>=70,"C>=100":len(trades["C"])>=100,"A_buy_sell>=25":all(value>=25 for value in directions.values()),"A95>=30":len(trades["A95"])>=30}; lower=lambda name:cast(list[float],cast(dict[str,object],boot[name])["ci95_percentile_linear"])[0]>0; gates={"A_net_positive":metrics["overall"]["net_pnl_jpy"]>0,"A_pf_gt1":metrics["overall"]["profit_factor"] is not None and metrics["overall"]["profit_factor"]>1,"CI_A_AminusC_AminusB_delta_positive":all(lower(name) for name in ("A_mean_net_jpy","A_minus_C_conditional_mean_jpy","A_minus_B_conditional_mean_jpy","delta_ols_jpy")),"A_beats_continue_buy_sell":all(lower(f"A_minus_{name}_conditional_mean_jpy") for name in ("A_continue","A_buy","A_sell")),"cost_delay_sensitivities_positive_pf":all(cast(dict[str,Any],results[name]["metrics"])["overall"]["net_pnl_jpy"]>0 and cast(dict[str,Any],results[name]["metrics"])["overall"]["profit_factor"] is not None and cast(dict[str,Any],results[name]["metrics"])["overall"]["profit_factor"]>1 for name in (*VARIANTS,*SENSITIVITIES)),"three_positive_2021_2024":sum(years[str(year)]>0 for year in range(2021,2025))>=3,"positive_months>=27":sum(value>0 for value in months.values())>=27,"top10_removed_positive":metrics["concentration"]["net_excluding_top10_jpy"]>0}; decision="INCONCLUSIVE" if not all(info.values()) else "INVESTIGATE" if all(gates.values()) else "REJECT"; write_json(OUT/"breakdowns.json",{"A_year_net_jpy":years,"A_month_net_jpy":months,"positive_months":sum(value>0 for value in months.values()),"A_direction_count":directions,"A_direction_performance":{value:research_metrics(tuple(trade for trade in trades["A"] if trade.side.value==value),target_bars)["overall"] for value in directions}}); write_json(OUT/"development_results.json",{"experiment_id":IDENTIFIER,"decision":decision,"quality":"PASS_LIMITED","information_gate":info,"fixed_gates":gates,"E":sum(row.get("status")=="E" for row in base),"conditions":results,"OLS":{"delta":estimates["delta_ols_jpy"],"formula":"fixed R059 FWL"},"scope":"Development only; OOS and Final Holdout not evaluated/accessed."}); write_json(OUT/"COMPLETED.json",{"experiment_id":IDENTIFIER,"status":"development_complete","decision":decision,"oos":"NOT_EVALUATED","final_holdout":"NOT_ACCESSED"})


if __name__ == "__main__": main()
