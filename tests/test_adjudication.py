from app.modules.m2_contract.service import run
from app.modules.m2_contract.rules import adjudicate,BASIS_BOT,BASIS_EARLIEST,BASIS_LINE,BASIS_ALL_BOT
from app.modules.m7_batches.service import run as batch
from app.modules.m7_batches.rules import csv_evidence
from app.store import history

CONFLICT={"sample":"conflict"}

def _decision(result,event_id):
    return next(d for d in result["adjudications"] if d["event_id"]==event_id)

def test_human_wins_over_bot_and_keeps_evidence():
    r=run(CONFLICT);d=_decision(r,"CFL-1")
    assert d["adopted"] is True and d["adopted_line"]==3
    assert BASIS_BOT in d["basis"] and BASIS_EARLIEST in d["basis"]
    # 机器版本永不入账单，且完整保留
    assert all(v["bot"]!="1" for v in d["versions"] if v["adopted"])
    assert {v["line"] for v in d["versions"]}=={2,3}
    assert {(x["line"],x["code"]) for x in r["quarantine"] if x["event_id"]=="CFL-1"}=={(2,"CONFLICT_BOT")}

def test_line_tie_break_deterministic():
    d=_decision(run(CONFLICT),"CFL-2")
    assert d["adopted_line"]==4 and d["basis"]==[BASIS_LINE]
    assert [v["line"] for v in d["versions"] if v["adopted"]]==[4]

def test_all_bot_zero_adoption():
    r=run(CONFLICT);d=_decision(r,"CFL-3")
    assert d["adopted"] is False and d["adopted_line"] is None and d["basis"]==[BASIS_ALL_BOT]
    assert not any(v["adopted"] for v in d["versions"])
    assert {x["code"] for x in r["quarantine"] if x["event_id"]=="CFL-3"}=={"CONFLICT_BOT"}

def test_order_invariant():
    # 裁决结果不应依赖回执到达顺序：打乱后结论一致
    from app.modules.m1_intake.service import run as intake
    from app.modules.m2_contract.rules import normalize,contract
    import json
    from app.config import SAMPLE_DIR
    policy=contract(json.loads((SAMPLE_DIR/"rates.json").read_text()))
    normalized=[normalize(row,policy) for row in intake(CONFLICT)["rows"]]
    g1,_,a1=adjudicate(normalized)
    g2,_,a2=adjudicate(list(reversed(normalized)))
    assert {(x["event_id"],x["line"]) for x in g1}=={(x["event_id"],x["line"]) for x in g2}
    assert [(d["event_id"],d["adopted_line"]) for d in a1]==[(d["event_id"],d["adopted_line"]) for d in a2]

def test_no_conflict_in_clean_sample():
    assert run({"sample":"clean"})["adjudications"]==[]

def test_identical_receipts_still_dedupe():
    # 内容完全相同的多份回执仍走 DUPLICATE，不算冲突
    r=run({"sample":"dirty"})
    assert [x["code"] for x in r["quarantine"]].count("DUPLICATE")==1
    assert r["adjudications"]==[]

def test_reversal_returns_to_original_day_and_only_once():
    r=batch(CONFLICT)
    group=next(g for g in r["results"] if g["channel"]=="DSP-C")
    # 冲销发生在 11-02，但抵扣回到原曝光账日 11-01
    assert group["day"]=="2026-11-01" and group["reversed"]==1
    assert group["credit"]=="0.01" and group["net"]=="0.00"
    # 第二次冲销被隔离，不产生重复冲销记录
    assert [x["code"] for x in r["quarantine"]].count("OVER_REVERSE")==1

def test_rerun_creates_no_run_no_impression_no_duplicate_reversal():
    a=batch(CONFLICT);b=batch(CONFLICT)
    assert a["manifest"]["run_id"]==b["manifest"]["run_id"]
    assert len(history())==1
    assert a["rows"]==b["rows"]
    assert [x["code"] for x in b["quarantine"]].count("OVER_REVERSE")==1

def test_conflict_records_never_lost_and_conservation_holds():
    r=batch(CONFLICT)
    # 所有回执版本都在裁决台账中，隔离记录均带行号/原因码/原值
    total_versions=sum(len(d["versions"]) for d in r["adjudications"])
    assert total_versions==6
    assert all("line" in x and x["code"] and "raw" in x for x in r["quarantine"])
    assert r["reconciliation"]["count_ok"] is True
    assert r["reconciliation"]["amount_ok"] is True

def test_evidence_csv_keeps_adopted_basis_and_every_version():
    r=batch(CONFLICT);text=csv_evidence(r)
    lines=[ln for ln in text.splitlines() if ln.startswith(r["manifest"]["run_id"])]
    assert len([ln for ln in lines if ",ADOPT_BASIS," in ln])==3
    assert len([ln for ln in lines if ",ADOPTED," in ln])==2
    assert len([ln for ln in lines if ",NOT_ADOPTED," in ln])==4
    for event_id in ("CFL-1","CFL-2","CFL-3"):
        assert event_id in text
