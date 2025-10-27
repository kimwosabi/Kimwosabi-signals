# main_mpesa.py
import os
import base64
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base

# -----------------------
# Config from environment
# -----------------------
MPESA_CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY", "<your_consumer_key>")
MPESA_CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET", "<your_consumer_secret>")
MPESA_PASSKEY = os.getenv("MPESA_PASSKEY", "<your_passkey>")
MPESA_SHORTCODE = os.getenv("MPESA_SHORTCODE", "3771522")  # your till
MPESA_CALLBACK_BASE = os.getenv("MPESA_CALLBACK_BASE", "https://your-backend.example.com")  # Render URL
MPESA_CALLBACK_ENDPOINT = "/mpesa/callback"
MPESA_CALLBACK_URL = MPESA_CALLBACK_BASE.rstrip("/") + MPESA_CALLBACK_ENDPOINT

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./signals.db")

# Choose Daraja endpoints: sandbox vs production. For production use https://api.safaricom.co.ke
MPESA_OAUTH_URL = os.getenv("MPESA_OAUTH_URL", "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials")
MPESA_STK_PUSH_URL = os.getenv("MPESA_STK_PUSH_URL", "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest")

# -----------------------
# App & DB setup
# -----------------------
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

engine = create_engine(DATABASE_URL, connect_args={} if "postgresql" in DATABASE_URL else {"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# -----------------------
# DB Models
# -----------------------
class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    checkout_request_id = Column(String, unique=True, index=True, nullable=True)  # returned by STK Push
    merchant_request_id = Column(String, nullable=True)
    amount = Column(Float)
    phone = Column(String, index=True)
    status = Column(String, default="PENDING")  # PENDING / SUCCESS / FAILED
    mpesa_receipt = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True)
    tier = Column(String)  # "daily" / "weekly" / "monthly"
    amount = Column(Float)
    start_at = Column(DateTime)
    expires_at = Column(DateTime)
    active = Column(Boolean, default=True)

Base.metadata.create_all(bind=engine)

# -----------------------
# Pydantic models
# -----------------------
class STKRequest(BaseModel):
    phone: str  # in format 2547xxxxxxxx
    tier: str  # "daily", "weekly", "monthly"

class STKResponse(BaseModel):
    checkout_request_id: str | None = None
    merchant_request_id: str | None = None
    response_description: str | None = None

# -----------------------
# Helper: get access token
# -----------------------
def get_oauth_token():
    url = MPESA_OAUTH_URL
    auth = (MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET)
    r = requests.get(url, auth=auth, timeout=10)
    if r.status_code != 200:
        raise Exception(f"Daraja token error: {r.status_code} {r.text}")
    return r.json().get("access_token")

# -----------------------
# Helper: build password for STK push
# -----------------------
def build_stk_password(shortcode, passkey, timestamp_str):
    raw = f"{shortcode}{passkey}{timestamp_str}"
    return base64.b64encode(raw.encode()).decode()

# -----------------------
# Tier amounts & duration
# -----------------------
TIERS = {
    "daily": {"amount": 10.0, "days": 1},      # amounts are example KES values - change to your pricing
    "weekly": {"amount": 50.0, "days": 7},
    "monthly": {"amount": 150.0, "days": 30},
}

# -----------------------
# STK Push endpoint
# -----------------------
@app.post("/mpesa/stk_push", response_model=STKResponse)
def initiate_stk_push(req: STKRequest):
    tier = req.tier.lower()
    if tier not in TIERS:
        raise HTTPException(status_code=400, detail="Invalid tier")
    amount = TIERS[tier]["amount"]

    # prepare timestamps
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")  # Daraja expects Kenyan time normally; UTC works but ideally use local KST
    password = build_stk_password(MPESA_SHORTCODE, MPESA_PASSKEY, timestamp)

    try:
        token = get_oauth_token()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to get token: {str(e)}")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "BusinessShortCode": MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": req.phone,
        "PartyB": MPESA_SHORTCODE,
        "PhoneNumber": req.phone,
        "CallBackURL": MPESA_CALLBACK_URL,
        "AccountReference": f"SABI-{req.phone}-{tier}",
        "TransactionDesc": f"SABI {tier} subscription"
    }

    r = requests.post(MPESA_STK_PUSH_URL, json=payload, headers=headers, timeout=15)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"STK Push failed: {r.status_code} {r.text}")

    data = r.json()
    merchant_request_id = data.get("MerchantRequestID")
    checkout_request_id = data.get("CheckoutRequestID")
    response_desc = data.get("ResponseDescription") or data.get("errorMessage") or str(data)

    # save initial transaction (pending)
    db = SessionLocal()
    tx = Transaction(
        checkout_request_id=checkout_request_id,
        merchant_request_id=merchant_request_id,
        amount=amount,
        phone=req.phone,
        status="PENDING"
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    db.close()

    return {"checkout_request_id": checkout_request_id, "merchant_request_id": merchant_request_id, "response_description": response_desc}

# -----------------------
# MPESA callback endpoint
# Daraja will POST payment result to this URL
# -----------------------
@app.post("/mpesa/callback")
async def mpesa_callback(payload: Request):
    body = await payload.json()
    # Daraja callback structure encloses result in "Body" / "stkCallback" - handle both structures defensively
    try:
        result = body.get("Body", body).get("stkCallback", body.get("Body", {}).get("stkCallback", {}))
    except Exception:
        result = body

    merchant_request_id = result.get("MerchantRequestID")
    checkout_request_id = result.get("CheckoutRequestID")
    result_code = None
    result_desc = None
    callback_metadata = None
    try:
        result_code = result.get("ResultCode")
        result_desc = result.get("ResultDesc")
        callback_metadata = result.get("CallbackMetadata", {})
    except Exception:
        pass

    db = SessionLocal()
    tx = None
    if checkout_request_id:
        tx = db.query(Transaction).filter(Transaction.checkout_request_id == checkout_request_id).first()
    if not tx and merchant_request_id:
        tx = db.query(Transaction).filter(Transaction.merchant_request_id == merchant_request_id).first()

    # update transaction from callback
    if tx:
        tx.updated_at = datetime.utcnow()
        if result_code == 0:
            # success: extract MpesaReceiptNumber etc.
            tx.status = "SUCCESS"
            # parse metadata items: list of {Name, Value}
            items = callback_metadata.get("Item", []) if isinstance(callback_metadata, dict) else []
            receipt = None
            for it in items:
                if it.get("Name") in ("MpesaReceiptNumber", "ReceiptNumber", "MpesaReceiptNo"):
                    receipt = it.get("Value")
                # sometimes Amount and Phone are present
                if it.get("Name") == "Amount":
                    try:
                        tx.amount = float(it.get("Value"))
                    except Exception:
                        pass
                if it.get("Name") in ("PhoneNumber", "Phone"):
                    tx.phone = str(it.get("Value"))
            tx.mpesa_receipt = receipt
            # create subscription record
            # For mapping amount->tier, use TIERS mapping
            tier = None
            for k, v in TIERS.items():
                if float(v["amount"]) == float(tx.amount):
                    tier = k
                    break
            if not tier:
                # fallback: use account reference parse
                tier = "monthly"
            dur_days = TIERS.get(tier, {}).get("days", 30)
            start_at = datetime.utcnow()
            expires_at = start_at + timedelta(days=dur_days)
            sub = Subscription(phone=tx.phone, tier=tier, amount=tx.amount, start_at=start_at, expires_at=expires_at, active=True)
            db.add(sub)
        else:
            tx.status = "FAILED"
            tx.mpesa_receipt = result_desc
        db.commit()
    db.close()

    # Daraja expects a 200 with proper response; return success structure
    return {"ResultCode": 0, "ResultDesc": "Received"}

# -----------------------
# Endpoint to check transaction/subscription status by checkout_request_id or phone
# -----------------------
@app.get("/mpesa/status")
def check_mpesa_status(checkout_request_id: str | None = None, phone: str | None = None):
    db = SessionLocal()
    if checkout_request_id:
        tx = db.query(Transaction).filter(Transaction.checkout_request_id == checkout_request_id).first()
        db.close()
        if not tx:
            raise HTTPException(status_code=404, detail="Transaction not found")
        return {"checkout_request_id": tx.checkout_request_id, "status": tx.status, "mpesa_receipt": tx.mpesa_receipt}
    if phone:
        sub = db.query(Subscription).filter(Subscription.phone == phone, Subscription.expires_at > datetime.utcnow(), Subscription.active == True).order_by(Subscription.expires_at.desc()).first()
        db.close()
        if not sub:
            return {"active": False}
        return {"active": True, "tier": sub.tier, "expires_at": sub.expires_at.isoformat()}
    db.close()
    return {"detail": "Provide checkout_request_id or phone"}

# -----------------------
# Protecting signals endpoint: allow free 3 signals for non-subscribed numbers
# Client must pass `X-User-Phone` header OR ?phone= query param
# -----------------------
from fastapi import Header

@app.get("/signals_restricted")
def get_signals_restricted(phone: str | None = None, x_user_phone: str | None = Header(None)):
    # choose phone source
    phone_value = phone or x_user_phone
    db = SessionLocal()
    # fetch signals (example uses same signals table)
    # for this example assume you have `SignalDB` model in this DB (from your existing app)
    try:
        from sqlalchemy import Table, MetaData
        metadata = MetaData(bind=engine)
        signals_table = Table("signals", metadata, autoload_with=engine)
        results = db.execute(signals_table.select()).fetchall()
        signals_list = [dict(row) for row in results]
    except Exception:
        signals_list = []

    # check subscription
    sub = db.query(Subscription).filter(Subscription.phone == phone_value, Subscription.expires_at > datetime.utcnow(), Subscription.active == True).first() if phone_value else None
    db.close()
    if sub:
        return {"total": len(signals_list), "signals": signals_list, "paid": True}
    else:
        # allow first 3 signals only
        limited = signals_list[:3]
        return {"total": len(limited), "signals": limited, "paid": False, "message": "Upgrade to see all signals"}
