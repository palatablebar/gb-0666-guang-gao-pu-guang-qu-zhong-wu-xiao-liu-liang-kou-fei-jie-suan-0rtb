from fastapi.testclient import TestClient
from app.main import app
from app.config import MODULES

def test_pages_and_api():
    with TestClient(app) as c:
        for slug,_ in MODULES:
            assert c.get("/"+slug).status_code==200
            assert c.get("/api/"+slug).status_code==200
def test_export_and_no_loss_id():
    with TestClient(app) as c:
        data=c.post("/api/runs",json={"sample":"dirty"}).json()
        assert data["rows"][0]["event_id"]=="9007199254740993000"
        assert "BOT" in c.get('/api/runs/'+data["manifest"]["run_id"]+'/evidence.csv').text
def test_wrong_csv():
    with TestClient(app) as c:assert c.post("/api/runs",json={"text":"bad,header\n1,2"}).status_code==422
def test_conflict_adjudication_end_to_end():
    with TestClient(app) as c:
        data=c.post("/api/runs",json={"sample":"conflict"}).json()
        rid=data["manifest"]["run_id"]
        assert {d["event_id"] for d in data["adjudications"]}=={"CFL-1","CFL-2","CFL-3"}
        page=c.get("/quarantine?run_id="+rid)
        assert page.status_code==200
        for token in ("冲突裁决","采用依据","未采信·机器","未采信·版本","机器流量优先排除","账单零采信"):
            assert token in page.text
        evidence=c.get("/api/runs/"+rid+"/evidence.csv").text
        assert "ADOPT_BASIS" in evidence and "NOT_ADOPTED" in evidence
        # 重跑幂等：同一 run_id
        again=c.post("/api/runs",json={"sample":"conflict"}).json()
        assert again["manifest"]["run_id"]==rid
