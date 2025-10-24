from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
import os

app = FastAPI(title="Kimwosabi Forex Signal API", version="2.0")

# Allow frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./signals.db")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Database Model ---
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

# --- Pydantic Models ---
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

# --- Routes ---

@app.post("/admin/login")
def admin_login(credentials: AdminLogin):
    """
    Admin login endpoint.
    """
    if credentials.email == "admin@sabi.tech" and credentials.password == "Sabi@2025":
        return {"status": "success", "message": "Admin login successful"}
    else:
        raise HTTPException(status_code=401, detail="Invalid admin credentials")

@app.post("/signal")
def create_signal(signal: Signal):
    """
    Create a new trading signal.
    """
    db = SessionLocal()
    db_signal = SignalDB(**signal.dict())
    db.add(db_signal)
    db.commit()
    db.refresh(db_signal)
    db.close()
    return {"status": "Signal added successfully", "signal": db_signal.__dict__}

@app.get("/signals")
def get_signals():
    """
    Get all signals.
    """
    db = SessionLocal()
    signals = db.query(SignalDB).all()
    db.close()
    return {"total": len(signals), "signals": [s.__dict__ for s in signals]}
