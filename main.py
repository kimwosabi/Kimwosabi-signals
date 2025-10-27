from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
import os, requests, base64, json

# --- Initialize App ---
app = FastAPI(title="Kimwosabi Forex Signal API", version="2.0")

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Database Setup ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./signals.db")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class SignalDB(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True, index=True)
    pair = Column(String)
    direction = Column(String)
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# --- Models ---
class Signal(BaseModel):
    pair: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    timestamp: datetime

class AdminLogin(BaseModel):
    email: str
    password: str

class PaymentRequest(BaseModel):
    phone_number: str
    amount: int

# --- Admin Login ---
@app.post("/admin/login")
def admin_login(credentials: AdminLogin):
    if credentials.email == "admin@sabi.tech" and credentials.password == "Sabi@2025":
        return {"status": "success"}
    raise HTTPException(status_code=401, detail="Invalid admin credentials")

# --- Add Signal ---
@app.post("/signal")
def create_signal(signal: Signal):
    db = SessionLocal()
    db_signal = SignalDB(**signal.dict())
    db.add(db_signal)
    db.commit()
    db.refresh(db_signal)
    db.close()
    return {"status": "Signal added successfully", "signal": db_signal.__dict__}

# --- Get Signals ---
@app.get("/signals")
def get_signals():
    db = SessionLocal()
    signals = db.query(SignalDB).all()
    db.close()
    return {"total": len(signals), "signals": [s.__dict__ for s in signals]}

# ===========================================================
# 💳 M-PESA Integration
# ===========================================================

# --- Your M-PESA credentials ---
CONSUMER_KEY = "7lA2ZfIYvGbGSi4fgLmOwhpeavb6pGAaHsrzm8slT0QWJuun"
CONSUMER_SECRET = "TQpLf40AltYpwusM3XGy1E1IG9MW5PD53G8frD67xpsGVkeGCOv2WMXqLnSVlr3s"
PASSKEY = "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"
SHORTCODE = "3771522"  # your till number

def get_access_token():
    url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    response = requests.get(url, auth=(CONSUMER_KEY, CONSUMER_SECRET))
    token = response.json().get("access_token")
    return token

@app.post("/mpesa/stkpush")
def stk_push(payment: PaymentRequest):
    try:
        access_token = get_access_token()
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password = base64.b64encode((SHORTCODE + PASSKEY + timestamp).encode()).decode("utf-8")

        payload = {
            "BusinessShortCode": SHORTCODE,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": payment.amount,
            "PartyA": payment.phone_number,
            "PartyB": SHORTCODE,
            "PhoneNumber": payment.phone_number,
            "CallBackURL": "https://kimwosabi-signals-1.onrender.com/mpesa/callback",
            "AccountReference": "SABI TECH",
            "TransactionDesc": "SABI TECH Subscription"
        }

        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        response = requests.post(
            "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers=headers,
        )

        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/mpesa/callback")
async def mpesa_callback(request: Request):
    data = await request.json()
    print("📥 M-PESA CALLBACK DATA:", json.dumps(data, indent=4))
    return {"ResultCode": 0, "ResultDesc": "Accepted"}
