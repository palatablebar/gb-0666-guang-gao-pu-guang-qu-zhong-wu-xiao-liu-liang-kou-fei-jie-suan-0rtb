import unicodedata,json
from datetime import datetime,timezone,timedelta
from decimal import Decimal,InvalidOperation
from app.config import SENTINELS
from app.errors import reject

# 冲突裁决依据：机器流量永不入账单；非机器回执按事件时间最早采信，
# 事件时间相同再以物理来源行兜底，保证同批输入的裁决结果确定、可复算。
BASIS_BOT="BOT_EXCLUDED"
BASIS_EARLIEST="EARLIEST_EVENT_TIME"
BASIS_LINE="LINE_ORDER_TIE_BREAK"
BASIS_ALL_BOT="ALL_BOT"

def amount(text):
    value=unicodedata.normalize("NFKC",str(text)).strip().replace(",","")
    if value in SENTINELS:reject("MISSING","资费缺失")
    try:number=Decimal(value)
    except InvalidOperation:reject("CONTRACT","资费无效")
    if not number.is_finite() or number<0:reject("CONTRACT","资费无效")
    return number

def contract(policy):
    if not isinstance(policy.get("frequency_cap"),int) or policy["frequency_cap"]<1:reject("CONTRACT","频次上限无效")
    for channel,rule in policy["channels"].items():
        amount(rule["cpm"])
        if rule.get("currency")!="CNY":reject("CONTRACT","币种不支持")
    return policy

def normalize(row,policy):
    for key in ("event_id","at","channel","user_id","kind"):
        if row[key].strip() in SENTINELS:reject("MISSING","事件字段缺失")
    if row["channel"] not in policy["channels"]:reject("CONTRACT","渠道资费缺失")
    if row["kind"] not in {"impression","reversal"}:reject("CONTRACT","事件类型未知")
    if row["bot"] not in {"0","1"}:reject("CONTRACT","机器标记无效")
    try:
        at=datetime.fromisoformat(row["at"].replace("Z","+00:00"))
        if at.tzinfo is None:raise ValueError()
    except ValueError:reject("TIME","时间须包含偏移")
    if row["kind"]=="reversal" and row["ref"].strip() in SENTINELS:reject("REFERENCE","冲销缺少原曝光ID")
    utc=at.astimezone(timezone.utc);day=utc.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    return {**row,"source":{k:v for k,v in row.items() if k!="line"},"at":utc.isoformat(),"day":day,"cpm":str(amount(policy["channels"][row["channel"]]["cpm"]))}

def _fingerprint(row):
    return json.dumps({k:v for k,v in row.items() if k!="line"},sort_keys=True,ensure_ascii=False)

def adjudicate(rows):
    """按曝光ID分组裁决。返回(采信行, 隔离记录, 冲突裁决台账)。

    完全相同的回执去重；字段冲突时按 机器标记→事件时间→来源行 的确定性
    优先级选出唯一采信版本，未采信版本完整保留进隔离，裁决过程全部留痕。
    """
    groups={}
    for row in rows:groups.setdefault(row["event_id"],[]).append(row)
    good=[];bad=[];decisions=[]
    for event_id,items in groups.items():
        unique={_fingerprint(x) for x in items}
        if len(unique)==1:
            chosen=min(items,key=lambda x:x["line"]);good.append(chosen)
            bad.extend({"line":x["line"],"code":"DUPLICATE","raw":x} for x in items if x["line"]!=chosen["line"])
            continue
        humans=[x for x in items if x["bot"]!="1"];machines=[x for x in items if x["bot"]=="1"]
        basis=[BASIS_BOT] if machines else []
        if humans:
            earliest_at=min(x["at"] for x in humans)
            same_time=sorted((x for x in humans if x["at"]==earliest_at),key=lambda x:x["line"])
            chosen=same_time[0]
            basis.append(BASIS_LINE if len(same_time)>1 else BASIS_EARLIEST)
            good.append(chosen)
        else:
            chosen=None;basis=[BASIS_ALL_BOT]
        versions=[]
        for x in sorted(items,key=lambda r:(r["at"],r["line"])):
            versions.append({"line":x["line"],"adopted":chosen is not None and x["line"]==chosen["line"],
                             "bot":x["bot"],"at":x["at"],"channel":x["channel"],"user_id":x["user_id"],
                             "kind":x["kind"],"ref":x["ref"],"raw":x})
        decisions.append({"event_id":event_id,"adopted":chosen is not None,
                          "adopted_line":chosen["line"] if chosen else None,"basis":basis,"versions":versions})
        for x in items:
            if chosen is not None and x["line"]==chosen["line"]:continue
            code="CONFLICT_BOT" if x["bot"]=="1" else "CONFLICT_VERSION"
            bad.append({"line":x["line"],"code":code,"raw":x,"event_id":event_id,
                        "adopted_line":chosen["line"] if chosen else None})
    good.sort(key=lambda x:x["line"]);bad.sort(key=lambda x:x["line"]);decisions.sort(key=lambda d:d["event_id"])
    return good,bad,decisions

def deduplicate(rows):
    """兼容旧调用名：仅返回(采信行, 隔离记录)。"""
    good,bad,_=adjudicate(rows)
    return good,bad
