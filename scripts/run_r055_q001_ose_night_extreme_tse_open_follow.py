"""Execute preregistered Development-only R055-Q001 without OOS access."""
# ruff: noqa: E701, E702, E703, I001
from __future__ import annotations
import json
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import ceil, floor, log
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
from n225m_bt.calendar.session_rules import regime_for_trade_date
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r053 import R053QNotIdentifiableError, fwl_delta
from n225m_bt.research.r055 import r055_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.r055_night_extreme_follow import R055NightExtremeFollowStrategy

ROOT=Path(__file__).resolve().parents[1]
IDENTIFIER="r055-q001-20260915-ose-night-extreme-tse-open-follow-04"
OUT=ROOT/"results"/"research"/IDENTIFIER
AXIS=ROOT/"results"/"research"/"r012-q001-20260914-night-direction-followthrough-01"/"daily_net_pnl_aligned.json"
PARENT_HASH="f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"; QUARANTINE_HASH="2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED=20261002; PINV_RCOND=1e-12; TOL=1e-12
BASE=("A","B","C","A_fade","A_buy","A_sell"); VARIANTS={"A2":(2,0,90,30),"A3":(3,0,90,30),"A_delay":(1,1,90,30)}; SENS={"A85":(1,0,85,30),"A95":(1,0,95,30),"A_h15":(1,0,90,15),"A_h45":(1,0,90,45)}

def digest(path:Path)->str:
 h=sha256();
 with path.open("rb") as f:
  for b in iter(lambda:f.read(1<<20),b""): h.update(b)
 return h.hexdigest()
def axis()->list[str]:
 x=json.loads(AXIS.read_text(encoding="utf-8"))["trade_dates"]
 if not isinstance(x,list) or len(x)!=1111 or len(set(x))!=1111: raise ValueError("fixed 1,111-day axis unavailable")
 return cast(list[str],x)
def quarantine(data:ResearchData)->tuple[ResearchData,dict[str,object],set[tuple[date,Session]]]:
 g=session_groups(data.bars); isolated={k for k,v in g.items() if any("TICK_GRID_VIOLATION" in b.quality_flags for b in v)}; included=[b for k,v in g.items() if k not in isolated for b in v]
 listed=[{"trade_date":d.isoformat(),"session":s.value} for d,s in sorted(isolated)]
 audit={"parent_data_version":data.data_version,"quarantined_sessions":len(isolated),"quarantined_bars":len(data.bars)-len(included),"included_sessions":len(g)-len(isolated),"included_bars":len(included),"quarantined_session_list":listed,"quarantined_session_list_hash":canonical_hash(listed),"included_tick_grid_violations":sum("TICK_GRID_VIOLATION" in b.quality_flags for b in included)}
 expected={"parent_data_version":PARENT_HASH,"quarantined_sessions":45,"quarantined_bars":27345,"included_sessions":2216,"included_bars":1326086,"quarantined_session_list_hash":QUARANTINE_HASH,"included_tick_grid_violations":0}; mismatch={k:{"actual":audit[k],"expected":v} for k,v in expected.items() if audit[k]!=v}
 if mismatch: raise ValueError(f"R004 isolation mismatch: {mismatch}")
 return ResearchData(included,canonical_hash({"parent":data.data_version,"sessions":listed}),data.quality|{"quarantine":audit}),audit,isolated
def manifest(root:Path)->dict[str,object]:
 fs=[{"path":str(p.resolve().relative_to(ROOT)),"sha256":digest(p)} for p in partition_paths(root,"development")]
 return {"status":"frozen_before_price_statistics_events_or_pnl","trade_date_range":["2021-01-01","2025-06-30"],"scope":"Selected normalized Development Parquet only; raw, volume, external prices, OOS and Final Holdout prohibited.","files":fs,"files_hash":canonical_hash(fs)}
def select(e:dict[str,object],condition:str,threshold:int)->bool:
 if e.get("status")!="E" or not e.get("direction_eligible"): return False
 x=float(e["x"])
 return x>=float(e["q75"]) if condition=="B" else float(e["q75"])<=x<float(e["q90"]) if condition=="C" else x>=float(e[f"q{threshold}"])
def side(e:dict[str,object],condition:str)->str:
 follow="long" if int(e["night_sign"])>0 else "short"
 if condition=="A_fade": return "short" if follow=="long" else "long"
 if condition=="A_buy": return "long"
 if condition=="A_sell": return "short"
 return follow
def rows_for(e:dict[str,object],groups:dict[tuple[date,Session],list[Bar]],holding:int)->list[Bar]|None:
 d=date.fromisoformat(cast(str,e["trade_date"])); n0=datetime.fromisoformat(cast(str,e["nS_jst"])); c=datetime.fromisoformat(cast(str,e["cN_bar_start_jst"])); ds=datetime.fromisoformat(cast(str,e["planned_entry_jst"])); count=int((c-n0).total_seconds()//60)+1
 byn={b.ts_jst:b for b in groups.get((d,Session.NIGHT),[])}; byd={b.ts_jst:b for b in groups.get((d,Session.DAY),[])}
 n=[byn.get(n0+timedelta(minutes=i)) for i in range(count)]; day=[byd.get(ds+timedelta(minutes=i)) for i in range(holding+1)]
 if any(b is None or not b.is_eligible or b.trade_date!=d for b in [*n,*day]): return None
 return cast(list[Bar],[*n,*day])
def run_condition(base:list[dict[str,object]],groups:dict[tuple[date,Session],list[Bar]],engine:BacktestEngine,condition:str,*,ticks:int,delay:int,threshold:int,holding:int)->tuple[tuple[Trade,...],list[dict[str,object]],dict[str,int]]:
 ledger=[]; trades=[]; audit:Counter[str]=Counter()
 for original in base:
  e=dict(original); ok=select(e,condition,threshold); e.update(condition=condition,condition_eligible=ok,requested_slippage_ticks=ticks,requested_delay_minutes=delay,requested_holding_minutes=holding)
  if not ok: e.update(status="skipped",condition_reason="DIRECTION_ZERO_OR_THRESHOLD_NOT_MET");ledger.append(e);continue
  path=rows_for(e,groups,holding)
  if path is None: e.update(status="skipped",condition_reason="SCHEDULED_EXECUTION_PATH_MISSING");ledger.append(e);continue
  direction=side(e,condition); entry=datetime.fromisoformat(cast(str,e["planned_entry_jst"])); exit_=datetime.fromisoformat(cast(str,e[f"planned_exit{holding}_jst"])); signal=datetime.fromisoformat(cast(str,e["cN_bar_start_jst"])); e.update(side=direction,planned_exit_jst=exit_.isoformat())
  result=engine.run(path,R055NightExtremeFollowStrategy(f"r055_{condition}",signal,entry,exit_,direction,delay),canonical_hash({"condition":condition,"ticks":ticks,"delay":delay,"threshold":threshold,"holding":holding}));audit["canceled_orders"]+=result.canceled_orders
  if len(result.trades)>1: raise ValueError("max one position violated")
  if not result.trades: e.update(status="cancelled",condition_reason="ENGINE_NO_FILL_OR_EXIT");ledger.append(e);continue
  t=result.trades[0]; e.update(status="filled",entry_ts_jst=t.entry_ts.isoformat(),exit_ts_jst=t.exit_ts.isoformat(),entry_signal_ts_jst=t.entry_signal_ts.isoformat(),exit_signal_ts_jst=t.exit_signal_ts.isoformat() if t.exit_signal_ts else None,exit_reason=t.exit_reason.value,gross_pnl_jpy=t.gross_pnl_jpy,fees_jpy=t.fees_jpy,slippage_cost_jpy=t.slippage_cost_jpy,net_pnl_jpy=t.net_pnl_jpy,entry_delay_minutes=round((t.entry_ts-entry).total_seconds()/60),exit_delay_minutes=round((t.exit_ts-exit_).total_seconds()/60));trades.append(t);ledger.append(e)
 return tuple(replace(t,trade_id=f"trade-{i:06d}") for i,t in enumerate(sorted(trades,key=lambda x:x.entry_ts),1)),ledger,dict(audit)
def pct(x:list[float],q:float)->float:
 z=sorted(x); p=(len(z)-1)*q; a,b=floor(p),ceil(p); return z[a] if a==b else z[a]+(z[b]-z[a])*(p-a)
def draws(ax:list[str])->list[list[int]]:
 rng=Random(SEED);out=[]
 for _ in range(10000):
  z=[]
  while len(z)<len(ax):
   s=rng.randrange(len(ax)-19);z.extend(range(s,s+20))
  out.append(z[:len(ax)])
 return out
def design(reg:list[dict[str,object]])->tuple[np.ndarray,np.ndarray,list[str]]:
 years=sorted({date.fromisoformat(cast(str,r["trade_date"])).year for r in reg});x=[];y=[]
 for r in reg:
  yr=date.fromisoformat(cast(str,r["trade_date"])).year;x.append([1.,float(r["Q"]),float(r["z"]),float(r["gap_bps"]),float(r["prior_tse_return_bps"]),float(r["night_range_bps"]),float(r["night_up"]),*[1. if yr==v else 0. for v in years[1:]]]);y.append(float(r["y_gross_jpy"]))
 return np.asarray(x),np.asarray(y),["intercept","Q","z_ln_x_q90","gap_bps","prior_tse_return_bps","night_range_bps","night_up",*[f"calendar_year_{v}" for v in years[1:]]]
def fwl(reg:list[dict[str,object]])->tuple[float,int,int,float]:
 x,y,_=design(reg);d,ss=fwl_delta(x[:,1],y,np.delete(x,1,axis=1),pinv_rcond=PINV_RCOND,residual_ss_tolerance=TOL);return d,int(np.linalg.matrix_rank(x)),x.shape[1],ss
def regression(base:list[dict[str,object]],g:dict[tuple[date,Session],list[Bar]],classifier:CalendarClassifier,cal:ExchangeCalendar)->list[dict[str,object]]:
 out=[]
 for e in base:
  if e.get("status")!="E" or not e.get("direction_eligible"):continue
  d=date.fromisoformat(cast(str,e["trade_date"])); prior=cal.get(d).previous_trade_date if cal.get(d) else None
  if prior is None: raise ValueError("unmapped prior TSE day")
  by={b.ts_jst:b for b in g.get((d,Session.DAY),[])}; prev={b.ts_jst:b for b in g.get((prior,Session.DAY),[])}; start=classifier.session_open(prior,Session.DAY); reg=regime_for_trade_date(classifier.sessions,prior); end=datetime.combine(prior,reg.day.regular_end or reg.day.session_close,JST)-timedelta(minutes=1); a,b=prev.get(start),prev.get(end); entry=datetime.fromisoformat(cast(str,e["planned_entry_jst"])); exit_=datetime.fromisoformat(cast(str,e["planned_exit30_jst"]));
  if a is None or b is None or a.open<=0 or not a.is_eligible or not b.is_eligible: raise ValueError("fixed prior TSE covariate unavailable")
  sign=int(e["night_sign"]);x=float(e["x"]);q=float(e["q90"])
  if x<=0 or q<=0: raise ValueError("directional R055 regression has invalid x/q90")
  out.append({"trade_date":d.isoformat(),"y_gross_jpy":sign*(by[exit_].open-by[entry].open)*100,"Q":int(x>=q),"z":log(x/q),"gap_bps":sign*(by[entry].open-float(e["cN_points"]))/float(e["cN_points"])*10000,"prior_tse_return_bps":sign*(b.close-a.open)/a.open*10000,"night_range_bps":e["night_range_bps"],"night_up":int(sign>0)})
 return out
def technical(reg:list[dict[str,object]],ax:list[str],samples:list[list[int]])->dict[str,object]:
 if not reg:
  return {"status":"BLOCKED","stage":"observational_sample","error":"R055 Q is not identifiable: no direction-eligible common-E observations exist after the exact scheduled-night completeness and 120-day/minimum-100 rules.","repetitions_not_run":10000,"action":"No replicate was discarded, redrawn, or altered."}
 x,y,cols=design(reg); index={r["trade_date"]:i for i,r in enumerate(reg)}
 try: _,ss=fwl_delta(x[:,1],y,np.delete(x,1,axis=1),pinv_rcond=PINV_RCOND,residual_ss_tolerance=TOL)
 except R053QNotIdentifiableError as exc:return {"status":"BLOCKED","stage":"observed","error":str(exc)}
 for n,ids in enumerate(samples,1):
  w=np.zeros(len(reg));
  for i in ids:
   if ax[i] in index:w[index[ax[i]]]+=1
  take=w>0;root=np.sqrt(w[take]);xx=x[take]*root[:,None]
  try:fwl_delta(xx[:,1],y[take]*root,np.delete(xx,1,axis=1),pinv_rcond=PINV_RCOND,residual_ss_tolerance=TOL)
  except R053QNotIdentifiableError as exc:return {"status":"BLOCKED","stage":"bootstrap","replicate_number_one_based":n,"error":str(exc),"sampled_trade_dates":[ax[i] for i in ids]}
 return {"status":"PASS","observed_Q_residual_sum_of_squares":ss,"columns":cols,"rank":int(np.linalg.matrix_rank(x)),"repetitions":10000,"block_length_trade_dates":20,"seed":SEED,"pinv_rcond":PINV_RCOND,"residual_ss_tolerance":TOL}
def bootstrap(daily:dict[str,dict[str,int]],records:dict[str,list[dict[str,object]]],reg:list[dict[str,object]],ax:list[str],samples:list[list[int]])->dict[str,object]:
 labels=("A_mean_net_jpy","A_minus_B_conditional_mean_jpy","A_minus_C_conditional_mean_jpy","A_minus_A_fade_conditional_mean_jpy","A_minus_A_buy_conditional_mean_jpy","A_minus_A_sell_conditional_mean_jpy","delta_ols_jpy"); vals={k:[] for k in labels};filled={n:{cast(str,r["trade_date"]):int(r["net_pnl_jpy"]) for r in rs if r.get("status")=="filled"} for n,rs in records.items()};x,y,_=design(reg);idx={r["trade_date"]:i for i,r in enumerate(reg)}
 for ids in samples:
  days=[ax[i] for i in ids];vals["A_mean_net_jpy"].append(fmean(daily["A"][d] for d in days))
  for n in ("B","C","A_fade","A_buy","A_sell"):
   aa=[filled["A"][d] for d in days if d in filled["A"]];bb=[filled[n][d] for d in days if d in filled[n]];vals[f"A_minus_{n}_conditional_mean_jpy"].append(fmean(aa)-fmean(bb) if aa and bb else float("nan"))
  w=np.zeros(len(reg));
  for d in days:
   if d in idx:w[idx[d]]+=1
  take=w>0;root=np.sqrt(w[take]);xx=x[take]*root[:,None];vals["delta_ols_jpy"].append(fwl_delta(xx[:,1],y[take]*root,np.delete(xx,1,axis=1),pinv_rcond=PINV_RCOND,residual_ss_tolerance=TOL)[0])
 if any(any(np.isnan(v) for v in z) for z in vals.values()):raise ValueError("bootstrap conditional population empty")
 point={"A_mean_net_jpy":fmean(daily["A"].values()),"delta_ols_jpy":fwl(reg)[0]}
 for n in ("B","C","A_fade","A_buy","A_sell"):point[f"A_minus_{n}_conditional_mean_jpy"]=fmean(filled["A"].values())-fmean(filled[n].values())
 return {"method":"20 trade_date noncircular moving-block bootstrap; common index, tail truncation, linear percentile; fixed FWL nuisance projection","repetitions":10000,"seed":SEED,"block_length_trade_dates":20,**{k:{"estimate":point[k],"ci95_percentile_linear":[pct(v,.025),pct(v,.975)]} for k,v in vals.items()}}
def main()->None:
 if OUT.exists():raise FileExistsError(f"immutable output exists: {OUT}")
 OUT.mkdir(parents=True);inst,sessions,data_cfg,baseline=load_project_config(ROOT/"config")
 runtime=baseline.model_copy(update={"execution":baseline.execution.model_copy(update={"allow_cross_session_pending_order":True,"max_fill_delay_minutes":20160}),"risk":baseline.risk.model_copy(update={"new_entry_cutoff_minutes_before_session_close":0})})
 if (runtime.execution.slippage_ticks,runtime.fees.jpy_per_side_per_contract,runtime.execution.max_fill_delay_minutes,runtime.execution.allow_cross_session_pending_order)!=(1,30,20160,True):raise ValueError("R055 cost/cross-session contract mismatch")
 source=snapshot_source(OUT,ROOT/"config",ROOT/"config"/"local_calendar.yaml");inputs=manifest(data_cfg.gold_root);files=[Path(__file__).relative_to(ROOT),Path("src/n225m_bt/research/r055.py"),Path("src/n225m_bt/strategies/r055_night_extreme_follow.py"),Path("tests/test_r055_q001.py")];implementation={str(p):digest(ROOT/p) for p in files}
 plan={"experiment_id":IDENTIFIER,"study_id":"R055-Q001","status":"frozen_before_price_statistics_events_or_pnl","seed":SEED,"scope":"Development 2021-01-01..2025-06-30 only; known-Development exploratory analysis.","duplicate_review":"R001-R054 checked before price statistics. R012 follows every nonzero full-night return only after 09:00-09:04 confirmation from 09:05 for 60 minutes. It has no 120-day q75/q85/q90/q95 magnitude classification, TSE-start entry, 30-minute hold, or fixed FWL increment; no identical registration exists.","hypothesis":"Causally extreme full OSE-night absolute returns continue in their direction during the first 30 scheduled TSE minutes and exceed smaller-night and same-event fade/fixed-side controls.","rule":"For the one explicit ExchangeCalendar night assigned to each trade_date, oN is first planned normal-night open and cN is last planned normal-night close; rN=(cN-oN)/oN, x=abs(rN). Every normal-night planned minute is required. Exactly prior 120 explicit scheduled trade_dates, no target/backfill, >=100 valid x, nearest-rank q75/q85/q90/q95, equality upper. E requires the night, all q, TSE open and 15/30/45 exits; zero rN remains E but has no direction. Signal is final-night close; entry is TSE first planned open and exits are fixed opens, so the gap is excluded.","conditions":"A q90 follow; B q75 follow; C q75<=x<q90 follow; A_fade opposite; A_buy/A_sell fixed same event. A2/A3, A_delay nonextended, A85/A95 and 15/45 only.","ols":"All direction-valid E: zero-tick pre-fee y=sign(rN)*(open(exit30)-open(entry))*100, Q, z=ln(x/q90), cN-to-entry directional gap bps, prior normal TSE directional return bps, night range bps, night-up, calendar-year FE. R053-Q002 FWL only: pinv_rcond=1e-12, residual-Q SS tolerance=1e-12; every nonidentified observation/draw BLOCKED without discard/redraw/column deletion.","execution":"Unmodified engine; cross-session pending order=true and max delay=20,160 minutes solely to carry the already-known final normal-night signal across scheduled weekends/holidays to the assigned TSE open. Supplied execution paths contain only the final normal-night segment and the exact day path, so a missing planned TSE entry bar still rejects execution. Normal-night closing-auction bars are not supplied. 1 contract/max 1, no Stop/Target/re-entry/early exit.","inputs":inputs,"source":source,"implementation":implementation,"r004":"fixed 45 sessions/27,345 bars isolation","oos":"NOT_EVALUATED","final_holdout":"NOT_ACCESSED","prohibited":["OOS","Final Holdout","WFA","rescue search","other anchors/thresholds/lookbacks/holds/filters"]}
 write_json(OUT/"input_manifest.json",inputs);write_json(OUT/"preregistration.json",plan);write_json(OUT/"campaign_manifest.json",{"campaign_id":IDENTIFIER,"status":"preregistered","started_at":datetime.now(timezone.utc).isoformat(),"plan_hash":canonical_hash(plan),"seed":SEED,"oos":"NOT_EVALUATED","final_holdout":"NOT_ACCESSED"})
 commands={"pytest":[executable,"-m","pytest","tests/test_r055_q001.py","tests/test_exit_after_entry_cutoff.py","-q"],"ruff":[executable,"-m","ruff","check","src/n225m_bt/research/r055.py","src/n225m_bt/strategies/r055_night_extreme_follow.py","tests/test_r055_q001.py",str(Path(__file__).relative_to(ROOT))],"mypy":[executable,"-m","mypy","src/n225m_bt/research/r055.py","src/n225m_bt/strategies/r055_night_extreme_follow.py"]}; validation={n:{"returncode":z.returncode,"stdout":z.stdout,"stderr":z.stderr} for n,c in commands.items() for z in [run(c,cwd=ROOT,capture_output=True,text=True,check=False)]};validation["coverage"]="trade_date assignment, regime/holiday/night endpoints, full night, rolling/minimum/rank/equality, missing/isolation/zero/prefix, cross-session TSE-open/fixed exits/delay/accounting/max1, predicates/axis/OOS-final lock";validation["status"]="PASS" if all(v["returncode"]==0 for v in validation.values() if isinstance(v,dict) and "returncode" in v) else "BLOCKED";write_json(OUT/"pre_execution_validation.json",validation)
 if validation["status"]!="PASS":raise ValueError("R055 synthetic/static validation failed before price access")
 classifier=CalendarClassifier(sessions,ExchangeCalendar.from_path(ROOT/"config"/"local_calendar.yaml"));cal=classifier.exchange_calendar;ax=axis();development=load_split(data_cfg.gold_root,"development");view,qaudit,isolated=quarantine(development);g=session_groups(view.bars)
 if {d.isoformat() for d,s in g if s is Session.DAY}!=set(ax):raise ValueError("fixed axis did not reproduce development day sessions")
 base=[]
 for value in ax:
  d=date.fromisoformat(value);hist=[];cur=d
  for _ in range(120):
   rec=cal.get(cur)
   if rec is None or rec.previous_trade_date is None:hist=[];break
   cur=rec.previous_trade_date;hist.append((cur,g.get((cur,Session.NIGHT)),(cur,Session.NIGHT) in isolated))
  base.append(r055_event(classifier,d,g.get((d,Session.NIGHT)),g.get((d,Session.DAY)),hist,night_quarantined=(d,Session.NIGHT) in isolated,day_quarantined=(d,Session.DAY) in isolated))
 write_json(OUT/"all_candidate_event_ledger.json",base);write_json(OUT/"preflight.json",{"status":"PASS_LIMITED","development_input":development.quality,"quarantine":qaudit,"fixed_axis":ax,"physical_io":"Development normalized Parquet only; OOS and Final Holdout not selected.","schedule_hashes":{"sessions_yaml":digest(ROOT/"config"/"sessions.yaml"),"exchange_calendar":digest(ROOT/"config"/"local_calendar.yaml")}})
 reg=regression(base,g,classifier,cal);samples=draws(ax);gate=technical(reg,ax,samples);write_json(OUT/"technical_fwl_gate.json",gate)
 if gate["status"]!="PASS":write_json(OUT/"BLOCKED.json",{"experiment_id":IDENTIFIER,"status":"BLOCKED","stage":"FWL_Q_identification","failure":gate,"action":"No trade performance, OOS, WFA, or holdout was generated."});return
 configs={n:(n,1,0,90,30) for n in BASE}|{n:("A",*v) for n,v in VARIANTS.items()}|{n:("A",*v) for n,v in SENS.items()};records={};trades={};daily={};results={};target_bars=[b for d in ax for b in g.get((date.fromisoformat(d),Session.DAY),[])]
 for n,(condition,ticks,delay,threshold,holding) in configs.items():
  cfg=runtime.model_copy(update={"execution":runtime.execution.model_copy(update={"slippage_ticks":ticks})});ts,ledger,aud=run_condition(base,g,BacktestEngine(inst.instrument.to_spec(),cfg,classifier),condition,ticks=ticks,delay=delay,threshold=threshold,holding=holding);series=dict.fromkeys(ax,0)
  for t in ts:series[t.trade_date.isoformat()]+=t.net_pnl_jpy
  folder=OUT/n;folder.mkdir();metrics=research_metrics(ts,target_bars);write_results(folder,ts,(),{"campaign_id":IDENTIFIER,"condition":n,"execution_audit":aud,"runtime_execution":cfg.model_dump(mode="json")});write_json(folder/"events.json",ledger);write_json(folder/"daily_net_pnl_aligned.json",series);write_json(folder/"research_metrics.json",metrics);records[n]=ledger;trades[n]=ts;daily[n]=series;results[n]={"trade_count":len(ts),"metrics":metrics,"audit":aud}
 a_dates={r["trade_date"] for r in records["A"] if r.get("status")=="filled"};checks={"A_subset_B_C_subset_B":all((not select(e,"A",90) or select(e,"B",90)) and (not select(e,"C",90) or select(e,"B",90)) for e in base),"predicates":all((r.get("status")=="filled")==select(base[i],c,t) for n,(c,_,_,t,_) in configs.items() for i,r in enumerate(records[n])),"same_A_controls_event_entry_exit":all(all(records[n][i].get(k)==records["A"][i].get(k) for k in ("nS_jst","cN_bar_start_jst","planned_entry_jst","planned_exit_jst")) for n in ("A_fade","A_buy","A_sell") for i,d in enumerate(ax) if d in a_dates),"A_fade_opposite_side":all(records["A"][i].get("side")!=records["A_fade"][i].get("side") for i,d in enumerate(ax) if d in a_dates),"variants_same_event_side":all(all(records[n][i].get(k)==records["A"][i].get(k) for k in ("night_sign","side","planned_entry_jst")) for n in ("A2","A3","A_h15","A_h45") for i in range(len(ax)) if records[n][i].get("status")=="filled"),"threshold_side":all(r.get("side")==side(base[i],"A") for n in ("A85","A95") for i,r in enumerate(records[n]) if r.get("status")=="filled"),"delay_nonextension":all(records["A_delay"][i].get(k)==records["A"][i].get(k) for k in ("night_sign","side","planned_exit_jst") for i,d in enumerate(ax) if d in a_dates),"fixed_1111_axis":all(len(x)==1111 for x in daily.values()),"one_trade_max":all(len({t.trade_date for t in ts})==len(ts) for ts in trades.values()),"execution_accounting":all(r.get("exit_reason")==ExitReason.SIGNAL.value and r.get("exit_delay_minutes")==0 and r.get("entry_delay_minutes")== (1 if n=="A_delay" else 0) and int(r["net_pnl_jpy"])==int(r["gross_pnl_jpy"])-int(r["fees_jpy"]) for n,rs in records.items() for r in rs if r.get("status")=="filled")};write_json(OUT/"execution_accounting_audit.json",{"status":"PASS" if all(checks.values()) else "BLOCKED","checks":checks,"accounting":"Gross is fill-to-fill/slippage-inclusive; Net=Gross-fees; no double slippage."})
 if not all(checks.values()):raise ValueError("R055 execution/accounting gate failed")
 boot=bootstrap(daily,records,reg,ax,samples);delta,rank,cols,ss=fwl(reg);boot["ols"]={"delta":delta,"rank":rank,"columns":cols,"Q_residual_sum_of_squares":ss,"rows":len(reg),"formula":"y~Q+ln(x/q90)+directional cN-entry gap bps+directional prior TSE return bps+night range bps+night-up+calendar-year FE"};write_json(OUT/"bootstrap.json",boot);write_json(OUT/"daily_net_pnl_aligned.json",{"trade_dates":ax,"no_trade_value_jpy":0,"series":daily});write_json(OUT/"regression_ledger.json",reg)
 am=cast(dict[str,Any],results["A"]["metrics"]);years={str(y):sum(daily["A"][d] for d in ax if d.startswith(str(y))) for y in range(2021,2026)};months={f"{y}-{m:02d}":sum(daily["A"][d] for d in ax if d.startswith(f"{y}-{m:02d}")) for y in range(2021,2026) for m in range(1,13) if (y,m)<=(2025,6)};directions={x:sum(r.get("status")=="filled" and r.get("side")==x for r in records["A"]) for x in ("long","short")};info={"E>=850":sum(e.get("status")=="E" for e in base)>=850,"A>=80":len(trades["A"])>=80,"B>=200":len(trades["B"])>=200,"C>=100":len(trades["C"])>=100,"A_buy_sell>=25":all(v>=25 for v in directions.values()),"A95>=35":len(trades["A95"])>=35}
 def positive(n:str)->bool:return cast(list[float],boot[n]["ci95_percentile_linear"])[0]>0
 gates={"A_net_positive":am["overall"]["net_pnl_jpy"]>0,"A_pf_gt1":am["overall"]["profit_factor"] is not None and am["overall"]["profit_factor"]>1,"CI_A_AminusB_AminusC_delta_positive":all(positive(n) for n in ("A_mean_net_jpy","A_minus_B_conditional_mean_jpy","A_minus_C_conditional_mean_jpy","delta_ols_jpy")),"A_beats_fade_fixed_buy_fixed_sell":all(positive(f"A_minus_{n}_conditional_mean_jpy") for n in ("A_fade","A_buy","A_sell")),"all_variants_positive_pf":all(results[n]["metrics"]["overall"]["net_pnl_jpy"]>0 and results[n]["metrics"]["overall"]["profit_factor"] is not None and results[n]["metrics"]["overall"]["profit_factor"]>1 for n in (*VARIANTS,*SENS)),"three_positive_2021_2024":sum(years[str(y)]>0 for y in range(2021,2025))>=3,"positive_months>=27":sum(v>0 for v in months.values())>=27,"top10_removed_positive":am["concentration"]["net_excluding_top10_jpy"]>0};decision="INCONCLUSIVE" if not all(info.values()) else "INVESTIGATE" if all(gates.values()) else "REJECT";write_json(OUT/"breakdowns.json",{"A_year_net_jpy":years,"A_month_net_jpy":months,"positive_months":sum(v>0 for v in months.values()),"A_direction_count":directions,"A_direction_performance":{x:research_metrics(tuple(t for t in trades["A"] if t.side.value==x),target_bars)["overall"] for x in directions}});write_json(OUT/"development_results.json",{"experiment_id":IDENTIFIER,"decision":decision,"quality":"PASS_LIMITED","information_gate":info,"fixed_gates":gates,"E":sum(e.get("status")=="E" for e in base),"conditions":results,"OLS":boot["ols"],"scope":"Development only; OOS and Final Holdout not evaluated/accessed."});write_json(OUT/"COMPLETED.json",{"experiment_id":IDENTIFIER,"status":"development_complete","decision":decision,"oos":"NOT_EVALUATED","final_holdout":"NOT_ACCESSED"})
if __name__=="__main__":main()
