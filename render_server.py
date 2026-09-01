import os
import json
import hmac
import hashlib
import base64
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DATABASE_URL = os.getenv("DATABASE_URL")
SESSION_SECRET = os.getenv("PORTFOLIO_SESSION_SECRET")
SESSION_DAYS = 1
SESSION_COOKIE = "portfolio_session"
ADMIN_COOKIE = "admin_session"

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")
if not SESSION_SECRET:
    raise RuntimeError("PORTFOLIO_SESSION_SECRET is required")

ADMIN_USER = os.getenv("PORTFOLIO_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("PORTFOLIO_ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    raise RuntimeError("PORTFOLIO_ADMIN_PASSWORD is required")

ADMIN_ACCOUNTS = {
    ADMIN_USER: {"password": ADMIN_PASSWORD, "prefixes": None, "label": "Full Admin"},
    "1312": {"password": os.getenv("PORTFOLIO_1312_PASSWORD", "1312"), "prefixes": ("1312",), "label": "1312 Supervisor"},
    "1313": {"password": os.getenv("PORTFOLIO_1313_PASSWORD", "1313"), "prefixes": ("1313", "1314"), "label": "1313/1314 Supervisor"},
    "1304": {"password": os.getenv("PORTFOLIO_1304_PASSWORD", "1304"), "prefixes": ("1304",), "label": "1304 Supervisor"},
}

app = FastAPI(title="Client Portfolio Portal", version="4.0-render")


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def norm(v: Any) -> str:
    return "" if v is None else str(v).strip()


def clean_client_code(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    text = str(v).strip()
    try:
        f = float(text)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass
    return text


def number(v: Any):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return v


def make_token(subject: str, kind: str) -> str:
    payload = {"sub": subject, "kind": kind, "exp": int((datetime.utcnow() + timedelta(days=SESSION_DAYS)).timestamp()), "nonce": secrets.token_urlsafe(8)}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(SESSION_SECRET.encode(), raw.encode(), hashlib.sha256).digest()
    return raw + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


def read_token(token: str | None, expected_kind: str) -> str | None:
    if not token or "." not in token:
        return None
    raw, sig = token.rsplit(".", 1)
    try:
        expected = hmac.new(SESSION_SECRET.encode(), raw.encode(), hashlib.sha256).digest()
        got = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        if not hmac.compare_digest(got, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode())
        if payload.get("kind") != expected_kind or int(payload.get("exp", 0)) < int(datetime.utcnow().timestamp()):
            return None
        return str(payload.get("sub", ""))
    except Exception:
        return None


def require_client(request: Request) -> str:
    client = read_token(request.cookies.get(SESSION_COOKIE), "client")
    if not client:
        raise HTTPException(401, "Login required")
    return clean_client_code(client)


def require_admin(request: Request) -> str:
    admin = read_token(request.cookies.get(ADMIN_COOKIE), "admin")
    if not admin or admin not in ADMIN_ACCOUNTS:
        raise HTTPException(401, "Admin login required")
    return admin


def admin_can_access_client(username: str, client_id: Any) -> bool:
    account = ADMIN_ACCOUNTS.get(str(username).strip())
    if not account:
        return False
    prefixes = account.get("prefixes")
    if prefixes is None:
        return True
    client = clean_client_code(client_id).upper()
    return bool(client) and any(client.startswith(str(prefix).upper()) for prefix in prefixes)


def meta():
    with db() as con:
        rows = con.execute("SELECT key,value FROM app_meta WHERE key IN ('headers','meta')").fetchall()
    d = {r["key"]: r["value"] for r in rows}
    return d.get("headers", []), d.get("meta", {})


def row_to_api(r):
    data = dict(r.get("data") or {})
    data["_excel_row"] = int(r["id"])
    data["_client_code"] = r["client_id"]
    data["_symbol"] = r["symbol"]
    data["_mtm"] = r["mtm"]
    data["_ltp"] = r["ltp"]
    data["_quantity"] = r["quantity"]
    data["_buy_price"] = r["buy_price"]
    data["_realised_profit"] = r["realised_profit"]
    data["_change"] = r["change_value"]

    data["_normal_mtf"] = (
    data.get("NORMAL / MTF")
    or data.get("NORMAL/MTF")
    or data.get("NORMAL_MTF")
)

data["_portfolio_id"] = (
    data.get("PORTFOLIO ID")
    or data.get("PORTFOLIO_ID")
    or data.get("Portfolio ID")
)
    return data


def password_hash(value: str) -> str:
    return hmac.new(os.getenv("MIGRATION_PASSWORD_HASH_SECRET", SESSION_SECRET).encode(), value.strip().upper().encode(), hashlib.sha256).hexdigest()


def authenticate_client(client_id: str, password: str = "") -> str | None:
    with db() as con:
        r = con.execute(
            "SELECT client_id FROM clients WHERE upper(client_id)=upper(%s)",
            (clean_client_code(client_id),)
        ).fetchone()

    if not r:
        return None

    return r["client_id"]


def insert_request(con, *, client_id, symbol, excel_row, field, old_value, requested_value, requested_quantity, reason, status, reviewed_at=None, reviewed_by=None, admin_remark=None):
    return con.execute(
        """INSERT INTO change_requests
        (client_id,symbol,excel_row,field,old_value,requested_value,requested_quantity,reason,status,created_at,reviewed_at,reviewed_by,admin_remark)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (client_id, symbol, excel_row, field, old_value, requested_value, requested_quantity, reason, status,
         datetime.now().isoformat(timespec="seconds"), reviewed_at, reviewed_by, admin_remark)
    ).fetchone()["id"]


class LoginRequest(BaseModel):
    client_id: str
    password: str

class AdminLoginRequest(BaseModel):
    username: str
    password: str

class ChangeRequestIn(BaseModel):
    action: str = "BUY_PRICE"
    symbol: str
    excel_row: int | None = Field(default=None, gt=0)
    new_buy_price: float | None = Field(default=None, gt=0)
    quantity: float | None = Field(default=None, gt=0)
    reason: str = ""
    admin_approval_required: bool = True

class ReviewRequest(BaseModel):
    action: str
    remark: str = ""

class AdminDirectDeleteIn(BaseModel):
    client_id: str
    symbol: str
    excel_row: int = Field(gt=0)

class AdminAddScripIn(BaseModel):
    client_id: str
    symbol: str
    quantity: float = Field(gt=0)
    buy_price: float = Field(gt=0)

class AdminModifyRecordIn(BaseModel):
    client_id: str
    symbol: str
    excel_row: int = Field(gt=0)
    quantity: float = Field(gt=0)
    buy_price: float = Field(gt=0)


def get_portfolio_row(con, lot, client=None, symbol=None):
    q = "SELECT * FROM portfolio WHERE id=%s"
    params = [lot]
    if client is not None:
        q += " AND upper(client_id)=upper(%s)"; params.append(client)
    if symbol is not None:
        q += " AND upper(symbol)=upper(%s)"; params.append(symbol)
    return con.execute(q, params).fetchone()


def update_row(con, r, quantity=None, buy_price=None):
    data = dict(r["data"] or {})
    if quantity is not None:
        data["BUY QUANTITY"] = quantity
    if buy_price is not None:
        data["BUY PRICE"] = buy_price
        if "ORGINAL BUY PRICE" in data:
            data["ORGINAL BUY PRICE"] = buy_price
        if "ORIGINAL BUY PRICE" in data:
            data["ORIGINAL BUY PRICE"] = buy_price
    return data


@app.post("/api/login")
def login(body: LoginRequest, request: Request):
    client = authenticate_client(body.client_id, body.password)
    if not client:
        raise HTTPException(401, "Invalid Client ID or password")
    response = JSONResponse({"ok": True, "client_id": client})
    response.set_cookie(SESSION_COOKIE, make_token(client, "client"), max_age=SESSION_DAYS*86400, httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/")
    return response

@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True}); response.delete_cookie(SESSION_COOKIE, path="/"); return response

@app.get("/api/me")
def me(request: Request):
    return {"logged_in": True, "client_id": require_client(request)}

@app.get("/api/portfolio")
def portfolio(request: Request, symbol: str | None = Query(default=None)):
    client = require_client(request)
    with db() as con:
        q = "SELECT * FROM portfolio WHERE upper(client_id)=upper(%s)"; params = [client]
        if symbol:
            q += " AND upper(symbol)=upper(%s)"; params.append(symbol.strip())
        q += " ORDER BY CASE WHEN mtm IS NULL THEN 1 ELSE 0 END, mtm DESC"
        rows = con.execute(q, params).fetchall()
    headers, meta_data = meta()
    api_rows = [row_to_api(r) for r in rows]
    return {"client_code": client, "count": len(api_rows),
            "mtm_total": sum((r["mtm"] or 0) for r in rows if isinstance(r["mtm"], (int,float))),
            "realised_profit_total": sum((r["realised_profit"] or 0) for r in rows if isinstance(r["realised_profit"], (int,float))),
            "headers": headers, "meta": meta_data, "rows": api_rows}

@app.get("/api/symbols")
def symbols(request: Request):
    client = require_client(request)
    with db() as con:
        rows = con.execute("SELECT DISTINCT symbol FROM portfolio WHERE upper(client_id)=upper(%s) AND symbol<>'' ORDER BY symbol", (client,)).fetchall()
    return {"symbols": [r["symbol"] for r in rows]}

@app.post("/api/requests")
def create_request(body: ChangeRequestIn, request: Request):
    client = require_client(request); action = body.action.strip().upper(); symbol = norm(body.symbol).upper()
    with db() as con:
        lot = get_portfolio_row(con, body.excel_row, client, symbol) if body.excel_row else None
        excel_row = None; old_value = None; requested_quantity = None
        if action == "BUY_PRICE":
            if not lot or body.new_buy_price is None: raise HTTPException(400, "Portfolio lot and New Buy Price are required.")
            current = lot["buy_price"]
            if current is None or abs(float(current)-float(body.new_buy_price)) < 1e-9: raise HTTPException(400, "New Buy Price is invalid or unchanged.")
            field="BUY PRICE"; excel_row=lot["id"]; old_value=float(current); requested_value=float(body.new_buy_price)
        elif action == "NEW_LOT":
            if not symbol or body.quantity is None or body.new_buy_price is None: raise HTTPException(400, "Symbol, quantity and Buy Price are required.")
            field="NEW LOT"; requested_value=float(body.new_buy_price); requested_quantity=float(body.quantity)
        elif action == "DELETE_RECORD":
            if not lot: raise HTTPException(404, "Selected portfolio lot was not found.")
            field="DELETE RECORD"; excel_row=lot["id"]; old_value=float(lot["buy_price"] or 0); requested_value=0; requested_quantity=float(lot["quantity"] or 0)
        else: raise HTTPException(400, "Action must be BUY_PRICE, NEW_LOT or DELETE_RECORD.")

        if not body.admin_approval_required:
            if field == "BUY PRICE":
                data=update_row(con, lot, buy_price=requested_value)
                con.execute("UPDATE portfolio SET buy_price=%s,data=%s::jsonb WHERE id=%s", (requested_value, json.dumps(data), excel_row))
            elif field == "NEW LOT":
                headers,_=meta(); data={h:None for h in headers}
                data["CLIENT CODE"]=client; data["SYMBOL"]=symbol; data["BUY QUANTITY"]=requested_quantity; data["BUY PRICE"]=requested_value
                cur=con.execute("""INSERT INTO portfolio(client_id,symbol,quantity,buy_price,data) VALUES(%s,%s,%s,%s,%s::jsonb) RETURNING id""",(client,symbol,requested_quantity,requested_value,json.dumps(data)))
                excel_row=cur.fetchone()["id"]
            else:
                con.execute("DELETE FROM portfolio WHERE id=%s", (excel_row,))
            rid=insert_request(con,client_id=client,symbol=symbol,excel_row=excel_row,field=field,old_value=old_value,requested_value=requested_value,requested_quantity=requested_quantity,reason=body.reason.strip(),status="DIRECT",reviewed_at=datetime.now().isoformat(timespec="seconds"),reviewed_by=client,admin_remark="Client chose direct update; Admin approval not required.")
            con.commit(); return {"ok":True,"mode":"DIRECT","request_id":rid,"status":"DIRECT","field":field,"excel_row":excel_row}

        if excel_row is not None:
            existing=con.execute("SELECT id FROM change_requests WHERE client_id=%s AND excel_row=%s AND status='PENDING'",(client,excel_row)).fetchone()
        else:
            existing=con.execute("SELECT id FROM change_requests WHERE client_id=%s AND symbol=%s AND field='NEW LOT' AND status='PENDING' AND requested_value=%s AND requested_quantity=%s",(client,symbol,requested_value,requested_quantity)).fetchone()
        if existing: raise HTTPException(409,"A pending request already exists for this item.")
        rid=insert_request(con,client_id=client,symbol=symbol,excel_row=excel_row,field=field,old_value=old_value,requested_value=requested_value,requested_quantity=requested_quantity,reason=body.reason.strip(),status="PENDING")
        con.commit(); return {"ok":True,"mode":"PENDING","request_id":rid,"status":"PENDING","field":field,"excel_row":excel_row}

@app.get("/api/requests")
def my_requests(request: Request):
    client=require_client(request)
    with db() as con:
        rows=con.execute("SELECT * FROM change_requests WHERE client_id=%s ORDER BY id DESC",(client,)).fetchall()
    return {"requests":rows}

@app.delete("/api/requests/{request_id}")
def delete_my_request(request_id:int,request:Request):
    client=require_client(request)
    with db() as con:
        row=con.execute("SELECT * FROM change_requests WHERE id=%s AND client_id=%s",(request_id,client)).fetchone()
        if not row: raise HTTPException(404,"Request not found")
        if row["status"]=="PENDING": raise HTTPException(409,"Pending requests cannot be deleted. Ask the administrator to reject it first.")
        con.execute("DELETE FROM change_requests WHERE id=%s",(request_id,)); con.commit()
    return {"ok":True,"deleted":request_id}

@app.post("/api/admin/login")
def admin_login(body:AdminLoginRequest,request:Request):
    username=body.username.strip(); account=ADMIN_ACCOUNTS.get(username)
    if not account or not hmac.compare_digest(body.password,str(account["password"])): raise HTTPException(401,"Invalid admin username or password")
    response=JSONResponse({"ok":True,"username":username,"label":account.get("label",username),"prefixes":account.get("prefixes")})
    response.set_cookie(ADMIN_COOKIE,make_token(username,"admin"),max_age=SESSION_DAYS*86400,httponly=True,secure=request.url.scheme=="https",samesite="lax",path="/")
    return response

@app.post("/api/admin/logout")
def admin_logout():
    response=JSONResponse({"ok":True}); response.delete_cookie(ADMIN_COOKIE,path="/"); return response

@app.get("/api/admin/me")
def admin_me(request:Request):
    username=require_admin(request); a=ADMIN_ACCOUNTS[username]
    return {"logged_in":True,"username":username,"label":a.get("label",username),"prefixes":a.get("prefixes")}

@app.get("/api/admin/portfolio")
def admin_portfolio(request:Request,client_code:str|None=Query(default=None),symbol:str|None=Query(default=None)):
    admin=require_admin(request)
    with db() as con:
        rows=con.execute("SELECT * FROM portfolio ORDER BY upper(client_id), CASE WHEN mtm IS NULL THEN 1 ELSE 0 END, mtm DESC").fetchall()
    rows=[r for r in rows if admin_can_access_client(admin,r["client_id"])]
    if client_code: rows=[r for r in rows if r["client_id"].upper()==clean_client_code(client_code).upper()]
    if symbol: rows=[r for r in rows if r["symbol"].upper()==norm(symbol).upper()]
    headers,meta_data=meta(); api_rows=[row_to_api(r) for r in rows]
    clients=sorted({r["client_id"] for r in rows if r["client_id"]}); symbols=sorted({r["symbol"] for r in rows if r["symbol"]})
    return {"count":len(rows),"client_count":len(clients),"clients":clients,"symbols":symbols,"mtm_total":sum((r["mtm"] or 0) for r in rows if isinstance(r["mtm"],(int,float))),"realised_profit_total":sum((r["realised_profit"] or 0) for r in rows if isinstance(r["realised_profit"],(int,float))),"headers":headers,"meta":meta_data,"rows":api_rows}

@app.post("/api/admin/add-script")
def admin_add_script(body:AdminAddScripIn,request:Request):
    admin=require_admin(request); client=clean_client_code(body.client_id); symbol=norm(body.symbol).upper()
    if not client or not symbol: raise HTTPException(400,"Client and Symbol are required.")
    if not admin_can_access_client(admin,client): raise HTTPException(403,"This admin account is not authorized for this client.")
    headers,_=meta(); data={h:None for h in headers}; data["CLIENT CODE"]=client; data["SYMBOL"]=symbol; data["BUY QUANTITY"]=body.quantity; data["BUY PRICE"]=body.buy_price
    if "ORGINAL BUY PRICE" in data: data["ORGINAL BUY PRICE"]=body.buy_price
    if "ORIGINAL BUY PRICE" in data: data["ORIGINAL BUY PRICE"]=body.buy_price
    with db() as con:
        rid=con.execute("INSERT INTO portfolio(client_id,symbol,quantity,buy_price,data) VALUES(%s,%s,%s,%s,%s::jsonb) RETURNING id",(client,symbol,body.quantity,body.buy_price,json.dumps(data))).fetchone()["id"]
        insert_request(con,client_id=client,symbol=symbol,excel_row=rid,field="NEW LOT",old_value=0,requested_value=body.buy_price,requested_quantity=body.quantity,reason="Admin added new scrip",status="DIRECT",reviewed_at=datetime.now().isoformat(timespec="seconds"),reviewed_by=admin,admin_remark="Added directly by Admin; approval not required.")
        con.commit()
    return {"ok":True,"mode":"DIRECT","excel_row":rid,"client_id":client,"symbol":symbol,"quantity":body.quantity,"buy_price":body.buy_price}

@app.post("/api/admin/modify-record")
def admin_modify_record(body:AdminModifyRecordIn,request:Request):
    admin=require_admin(request); client=clean_client_code(body.client_id); symbol=norm(body.symbol).upper(); lot=int(body.excel_row)
    if not admin_can_access_client(admin,client): raise HTTPException(403,"This admin account is not authorized for this client.")
    with db() as con:
        r=get_portfolio_row(con,lot,client,symbol)
        if not r: raise HTTPException(404,"Selected portfolio record was not found or no longer matches.")
        old_qty=float(r["quantity"] or 0); old_buy=float(r["buy_price"] or 0)
        if abs(old_qty-body.quantity)<1e-9 and abs(old_buy-body.buy_price)<1e-9: raise HTTPException(400,"Quantity and Buy Price are unchanged.")
        data=update_row(con,r,quantity=body.quantity,buy_price=body.buy_price)
        con.execute("UPDATE portfolio SET quantity=%s,buy_price=%s,data=%s::jsonb WHERE id=%s",(body.quantity,body.buy_price,json.dumps(data),lot))
        insert_request(con,client_id=client,symbol=symbol,excel_row=lot,field="MODIFY RECORD",old_value=old_buy,requested_value=body.buy_price,requested_quantity=body.quantity,reason="Admin modified quantity/buy price",status="DIRECT",reviewed_at=datetime.now().isoformat(timespec="seconds"),reviewed_by=admin,admin_remark="Modified directly by Admin; approval not required.")
        con.commit()
    return {"ok":True,"mode":"DIRECT","excel_row":lot,"client_id":client,"symbol":symbol,"old_quantity":old_qty,"old_buy_price":old_buy,"quantity":body.quantity,"buy_price":body.buy_price}

@app.post("/api/admin/direct-delete")
def admin_direct_delete(body:AdminDirectDeleteIn,request:Request):
    admin=require_admin(request); client=clean_client_code(body.client_id); symbol=norm(body.symbol).upper(); lot=int(body.excel_row)
    if not admin_can_access_client(admin,client): raise HTTPException(403,"This admin account is not authorized for this client.")
    with db() as con:
        r=get_portfolio_row(con,lot,client,symbol)
        if not r: raise HTTPException(404,"Selected portfolio record was not found or no longer matches.")
        old=float(r["buy_price"] or 0); qty=float(r["quantity"] or 0)
        con.execute("DELETE FROM portfolio WHERE id=%s",(lot,))
        insert_request(con,client_id=client,symbol=symbol,excel_row=lot,field="DELETE RECORD",old_value=old,requested_value=0,requested_quantity=qty,reason="Admin direct deletion",status="DIRECT",reviewed_at=datetime.now().isoformat(timespec="seconds"),reviewed_by=admin,admin_remark="Deleted directly by Admin; approval not required.")
        con.commit()
    return {"ok":True,"mode":"DIRECT","deleted_row":lot}

@app.get("/api/admin/requests")
def admin_requests(request:Request,status:str="PENDING"):
    admin=require_admin(request)
    with db() as con:
        if status.upper()=="ALL": rows=con.execute("SELECT * FROM change_requests ORDER BY id DESC").fetchall()
        else: rows=con.execute("SELECT * FROM change_requests WHERE status=%s ORDER BY id DESC",(status.upper(),)).fetchall()
    rows=[r for r in rows if admin_can_access_client(admin,r["client_id"])]
    return {"requests":rows}

@app.post("/api/admin/requests/{request_id}/review")
def review(request_id:int,body:ReviewRequest,request:Request):
    admin=require_admin(request); action=body.action.strip().upper()
    if action not in ("APPROVE","REJECT"): raise HTTPException(400,"Action must be APPROVE or REJECT")
    with db() as con:
        r=con.execute("SELECT * FROM change_requests WHERE id=%s",(request_id,)).fetchone()
        if not r: raise HTTPException(404,"Request not found")
        if not admin_can_access_client(admin,r["client_id"]): raise HTTPException(403,"This admin account is not authorized for this client.")
        if r["status"]!="PENDING": raise HTTPException(409,f"Request is already {r['status']}")
        now=datetime.now().isoformat(timespec="seconds")
        if action=="REJECT":
            con.execute("UPDATE change_requests SET status='REJECTED',reviewed_at=%s,reviewed_by=%s,admin_remark=%s WHERE id=%s",(now,admin,body.remark.strip(),request_id)); con.commit(); return {"ok":True,"status":"REJECTED"}
        field=r["field"]
        if field=="BUY PRICE":
            lot=get_portfolio_row(con,r["excel_row"],r["client_id"],r["symbol"])
            if not lot: raise HTTPException(404,"Portfolio lot no longer exists.")
            data=update_row(con,lot,buy_price=float(r["requested_value"]))
            con.execute("UPDATE portfolio SET buy_price=%s,data=%s::jsonb WHERE id=%s",(float(r["requested_value"]),json.dumps(data),r["excel_row"]))
        elif field=="NEW LOT":
            headers,_=meta(); data={h:None for h in headers}; data["CLIENT CODE"]=r["client_id"]; data["SYMBOL"]=r["symbol"]; data["BUY QUANTITY"]=r["requested_quantity"]; data["BUY PRICE"]=r["requested_value"]
            rid=con.execute("INSERT INTO portfolio(client_id,symbol,quantity,buy_price,data) VALUES(%s,%s,%s,%s,%s::jsonb) RETURNING id",(r["client_id"],r["symbol"],r["requested_quantity"],r["requested_value"],json.dumps(data))).fetchone()["id"]
            con.execute("UPDATE change_requests SET excel_row=%s WHERE id=%s",(rid,request_id))
        elif field=="DELETE RECORD":
            con.execute("DELETE FROM portfolio WHERE id=%s",(r["excel_row"],))
        else: raise HTTPException(400,f"Unsupported request field: {field}")
        con.execute("UPDATE change_requests SET status='APPROVED',reviewed_at=%s,reviewed_by=%s,admin_remark=%s WHERE id=%s",(now,admin,body.remark.strip(),request_id)); con.commit()
    return {"ok":True,"status":"APPROVED"}

@app.delete("/api/admin/requests/{request_id}")
def delete_request(request_id:int,request:Request):
    admin=require_admin(request)
    with db() as con:
        r=con.execute("SELECT * FROM change_requests WHERE id=%s",(request_id,)).fetchone()
        if not r: raise HTTPException(404,"Request not found")
        if not admin_can_access_client(admin,r["client_id"]): raise HTTPException(403,"This admin account is not authorized for this client.")
        if r["status"]=="PENDING": raise HTTPException(409,"Pending requests must be approved or rejected before deletion.")
        con.execute("DELETE FROM change_requests WHERE id=%s",(request_id,)); con.commit()
    return {"ok":True,"deleted":request_id}

@app.get("/api/health")
def health():
    with db() as con:
        con.execute("SELECT 1")
    return {"status":"ok","storage":"Postgres/Supabase","excel_dependency":False}

@app.get("/")
def home(): return FileResponse(WEB_DIR / "index.html")

@app.get("/admin")
def admin_page(): return FileResponse(WEB_DIR / "admin.html")

@app.get("/1312")
def supervisor_1312_page():
    return FileResponse(WEB_DIR / "admin.html")

@app.get("/1313")
def supervisor_1313_page():
    return FileResponse(WEB_DIR / "admin.html")

@app.get("/1304")
def supervisor_1304_page():
    return FileResponse(WEB_DIR / "admin.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT","10000")))
