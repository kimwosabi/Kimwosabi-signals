from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base

# Initialize app
app = FastAPI(title="Kimwosabi Forex Signal API", version="2.0")

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or ["http://localhost:3000"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup (PostgreSQL)
DATABASE_URL = "postgresql+psycopg2://sabifx_user:hCfV8l2sWyZ1Ib04dIkvBvgkpPcxjTYs@dpg-d3t1o01r0fns738se45g-a.oregon-postgres.render.com/sabifx"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Model
class SignalDB(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True, index=True)
    pair = Column(String, index=True)
    direction = Column(String)
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    timestamp = Column(DateTime, default=datetime.utcnow)

# Create DB tables
Base.metadata.create_all(bind=engine)

# Pydantic model (for request validation)
class Signal(BaseModel):
    pair: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    timestamp: datetime

@app.get("/")
def home():
    return {"message": "Welcome to Kimwosabi Forex Signal API"}

@app.post("/signal")
def create_signal(signal: Signal):
    db = SessionLocal()
    db_signal = SignalDB(
        pair=signal.pair,
        direction=signal.direction,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        timestamp=signal.timestamp,
    )
    db.add(db_signal)
    db.commit()
    db.refresh(db_signal)
    db.close()
    return {"status": "Signal added successfully", "signal": db_signal.__dict__}

@app.get("/signals")
def get_signals():
    db = SessionLocal()
    signals = db.query(SignalDB).all()
    db.close()
    return {"total": len(signals), "signals": [s.__dict__ for s in signals]}
