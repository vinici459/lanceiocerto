from __future__ import annotations

import asyncio
import math
import os
import re
import secrets
import shutil
import base64
import hashlib
import hmac
import smtplib
from email.message import EmailMessage
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    desc,
    inspect,
    text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker, selectinload


APP_NAME = "Lanceio Certo"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./lanceiocerto.db")
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    public_name: Mapped[str] = mapped_column(String(40), default="", index=True)
    # Mantém compatibilidade com bancos criados em versões anteriores, que tinham a coluna nickname como obrigatória.
    nickname: Mapped[str] = mapped_column(String(40), default="", index=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    email_verification_token: Mapped[str] = mapped_column(String(120), default="")
    email_verification_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    password: Mapped[str] = mapped_column(String(120))
    cpf: Mapped[str] = mapped_column(String(20), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    cep: Mapped[str] = mapped_column(String(20), default="")
    street: Mapped[str] = mapped_column(String(150), default="")
    number: Mapped[str] = mapped_column(String(30), default="")
    district: Mapped[str] = mapped_column(String(100), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    state: Mapped[str] = mapped_column(String(20), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    chat_muted: Mapped[bool] = mapped_column(Boolean, default=False)
    identity_status: Mapped[str] = mapped_column(String(30), default="pending")  # pending/verified/rejected
    identity_note: Mapped[str] = mapped_column(Text, default="")
    document_type: Mapped[str] = mapped_column(String(40), default="CPF")
    document_number: Mapped[str] = mapped_column(String(40), default="")
    document_file_url: Mapped[str] = mapped_column(String(600), default="")
    selfie_file_url: Mapped[str] = mapped_column(String(600), default="")
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    terms_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    privacy_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    wallet_balance: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    bids: Mapped[list["Bid"]] = relationship(back_populates="user")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="user")
    orders: Mapped[list["WinnerOrder"]] = relationship(back_populates="user")


class AuctionItem(Base):
    __tablename__ = "auction_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str] = mapped_column(String(500), default="https://via.placeholder.com/900x600?text=Produto")
    source_store: Mapped[str] = mapped_column(String(80), default="Mercado Livre")
    source_url: Mapped[str] = mapped_column(String(600), default="")
    source_price: Mapped[float] = mapped_column(Float, default=0.0)
    start_price: Mapped[float] = mapped_column(Float, default=0.0)
    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(30), default="scheduled")  # scheduled/relisted/live/pending_payment/ended
    scheduled_start: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    winner_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    winner_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    turbo_level: Mapped[int] = mapped_column(Integer, default=0)
    initial_duration_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    turbo_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    turbo_trigger_percent: Mapped[float] = mapped_column(Float, default=60.0)
    turbo_level_3_percent: Mapped[float] = mapped_column(Float, default=65.0)
    turbo_level_4_percent: Mapped[float] = mapped_column(Float, default=70.0)
    bid_fee_percent: Mapped[float] = mapped_column(Float, default=10.0)
    winner_min_percent: Mapped[float] = mapped_column(Float, default=50.0)
    target_profit_percent: Mapped[float] = mapped_column(Float, default=10.0)
    turbo_base_value: Mapped[float] = mapped_column(Float, default=0.0)
    cashback_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    total_bid_fees: Mapped[float] = mapped_column(Float, default=0.0)
    total_bid_spent: Mapped[float] = mapped_column(Float, default=0.0)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=True)
    chat_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    winner: Mapped[Optional[User]] = relationship(foreign_keys=[winner_user_id])
    bids: Mapped[list["Bid"]] = relationship(back_populates="auction", cascade="all, delete-orphan")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="auction", cascade="all, delete-orphan")
    orders: Mapped[list["WinnerOrder"]] = relationship(back_populates="auction", cascade="all, delete-orphan")


class Bid(Base):
    __tablename__ = "bids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    auction_id: Mapped[int] = mapped_column(ForeignKey("auction_items.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    bid_value: Mapped[float] = mapped_column(Float)
    fee_value: Mapped[float] = mapped_column(Float)
    price_increment: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    auction: Mapped[AuctionItem] = relationship(back_populates="bids")
    user: Mapped[User] = relationship(back_populates="bids")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    auction_id: Mapped[int] = mapped_column(ForeignKey("auction_items.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    message: Mapped[str] = mapped_column(String(250))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    auction: Mapped[AuctionItem] = relationship(back_populates="messages")
    user: Mapped[User] = relationship(back_populates="messages")


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WinnerOrder(Base):
    __tablename__ = "winner_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    auction_id: Mapped[int] = mapped_column(ForeignKey("auction_items.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    final_price: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(30), default="pending_payment")
    payment_deadline: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    pix_code: Mapped[str] = mapped_column(String(180), default="")
    payment_link: Mapped[str] = mapped_column(String(600), default="")
    delivery_name: Mapped[str] = mapped_column(String(120), default="")
    delivery_cep: Mapped[str] = mapped_column(String(20), default="")
    delivery_street: Mapped[str] = mapped_column(String(150), default="")
    delivery_number: Mapped[str] = mapped_column(String(30), default="")
    delivery_district: Mapped[str] = mapped_column(String(100), default="")
    delivery_city: Mapped[str] = mapped_column(String(80), default="")
    delivery_state: Mapped[str] = mapped_column(String(20), default="")
    tracking_code: Mapped[str] = mapped_column(String(120), default="")
    purchase_link: Mapped[str] = mapped_column(String(600), default="")
    purchase_status: Mapped[str] = mapped_column(String(40), default="")
    purchased_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    admin_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expired_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    auction: Mapped[AuctionItem] = relationship(back_populates="orders")
    user: Mapped[User] = relationship(back_populates="orders")



class AdminDirectMessage(Base):
    __tablename__ = "admin_direct_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("winner_orders.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    message: Mapped[str] = mapped_column(Text, default="")
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    order: Mapped[WinnerOrder] = relationship(foreign_keys=[order_id])
    user: Mapped[User] = relationship(foreign_keys=[user_id])
    admin: Mapped[User] = relationship(foreign_keys=[admin_id])


class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    pix_key: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending/approved/rejected/paid
    admin_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("winner_orders.id"), nullable=True)
    subject: Mapped[str] = mapped_column(String(160), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    proof_url: Mapped[str] = mapped_column(String(600), default="")
    status: Mapped[str] = mapped_column(String(30), default="open")  # open/in_review/dispute/resolved/closed
    admin_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    order: Mapped[Optional[WinnerOrder]] = relationship(foreign_keys=[order_id])


class OrderProof(Base):
    __tablename__ = "order_proofs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("winner_orders.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    file_url: Mapped[str] = mapped_column(String(600), default="")
    kind: Mapped[str] = mapped_column(String(40), default="shipping")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    order: Mapped[WinnerOrder] = relationship(foreign_keys=[order_id])
    user: Mapped[User] = relationship(foreign_keys=[user_id])


class CashbackEvent(Base):
    __tablename__ = "cashback_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    auction_id: Mapped[int] = mapped_column(ForeignKey("auction_items.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="open")
    join_deadline: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    winner_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    cashback_amount: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    drawn_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    auction: Mapped[AuctionItem] = relationship(foreign_keys=[auction_id])
    winner: Mapped[Optional[User]] = relationship(foreign_keys=[winner_user_id])


class CashbackEntry(Base):
    __tablename__ = "cashback_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("cashback_events.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    auction_id: Mapped[int] = mapped_column(ForeignKey("auction_items.id"))
    amount_spent: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    event: Mapped[CashbackEvent] = relationship(foreign_keys=[event_id])
    user: Mapped[User] = relationship(foreign_keys=[user_id])
    auction: Mapped[AuctionItem] = relationship(foreign_keys=[auction_id])


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80), default="")
    entity_type: Mapped[str] = mapped_column(String(80), default="")
    entity_id: Mapped[str] = mapped_column(String(80), default="")
    ip_address: Mapped[str] = mapped_column(String(80), default="")
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[Optional[User]] = relationship(foreign_keys=[user_id])

class ProductSuggestionVote(Base):
    __tablename__ = "product_suggestion_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    product_key: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[int, list[WebSocket]] = defaultdict(list)

    async def connect(self, auction_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections[auction_id].append(websocket)

    def disconnect(self, auction_id: int, websocket: WebSocket) -> None:
        if auction_id in self.connections and websocket in self.connections[auction_id]:
            self.connections[auction_id].remove(websocket)

    async def broadcast(self, auction_id: int, payload: dict) -> None:
        dead = []
        for ws in self.connections.get(auction_id, []):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(auction_id, ws)


app = FastAPI(title=APP_NAME)
app.add_middleware(GZipMiddleware, minimum_size=1000)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
manager = ConnectionManager()
AUCTION_BID_LOCKS: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


@app.middleware("http")
async def add_static_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=604800, stale-while-revalidate=86400")
    return response
SESSIONS: dict[str, int] = {}
BANNED_WORDS = {
    "idiota", "burro", "otario", "otário", "droga", "merda", "porra", "fdp", "puta",
    "imbecil", "lixo", "desgraça", "arrombado", "vagabundo"
}

# Curva estratégica dos lances.
# Normal: 0,10 a 0,50 adicionam tempo; 0,60 a 1,00 removem tempo.
# Turbo 2.0+: cada lance define uma janela curta de disputa.
ALLOWED_BIDS = {0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00}

NORMAL_TIME_EFFECT_SECONDS = {
    0.10: 15,
    0.20: 20,
    0.30: 25,
    0.40: 30,
    0.50: 35,
    0.60: -5,
    0.70: -10,
    0.80: -15,
    0.90: -20,
    1.00: -25,
}

NORMAL_BID_BUTTON_COOLDOWN_SECONDS = {
    0.10: 10,
    0.20: 20,
    0.30: 20,
    0.40: 20,
    0.50: 20,
    0.60: 30,
    0.70: 30,
    0.80: 30,
    0.90: 30,
    1.00: 30,
}

TURBO_2_SECONDS = {
    1.00: 8,
    0.90: 10,
    0.80: 12,
    0.70: 14,
    0.60: 16,
    0.50: 18,
    0.40: 20,
    0.30: 22,
    0.20: 24,
    0.10: 26,
}

TURBO_3_SECONDS = {
    1.00: 4,
    0.90: 5,
    0.80: 6,
    0.70: 7,
    0.60: 8,
    0.50: 9,
    0.40: 10,
    0.30: 11,
    0.20: 12,
    0.10: 13,
}

TURBO_4_SECONDS = {
    1.00: 2,
    0.90: 3,
    0.80: 4,
    0.70: 5,
    0.60: 6,
    0.50: 7,
    0.40: 8,
    0.30: 9,
    0.20: 10,
    0.10: 11,
}

# Cooldown visual e operacional do botão após cada lance.
# Regra solicitada: normal 20s, Turbo 2.0 30s, Turbo 4.0 40s.
# Turbo 3.0 fica no meio para manter progressão natural sem alterar a lógica do relógio do leilão.
BID_COOLDOWN_NORMAL_SECONDS = 20
TURBO_BUTTON_COOLDOWN_SECONDS = {
    2: 30,
    3: 35,
    4: 40,
}
TURBO_GLOBAL_COOLDOWN_SECONDS = TURBO_BUTTON_COOLDOWN_SECONDS
TURBO_AUCTION_COOLDOWN_UNTIL: dict[int, datetime] = {}

CHAT_PRE_START_SECONDS = 5 * 60

# Compatibilidade com código antigo que ainda consulte este nome.
BID_BUTTON_COOLDOWN_SECONDS = NORMAL_BID_BUTTON_COOLDOWN_SECONDS
MAX_INITIAL_DURATION_SECONDS = 60 * 60
DEFAULT_INITIAL_DURATION_SECONDS = 30 * 60
PAYMENT_DEADLINE_MINUTES = 10
ENABLE_CASHBACK_DRAW = False
DEFAULT_BID_FEE_PERCENT = 10.0
PLATFORM_PROFIT_PERCENT = 10.0


SUGGESTION_PRODUCTS = [
    {"key": "fone_bluetooth", "name": "Fone Bluetooth", "category": "Áudio", "price_level": "Baixo"},
    {"key": "mouse_gamer", "name": "Mouse Gamer", "category": "Informática", "price_level": "Baixo"},
    {"key": "teclado_mecanico", "name": "Teclado Mecânico", "category": "Informática", "price_level": "Baixo/Médio"},
    {"key": "caixa_som_bluetooth", "name": "Caixa de Som Bluetooth", "category": "Áudio", "price_level": "Baixo/Médio"},
    {"key": "carregador_portatil", "name": "Carregador Portátil", "category": "Acessórios", "price_level": "Baixo/Médio"},
    {"key": "smartwatch", "name": "Smartwatch", "category": "Wearable", "price_level": "Médio"},
    {"key": "echo_dot_alexa", "name": "Echo Dot / Alexa", "category": "Casa inteligente", "price_level": "Médio"},
    {"key": "controle_gamer", "name": "Controle Gamer", "category": "Games", "price_level": "Médio"},
    {"key": "kindle", "name": "Kindle", "category": "Leitura", "price_level": "Médio"},
    {"key": "tablet", "name": "Tablet", "category": "Eletrônicos", "price_level": "Médio"},
    {"key": "monitor_24", "name": "Monitor 24 polegadas", "category": "Informática", "price_level": "Médio"},
    {"key": "nintendo_switch", "name": "Nintendo Switch", "category": "Games", "price_level": "Médio/Alto"},
    {"key": "playstation_4", "name": "PlayStation 4", "category": "Games", "price_level": "Médio/Alto"},
    {"key": "xbox_series_s", "name": "Xbox Series S", "category": "Games", "price_level": "Médio/Alto"},
    {"key": "celular_android", "name": "Celular Android", "category": "Smartphone", "price_level": "Médio/Alto"},
    {"key": "iphone", "name": "iPhone", "category": "Smartphone", "price_level": "Alto"},
    {"key": "notebook", "name": "Notebook", "category": "Informática", "price_level": "Alto"},
    {"key": "tv_43", "name": "TV 43 polegadas", "category": "TV", "price_level": "Alto"},
    {"key": "tv_50", "name": "TV 50 polegadas", "category": "TV", "price_level": "Alto"},
    {"key": "soundbar", "name": "Soundbar", "category": "Áudio", "price_level": "Alto"},
]



def BR(v: float) -> float:
    return float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def fmt_money(v: float) -> str:
    return f"{BR(v):.2f}".replace(".", ",")


STATIC_FALLBACK_IMAGE = "/static/lanceio_hero_slide_01.png"


def safe_image_url(value: str) -> str:
    """Evita 404 em imagens de produto removidas após redeploy/cópia local."""
    url = (value or "").strip()
    if not url:
        return STATIC_FALLBACK_IMAGE
    if url.startswith("/static/uploads/"):
        local_path = BASE_DIR / url.lstrip("/")
        if not local_path.exists():
            return STATIC_FALLBACK_IMAGE
    return url


def public_user_name(user: Optional["User"]) -> str:
    if not user:
        return "—"
    nickname = (getattr(user, "public_name", "") or "").strip()
    if nickname:
        return f"@{nickname}"
    first = (getattr(user, "full_name", "") or "Participante").strip().split()[0]
    return first or "Participante"


def normalize_public_name(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9._-]", "", value)
    return value[:24]


def user_is_verified(user: Optional["User"]) -> bool:
    return bool(user and getattr(user, "identity_status", "pending") == "verified")


def user_has_identity_files(user: Optional["User"]) -> bool:
    return bool(user and (getattr(user, "document_file_url", "") or "").strip() and (getattr(user, "selfie_file_url", "") or "").strip())


def account_status_label(user: Optional["User"]) -> str:
    if user_is_verified(user):
        return "Conta confirmada"
    if user_has_identity_files(user):
        return "Documentos em análise"
    return "Documentos pendentes"




def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:80]
    return (request.client.host if request.client else "")[:80]


def audit_event(db: Session, request: Request, action: str, user: Optional[User] = None, entity_type: str = "", entity_id: str | int = "", details: str = "") -> None:
    try:
        db.add(AuditLog(
            user_id=getattr(user, "id", None),
            action=action[:80],
            entity_type=entity_type[:80],
            entity_id=str(entity_id)[:80],
            ip_address=client_ip(request),
            details=(details or "")[:2000],
        ))
    except Exception:
        pass


def only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def validate_cpf_digits(value: str) -> bool:
    digits = only_digits(value)
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    total = sum(int(digits[i]) * (10 - i) for i in range(9))
    check1 = (total * 10) % 11
    check1 = 0 if check1 == 10 else check1
    total = sum(int(digits[i]) * (11 - i) for i in range(10))
    check2 = (total * 10) % 11
    check2 = 0 if check2 == 10 else check2
    return check1 == int(digits[9]) and check2 == int(digits[10])


def validate_phone_digits(value: str) -> bool:
    digits = only_digits(value)
    return len(digits) in {10, 11} and len(set(digits)) > 1


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def hash_password(raw_password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", (raw_password or "").encode("utf-8"), salt, 120_000)
    return "pbkdf2_sha256$120000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(raw_password: str, stored_password: str) -> bool:
    stored_password = stored_password or ""
    if stored_password.startswith("pbkdf2_sha256$"):
        try:
            _, rounds, salt_b64, digest_b64 = stored_password.split("$", 3)
            salt = base64.b64decode(salt_b64.encode())
            expected = base64.b64decode(digest_b64.encode())
            actual = hashlib.pbkdf2_hmac("sha256", (raw_password or "").encode("utf-8"), salt, int(rounds))
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False
    # Compatibilidade com contas antigas em texto puro. Ao logar, o código atualiza para hash.
    return hmac.compare_digest(stored_password, raw_password or "")


def make_email_verification_token() -> str:
    return secrets.token_urlsafe(40)


def public_base_url(request: Optional[Request] = None) -> str:
    env_url = (os.getenv("PUBLIC_BASE_URL") or os.getenv("SERVER_URL") or "").strip().rstrip("/")
    if env_url:
        return env_url
    if request:
        return str(request.base_url).rstrip("/")
    return ""


def send_verification_email(user: User, request: Optional[Request] = None) -> bool:
    if not getattr(user, "email_verification_token", ""):
        return False
    base_url = public_base_url(request)
    link = f"{base_url}/confirmar-email?token={user.email_verification_token}"
    subject = "Confirme seu e-mail no Lancei o Certo"
    body = (
        f"Olá, {user.full_name}.\n\n"
        "Para ativar seu acesso ao Lancei o Certo, confirme seu e-mail pelo link abaixo:\n"
        f"{link}\n\n"
        "Este link expira em 24 horas. Se você não criou essa conta, ignore esta mensagem.\n"
    )
    smtp_host = (os.getenv("SMTP_HOST") or "").strip()
    smtp_port = int(os.getenv("SMTP_PORT") or "587")
    smtp_user = (os.getenv("SMTP_USER") or "").strip()
    smtp_password = (os.getenv("SMTP_PASSWORD") or "").strip()
    smtp_from = (os.getenv("SMTP_FROM") or smtp_user or "no-reply@lanceiocerto.com.br").strip()
    if not smtp_host or not smtp_from:
        print(f"[EMAIL VERIFICATION DEV] {user.email}: {link}")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = user.email
    msg.set_content(body)
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            if (os.getenv("SMTP_TLS") or "1").strip() != "0":
                server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
        return True
    except Exception as exc:
        print(f"[EMAIL VERIFICATION ERROR] {user.email}: {exc} | link={link}")
        return False

def fmt_deadline(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    return dt.strftime("%d/%m/%Y %H:%M")


def remaining_label(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    seconds = int((dt - datetime.utcnow()).total_seconds())
    if seconds <= 0:
        return "Expirado"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h {minutes}min"
    if hours > 0:
        return f"{hours}h {minutes}min"
    return f"{minutes}min"


def current_user(request: Request, db: Session) -> Optional[User]:
    token = request.cookies.get("session_token")
    if not token:
        return None
    user_id = SESSIONS.get(token)
    if not user_id:
        return None
    return db.get(User, user_id)


def require_user(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Faça login para continuar.")
    if user.is_banned:
        raise HTTPException(status_code=403, detail="Conta bloqueada.")
    return user


def require_admin(request: Request, db: Session) -> User:
    user = require_user(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    return user


def auction_collected_total(item: AuctionItem) -> float:
    """Total usado para acionar o turbo: preço atual + taxas acumuladas."""
    return BR((getattr(item, "current_price", 0.0) or 0.0) + (getattr(item, "total_bid_fees", 0.0) or 0.0))


def auction_total_if_paid(item: AuctionItem) -> float:
    current_price = float(getattr(item, "current_price", 0.0) or 0.0)
    fees = float(getattr(item, "total_bid_fees", 0.0) or 0.0)
    return BR(current_price + fees + current_price)


def auction_cash_reserved_before_payment(item: AuctionItem) -> float:
    return auction_collected_total(item)


def auction_expected_profit_if_paid(item: AuctionItem) -> float:
    return BR(auction_total_if_paid(item) - (getattr(item, "source_price", 0.0) or 0.0))


def turbo_base_amount(item: AuctionItem) -> float:
    base_value = float(getattr(item, "turbo_base_value", 0.0) or 0.0)
    if base_value > 0:
        return BR(base_value)
    source_price = float(getattr(item, "source_price", 0.0) or 0.0)
    min_pct = float(getattr(item, "winner_min_percent", 50.0) or 50.0)
    target_pct = float(getattr(item, "target_profit_percent", PLATFORM_PROFIT_PERCENT) or PLATFORM_PROFIT_PERCENT)
    return BR(source_price * ((min_pct + target_pct) / 100.0))


def turbo_trigger_amount(item: AuctionItem) -> float:
    return BR((getattr(item, "source_price", 0.0) or 0.0) * ((getattr(item, "turbo_trigger_percent", 60.0) or 60.0) / 100.0))

def auction_progress_percent(item: AuctionItem) -> float:
    if item.source_price <= 0:
        return 0.0
    return round((auction_collected_total(item) / item.source_price) * 100, 2)

def calculate_turbo_trigger_percent(winner_min_percent: float = 50.0, target_profit_percent: float = 10.0) -> float:
    """Regra simples: preço mínimo desejado + meta de lucro/taxa.
    Ex.: 50% + 10% = Turbo 2.0 em 60% do preço original.
    """
    try:
        winner_min = float(winner_min_percent)
    except Exception:
        winner_min = 50.0
    try:
        target = float(target_profit_percent)
    except Exception:
        target = 10.0
    return max(1.0, min(95.0, winner_min + target))


def compute_turbo_level(item: AuctionItem) -> int:
    if not getattr(item, "turbo_enabled", True):
        return 0
    progress = auction_progress_percent(item)
    if progress >= getattr(item, "turbo_level_4_percent", 70.0):
        return 4
    if progress >= getattr(item, "turbo_level_3_percent", 65.0):
        return 3
    if progress >= getattr(item, "turbo_trigger_percent", 60.0):
        return 2
    return 0


def turbo_label(level: int) -> str:
    return {0: "Modo Normal", 2: "Turbo 2.0", 3: "Turbo 3.0", 4: "Turbo 4.0"}.get(level, "Modo Normal")


def normal_force_multiplier(item: AuctionItem) -> float:
    progress = auction_progress_percent(item)
    trigger = max(1.0, getattr(item, "turbo_trigger_percent", 60.0))
    ratio_to_turbo = max(0.0, min(1.0, progress / trigger))
    return 0.30 + (0.70 * ratio_to_turbo)


def normal_time_delta_seconds(item: AuctionItem, bid_value: float) -> int:
    """Modo normal: botões até R$ 0,50 aumentam tempo; R$ 0,60 até R$ 1,00 reduzem tempo."""
    return int(NORMAL_TIME_EFFECT_SECONDS[bid_value])


def turbo_bid_seconds(bid_value: float, turbo_level: int) -> float:
    """No turbo, cada lance redefine o relógio para uma janela curta de disputa."""
    if turbo_level == 4:
        return float(TURBO_4_SECONDS[bid_value])
    if turbo_level == 3:
        return float(TURBO_3_SECONDS[bid_value])
    return float(TURBO_2_SECONDS[bid_value])


def bid_button_cooldown_seconds(bid_value: float, turbo_level: int = 0) -> int:
    """Cooldown por botão, aplicado no backend e enviado ao frontend.

    Antes o turbo retornava 0, então o botão não travava e o contador visual
    não aparecia. Isso permitia cliques repetidos e deixava o leilão parecer
    bugado/lento quando o servidor recusava ou processava lances em sequência.
    """
    level = int(turbo_level or 0)
    if level >= 4:
        return TURBO_BUTTON_COOLDOWN_SECONDS[4]
    if level >= 3:
        return TURBO_BUTTON_COOLDOWN_SECONDS[3]
    if level >= 2:
        return TURBO_BUTTON_COOLDOWN_SECONDS[2]
    return BID_COOLDOWN_NORMAL_SECONDS


def turbo_activation_cooldown_seconds(turbo_level: int) -> int:
    return int(TURBO_GLOBAL_COOLDOWN_SECONDS.get(int(turbo_level or 0), 0))


def auction_chat_is_open(item: AuctionItem, now: Optional[datetime] = None) -> bool:
    if not item or getattr(item, "chat_paused", False):
        return False
    now = now or datetime.utcnow()
    if item.status == "live":
        return True
    if item.status in {"scheduled", "relisted"} and item.scheduled_start:
        return 0 <= (item.scheduled_start - now).total_seconds() <= CHAT_PRE_START_SECONDS
    return False



def reset_relisted_public_history(db: Session, item: AuctionItem) -> None:
    """Relançamento deve começar limpo para o público: sem lances, último lance ou chat antigo."""
    if not item:
        return
    db.query(Bid).filter(Bid.auction_id == item.id).delete(synchronize_session=False)
    db.query(ChatMessage).filter(ChatMessage.auction_id == item.id).delete(synchronize_session=False)
    item.current_price = 0.0
    item.start_price = 0.0
    item.total_bid_fees = 0.0
    item.total_bid_spent = 0.0
    item.turbo_level = 0
    item.winner_user_id = None
    item.winner_deadline = None
    item.ends_at = None

def start_auction_if_due(item: AuctionItem, now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    if item and item.status in {"scheduled", "relisted"} and item.scheduled_start and item.scheduled_start <= now:
        item.status = "live"
        duration = getattr(item, "initial_duration_seconds", DEFAULT_INITIAL_DURATION_SECONDS) or DEFAULT_INITIAL_DURATION_SECONDS
        item.ends_at = now + timedelta(seconds=min(MAX_INITIAL_DURATION_SECONDS, duration))
        item.chat_paused = False
        return True
    return False


def clamp_initial_duration(minutes: int | float | None) -> int:
    try:
        minutes_int = int(minutes or 30)
    except Exception:
        minutes_int = 30
    minutes_int = max(1, min(60, minutes_int))
    return minutes_int * 60


def public_auction_payload(item: AuctionItem, db: Session) -> dict:
    # Em produtos agendados/relançados, a vitrine deve parecer uma nova disputa.
    # Lances antigos ficam fora da visualização pública e o relançamento limpa o histórico.
    if item.status in {"scheduled", "relisted"}:
        bids_count = 0
        last_bid = None
        last_bidder = None
    else:
        bids_count = db.query(Bid).filter(Bid.auction_id == item.id).count()
        last_bid = db.query(Bid).filter(Bid.auction_id == item.id).order_by(desc(Bid.created_at)).first()
        last_bidder = public_user_name(last_bid.user) if last_bid else None
    remaining = 0
    if item.status == "live" and item.ends_at:
        remaining = max(0, int((item.ends_at - datetime.utcnow()).total_seconds()))
    start_remaining = 0
    if item.status in {"scheduled", "relisted"} and item.scheduled_start:
        start_remaining = max(0, int((item.scheduled_start - datetime.utcnow()).total_seconds()))
    return {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "status": item.status,
        "current_price": BR(item.current_price),
        "start_price": BR(item.start_price),
        "source_price": BR(item.source_price),
        "scheduled_start": item.scheduled_start.isoformat() if item.scheduled_start else None,
        "start_remaining": start_remaining,
        "ends_at": item.ends_at.isoformat() if item.ends_at else None,
        "remaining_seconds": remaining,
        "winner_name": public_user_name(item.winner) if item.winner else None,
        "winner_deadline": item.winner_deadline.isoformat() if item.winner_deadline else None,
        "turbo_level": item.turbo_level,
        "turbo_label": turbo_label(item.turbo_level),
        "progress_percent": auction_progress_percent(item),
        "collected_total": auction_collected_total(item),
        "turbo_trigger_percent": getattr(item, "turbo_trigger_percent", 60.0),
        "turbo_level_3_percent": getattr(item, "turbo_level_3_percent", 65.0),
        "turbo_level_4_percent": getattr(item, "turbo_level_4_percent", 70.0),
        "turbo_start_amount": BR(item.source_price * (getattr(item, "turbo_trigger_percent", 60.0) / 100.0)),
        "turbo_level_3_amount": BR(item.source_price * (getattr(item, "turbo_level_3_percent", 65.0) / 100.0)),
        "turbo_level_4_amount": BR(item.source_price * (getattr(item, "turbo_level_4_percent", 70.0) / 100.0)),
        "bid_fee_percent": getattr(item, "bid_fee_percent", DEFAULT_BID_FEE_PERCENT),
        "total_bid_fees": BR(getattr(item, "total_bid_fees", 0.0) or 0.0),
        "cash_reserved_before_payment": auction_cash_reserved_before_payment(item),
        "total_if_paid": auction_total_if_paid(item),
        "expected_profit_if_paid": auction_expected_profit_if_paid(item),
        "turbo_base_value": turbo_base_amount(item),
        "cashback_enabled": bool(getattr(item, "cashback_enabled", False)),
        "bid_fee_percent": getattr(item, "bid_fee_percent", DEFAULT_BID_FEE_PERCENT),
        "total_bid_fees": BR(getattr(item, "total_bid_fees", 0.0) or 0.0),
        "initial_duration_seconds": getattr(item, "initial_duration_seconds", DEFAULT_INITIAL_DURATION_SECONDS),
        "bids_count": bids_count,
        "last_bidder": last_bidder,
        "image_url": safe_image_url(item.image_url),
        "chat_paused": item.chat_paused,
        "chat_open": auction_chat_is_open(item),
        "cashback": cashback_payload(item, db),
    }


def public_auction_live_payload(item: AuctionItem, db: Session, *, include_cashback: bool = False) -> dict:
    """Payload leve para atualizações em tempo real do leilão.

    O endpoint de lance precisa ser rápido. O payload completo chama cashback e
    outras informações que não precisam ser recalculadas a cada clique. Este
    payload mantém todos os campos usados pelo JavaScript da tela do leilão,
    mas evita trabalho extra desnecessário.
    """
    if item.status in {"scheduled", "relisted"}:
        bids_count = 0
        last_bid = None
        last_bidder = None
    else:
        bids_count = db.query(func.count(Bid.id)).filter(Bid.auction_id == item.id).scalar() or 0
        last_bid = (
            db.query(Bid)
            .options(selectinload(Bid.user))
            .filter(Bid.auction_id == item.id)
            .order_by(desc(Bid.created_at))
            .first()
        )
        last_bidder = public_user_name(last_bid.user) if last_bid else None

    now = datetime.utcnow()
    remaining = 0
    if item.status == "live" and item.ends_at:
        remaining = max(0, int((item.ends_at - now).total_seconds()))
    start_remaining = 0
    if item.status in {"scheduled", "relisted"} and item.scheduled_start:
        start_remaining = max(0, int((item.scheduled_start - now).total_seconds()))

    level = int(getattr(item, "turbo_level", 0) or 0)
    payload = {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "status": item.status,
        "current_price": BR(item.current_price),
        "start_price": BR(item.start_price),
        "source_price": BR(item.source_price),
        "scheduled_start": item.scheduled_start.isoformat() if item.scheduled_start else None,
        "start_remaining": start_remaining,
        "ends_at": item.ends_at.isoformat() if item.ends_at else None,
        "remaining_seconds": remaining,
        "winner_name": public_user_name(item.winner) if item.winner else None,
        "winner_deadline": item.winner_deadline.isoformat() if item.winner_deadline else None,
        "turbo_level": level,
        "turbo_label": turbo_label(level),
        "progress_percent": auction_progress_percent(item),
        "collected_total": auction_collected_total(item),
        "turbo_trigger_percent": getattr(item, "turbo_trigger_percent", 60.0),
        "turbo_level_3_percent": getattr(item, "turbo_level_3_percent", 65.0),
        "turbo_level_4_percent": getattr(item, "turbo_level_4_percent", 70.0),
        "turbo_start_amount": BR(item.source_price * (getattr(item, "turbo_trigger_percent", 60.0) / 100.0)),
        "turbo_level_3_amount": BR(item.source_price * (getattr(item, "turbo_level_3_percent", 65.0) / 100.0)),
        "turbo_level_4_amount": BR(item.source_price * (getattr(item, "turbo_level_4_percent", 70.0) / 100.0)),
        "bid_fee_percent": getattr(item, "bid_fee_percent", DEFAULT_BID_FEE_PERCENT),
        "total_bid_fees": BR(getattr(item, "total_bid_fees", 0.0) or 0.0),
        "cash_reserved_before_payment": auction_cash_reserved_before_payment(item),
        "total_if_paid": auction_total_if_paid(item),
        "expected_profit_if_paid": auction_expected_profit_if_paid(item),
        "turbo_base_value": turbo_base_amount(item),
        "initial_duration_seconds": getattr(item, "initial_duration_seconds", DEFAULT_INITIAL_DURATION_SECONDS),
        "bids_count": bids_count,
        "last_bidder": last_bidder,
        "image_url": safe_image_url(item.image_url),
        "chat_paused": item.chat_paused,
        "chat_open": auction_chat_is_open(item),
        "button_cooldown": bid_button_cooldown_seconds(0.10, level),
    }
    if include_cashback:
        payload["cashback"] = cashback_payload(item, db)
    return payload


def public_auction_card_payload(item: AuctionItem) -> dict:
    """Payload leve para a vitrine/home.

    Mantém os campos usados pelos cards, mas evita consultas extras por leilão
    (lances, cashback, usuário vencedor etc.). A página completa do leilão
    continua usando public_auction_payload(), preservando a lógica original.
    """
    remaining = 0
    if item.status == "live" and item.ends_at:
        remaining = max(0, int((item.ends_at - datetime.utcnow()).total_seconds()))

    start_remaining = 0
    if item.status in {"scheduled", "relisted"} and item.scheduled_start:
        start_remaining = max(0, int((item.scheduled_start - datetime.utcnow()).total_seconds()))

    return {
        "id": item.id,
        "title": item.title,
        "status": item.status,
        "current_price": BR(item.current_price),
        "source_price": BR(item.source_price),
        "scheduled_start": item.scheduled_start.isoformat() if item.scheduled_start else None,
        "start_remaining": start_remaining,
        "remaining_seconds": remaining,
        "winner_name": public_user_name(item.winner) if item.winner else None,
        "image_url": safe_image_url(item.image_url),
    }



def user_stats(db: Session, user: User) -> dict:
    bids_total = db.query(Bid).filter(Bid.user_id == user.id).count()
    distinct_auctions = db.query(Bid.auction_id).filter(Bid.user_id == user.id).distinct().count()
    won = db.query(WinnerOrder).filter(WinnerOrder.user_id == user.id).count()
    pending = db.query(WinnerOrder).filter(WinnerOrder.user_id == user.id, WinnerOrder.status == "pending_payment").count()
    expired = db.query(WinnerOrder).filter(WinnerOrder.user_id == user.id, WinnerOrder.status == "expired").count()
    return {
        "bids_total": bids_total,
        "distinct_auctions": distinct_auctions,
        "won": won,
        "pending": pending,
        "expired": expired,
    }




def build_returned_items(db: Session) -> list[dict]:
    # Carrega os pedidos expirados com produto e usuário em lote.
    # Antes, order.auction/order.user podiam gerar consultas extras durante o loop.
    expired_orders = (
        db.query(WinnerOrder)
        .options(selectinload(WinnerOrder.auction), selectinload(WinnerOrder.user))
        .filter(WinnerOrder.status == "expired")
        .order_by(desc(WinnerOrder.expired_at), desc(WinnerOrder.created_at))
        .limit(80)
        .all()
    )
    returned = []
    seen = set()
    for order in expired_orders:
        item = order.auction
        if not item or item.id in seen:
            continue
        seen.add(item.id)
        source_price = BR(item.source_price or 0.0)
        final_price = BR(order.final_price or item.current_price or 0.0)
        fees_total = BR(getattr(item, "total_bid_fees", 0.0) or 0.0)
        reserved_cash = BR(final_price + fees_total)
        expected_total_if_paid = BR(final_price + fees_total + final_price)
        expected_profit_if_paid = BR(expected_total_if_paid - source_price)
        suggested_turbo_base = BR(max(1.0, source_price - reserved_cash))
        suggested_turbo_trigger_amount = BR(suggested_turbo_base / 2.0)
        returned.append({
            "id": item.id,
            "title": item.title,
            "description": item.description,
            "image_url": safe_image_url(item.image_url),
            "source_store": item.source_store,
            "source_url": item.source_url,
            "source_price": source_price,
            "last_final_price": final_price,
            "accumulated_fees": fees_total,
            "reserved_cash": reserved_cash,
            "expected_total_if_paid": expected_total_if_paid,
            "expected_profit_if_paid": expected_profit_if_paid,
            "suggested_turbo_base": suggested_turbo_base,
            "suggested_turbo_trigger_amount": suggested_turbo_trigger_amount,
            "winner_name": public_user_name(order.user) if order.user else "—",
            "expired_at": order.expired_at,
        })
    return returned


def _sum_scalar(db: Session, expr, *filters) -> float:
    query = db.query(func.coalesce(func.sum(expr), 0.0))
    if filters:
        query = query.filter(*filters)
    return BR(query.scalar() or 0.0)


def build_finance_dashboard(db: Session) -> dict:
    # Agregações direto no banco: evita carregar todos os produtos, usuários e pedidos em memória.
    paid_statuses = ["paid", "processing", "purchased", "sent", "delivered"]
    outgoing_statuses = ["paid", "processing", "purchased"]

    total_fees = _sum_scalar(db, AuctionItem.total_bid_fees)
    total_bid_spent = _sum_scalar(db, AuctionItem.total_bid_spent)
    total_payments = _sum_scalar(db, WinnerOrder.final_price, WinnerOrder.status.in_(paid_statuses))
    user_wallet_total = _sum_scalar(db, User.wallet_balance)
    pending_withdrawals = _sum_scalar(db, WithdrawalRequest.amount, WithdrawalRequest.status == "pending")

    expected_products = BR(
        db.query(func.coalesce(func.sum(AuctionItem.source_price), 0.0))
        .join(WinnerOrder, WinnerOrder.auction_id == AuctionItem.id)
        .filter(WinnerOrder.status.in_(outgoing_statuses))
        .scalar()
        or 0.0
    )
    expected_outgoing = BR(expected_products + pending_withdrawals)
    total_income = BR(total_bid_spent + total_fees + total_payments)
    net_result = BR(total_income - expected_outgoing)
    return {
        "total_fees": total_fees,
        "total_bid_spent": total_bid_spent,
        "total_payments": total_payments,
        "user_wallet_total": user_wallet_total,
        "expected_outgoing": expected_outgoing,
        "total_income": total_income,
        "total_outgoing": expected_outgoing,
        "net_result": net_result,
        "estimated_profit": net_result,
        "available_cash": BR(total_income - user_wallet_total),
        "accumulated_loss": BR(abs(net_result) if net_result < 0 else 0),
        "pending_withdrawals": pending_withdrawals,
    }


def build_cashflow_movements(db: Session) -> list[dict]:
    rows = []
    transactions = db.query(WalletTransaction).order_by(desc(WalletTransaction.created_at)).limit(120).all()
    user_ids = {tx.user_id for tx in transactions if tx.user_id}
    users_by_id = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    for tx in transactions:
        tx_user = users_by_id.get(tx.user_id)
        rows.append({
            "created_at": tx.created_at,
            "type": tx.kind,
            "description": f"Usuário #{tx.user_id} • CPF {getattr(tx_user, 'cpf', '—') or '—'} • {tx.note or 'Movimentação'}",
            "amount": BR(tx.amount or 0.0),
            "balance_after": 0.0,
            "status": "registrado",
        })
    return rows


def build_auction_results(db: Session) -> list[dict]:
    rows = []
    for item in db.query(AuctionItem).order_by(desc(AuctionItem.created_at)).limit(120).all():
        source_price = BR(item.source_price or 0.0)
        final_price = BR(item.current_price or 0.0)
        fees_total = BR(item.total_bid_fees or 0.0)
        total_if_paid = BR(final_price + fees_total + final_price)
        result = BR(total_if_paid - source_price)
        rows.append({
            "title": item.title,
            "source_price": source_price,
            "final_price": final_price,
            "fees_total": fees_total,
            "site_complement": BR(max(0.0, source_price - (final_price + fees_total))),
            "result": result,
            "status_label": item.status,
        })
    return rows



def admin_order_finance(order: WinnerOrder) -> dict:
    item = order.auction
    if not item:
        return {}
    final_price = BR(order.final_price or item.current_price or 0.0)
    fees = BR(getattr(item, "total_bid_fees", 0.0) or 0.0)
    bid_pool = BR(getattr(item, "current_price", 0.0) or 0.0)
    source_price = BR(getattr(item, "source_price", 0.0) or 0.0)
    total_if_paid = BR(bid_pool + fees + final_price)
    reserved_before_payment = BR(bid_pool + fees)
    result_if_paid = BR(total_if_paid - source_price)
    site_complement_if_paid = BR(max(0.0, source_price - total_if_paid))
    return {
        "final_price": final_price,
        "bid_pool": bid_pool,
        "fees": fees,
        "reserved_before_payment": reserved_before_payment,
        "total_if_paid": total_if_paid,
        "result_if_paid": result_if_paid,
        "site_complement_if_paid": site_complement_if_paid,
    }


def order_direct_messages(db: Session, order_id: int) -> list[AdminDirectMessage]:
    return db.query(AdminDirectMessage).filter(AdminDirectMessage.order_id == order_id).order_by(desc(AdminDirectMessage.created_at)).all()


def build_admin_order_cards(db: Session, orders: list[WinnerOrder]) -> list[dict]:
    """Monta cards do admin sem fazer uma consulta de mensagens por pedido."""
    order_ids = [o.id for o in orders]
    messages_by_order: dict[int, list[AdminDirectMessage]] = defaultdict(list)
    if order_ids:
        messages = (
            db.query(AdminDirectMessage)
            .filter(AdminDirectMessage.order_id.in_(order_ids))
            .order_by(desc(AdminDirectMessage.created_at))
            .limit(300)
            .all()
        )
        for msg in messages:
            if len(messages_by_order[msg.order_id]) < 5:
                messages_by_order[msg.order_id].append(msg)
    return [{"order": o, "finance": admin_order_finance(o), "messages": messages_by_order.get(o.id, [])} for o in orders]


def build_order_card(order: WinnerOrder) -> dict:
    return {
        "id": order.id,
        "auction_id": order.auction_id,
        "status": order.status,
        "auction_title": order.auction.title,
        "image_url": order.auction.image_url,
        "final_price": BR(order.final_price),
        "deadline_label": fmt_deadline(order.payment_deadline),
        "remaining_label": remaining_label(order.payment_deadline),
        "payment_link": order.payment_link,
        "tracking_code": order.tracking_code,
        "admin_note": order.admin_note,
        "source_store": order.auction.source_store,
        "source_url": order.auction.source_url,
        "created_at": order.created_at.strftime("%d/%m/%Y %H:%M"),
    }



def cashback_payload(item: AuctionItem, db: Session, user: Optional[User] = None) -> dict:
    if not getattr(item, "cashback_enabled", False):
        return {"available": False}
    event = db.query(CashbackEvent).filter(CashbackEvent.auction_id == item.id).first()
    if not event:
        return {"available": False}
    joined = False
    user_spent = 0.0
    if user:
        joined = db.query(CashbackEntry).filter(CashbackEntry.event_id == event.id, CashbackEntry.user_id == user.id).first() is not None
        user_spent = sum((b.bid_value or 0.0) for b in db.query(Bid).filter(Bid.auction_id == item.id, Bid.user_id == user.id).all())
    remaining = max(0, int((event.join_deadline - datetime.utcnow()).total_seconds())) if event.join_deadline else 0
    return {
        "available": True,
        "status": event.status,
        "join_remaining_seconds": remaining,
        "joined": joined,
        "user_spent": BR(user_spent),
        "winner_name": public_user_name(event.winner) if event.winner else None,
        "cashback_amount": BR(event.cashback_amount),
    }

def ensure_cashback_event(item: AuctionItem, db: Session, now: datetime) -> None:
    existing = db.query(CashbackEvent).filter(CashbackEvent.auction_id == item.id).first()
    if existing:
        return
    db.add(CashbackEvent(auction_id=item.id, status="open", join_deadline=now + timedelta(minutes=5)))


def draw_cashback_if_due(event: CashbackEvent, db: Session, now: datetime) -> None:
    if event.status != "open" or event.join_deadline > now:
        return
    entries = db.query(CashbackEntry).filter(CashbackEntry.event_id == event.id).all()
    if not entries:
        event.status = "closed_no_entries"
        event.drawn_at = now
        return
    winner_entry = entries[secrets.randbelow(len(entries))]
    event.status = "drawn"
    event.winner_user_id = winner_entry.user_id
    event.cashback_amount = BR(winner_entry.amount_spent)
    event.drawn_at = now
    winner = db.get(User, winner_entry.user_id)
    if winner and event.cashback_amount > 0:
        winner.wallet_balance = BR(winner.wallet_balance + event.cashback_amount)
        db.add(WalletTransaction(
            user_id=winner.id,
            amount=event.cashback_amount,
            kind="cashback",
            note=f"Cashback sorteado no leilão #{event.auction_id}",
        ))


def today_start_utc() -> datetime:
    return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


def suggestion_product_by_key(product_key: str) -> Optional[dict]:
    for product in SUGGESTION_PRODUCTS:
        if product["key"] == product_key:
            return product
    return None


def user_today_suggestion_vote(db: Session, user: Optional[User]) -> Optional[ProductSuggestionVote]:
    if not user:
        return None
    return (
        db.query(ProductSuggestionVote)
        .filter(ProductSuggestionVote.user_id == user.id, ProductSuggestionVote.created_at >= today_start_utc())
        .order_by(desc(ProductSuggestionVote.created_at))
        .first()
    )


def suggestion_vote_stats(db: Session) -> list[dict]:
    """Estatísticas de indicação em uma única consulta agregada.

    Antes havia um count() para o total e mais um count() por produto.
    Com muitos acessos simultâneos, isso aumenta o tempo de resposta da home/admin.
    """
    grouped = (
        db.query(ProductSuggestionVote.product_key, func.count(ProductSuggestionVote.id))
        .group_by(ProductSuggestionVote.product_key)
        .all()
    )
    votes_by_key = {key: int(total or 0) for key, total in grouped}
    total_votes = sum(votes_by_key.values())
    rows = []
    for product in SUGGESTION_PRODUCTS:
        votes = votes_by_key.get(product["key"], 0)
        percent = round((votes / total_votes) * 100, 2) if total_votes else 0.0
        rows.append({**product, "votes": votes, "percent": percent})
    rows.sort(key=lambda item: item["votes"], reverse=True)
    return rows


def ensure_columns() -> None:
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    with engine.begin() as conn:
        if inspector.has_table("users"):
            cols = {c["name"] for c in inspector.get_columns("users")}
            for name, ddl in {
                "public_name": "VARCHAR(40) DEFAULT ''",
                "nickname": "VARCHAR(40) DEFAULT ''",
                "identity_status": "VARCHAR(30) DEFAULT 'pending'",
                "identity_note": "TEXT DEFAULT ''",
                "document_type": "VARCHAR(40) DEFAULT 'CPF'",
                "document_number": "VARCHAR(40) DEFAULT ''",
                "document_file_url": "VARCHAR(600) DEFAULT ''",
                "selfie_file_url": "VARCHAR(600) DEFAULT ''",
                "verified_at": "TIMESTAMP NULL",
                "terms_accepted_at": "TIMESTAMP NULL",
                "privacy_accepted_at": "TIMESTAMP NULL",
                "email_verified": "BOOLEAN DEFAULT FALSE",
                "email_verified_at": "TIMESTAMP NULL",
                "email_verification_token": "VARCHAR(120) DEFAULT ''",
                "email_verification_expires_at": "TIMESTAMP NULL",
            }.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))
            # Garante que bancos antigos com nickname obrigatório não quebrem o cadastro.
            if "nickname" in cols:
                conn.execute(text("UPDATE users SET nickname = COALESCE(NULLIF(nickname, ''), public_name, email, 'usuario') WHERE nickname IS NULL OR nickname = ''"))

        if inspector.has_table("auction_items"):
            cols = {c["name"] for c in inspector.get_columns("auction_items")}
            for name, ddl in {
                "source_url": "VARCHAR(600) DEFAULT ''",
                "chat_paused": "BOOLEAN DEFAULT 0",
                "initial_duration_seconds": "INTEGER DEFAULT 3600",
                "turbo_enabled": "BOOLEAN DEFAULT 1",
                "turbo_trigger_percent": "FLOAT DEFAULT 60",
                "turbo_level_3_percent": "FLOAT DEFAULT 65",
                "turbo_level_4_percent": "FLOAT DEFAULT 70",
                "bid_fee_percent": "FLOAT DEFAULT 10",
                "winner_min_percent": "FLOAT DEFAULT 50",
                "target_profit_percent": "FLOAT DEFAULT 10",
                "turbo_base_value": "FLOAT DEFAULT 0",
                "cashback_enabled": "BOOLEAN DEFAULT 0",
            }.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE auction_items ADD COLUMN {name} {ddl}"))

        if inspector.has_table("winner_orders"):
            cols = {c["name"] for c in inspector.get_columns("winner_orders")}
            for name, ddl in {
                "purchase_link": "VARCHAR(600) DEFAULT ''",
                "purchase_status": "VARCHAR(40) DEFAULT ''",
                "purchased_at": "TIMESTAMP NULL",
                "sent_at": "TIMESTAMP NULL",
                "delivered_at": "TIMESTAMP NULL",
            }.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE winner_orders ADD COLUMN {name} {ddl}"))


        # Índices leves para as consultas mais repetidas da home, conta, admin e leilão.
        # CREATE INDEX IF NOT EXISTS funciona em SQLite e PostgreSQL.
        for ddl in [
            "CREATE INDEX IF NOT EXISTS ix_auction_items_status_created ON auction_items (status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_auction_items_status_start ON auction_items (status, scheduled_start)",
            "CREATE INDEX IF NOT EXISTS ix_bids_auction_created ON bids (auction_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_bids_user ON bids (user_id)",
            "CREATE INDEX IF NOT EXISTS ix_winner_orders_user_status_created ON winner_orders (user_id, status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_winner_orders_auction_created ON winner_orders (auction_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_wallet_transactions_user_created ON wallet_transactions (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_withdrawals_user_created ON withdrawal_requests (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_support_tickets_user_created ON support_tickets (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_audit_logs_created ON audit_logs (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_suggestion_votes_key ON product_suggestion_votes (product_key)",
            "CREATE INDEX IF NOT EXISTS ix_cashback_events_auction ON cashback_events (auction_id)",
        ]:
            try:
                conn.execute(text(ddl))
            except Exception:
                pass


def save_uploaded_image(file: Optional[UploadFile]) -> str:
    if not file or not file.filename:
        return ""
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename)
    final_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{safe_name}"
    target = UPLOAD_DIR / final_name
    with target.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return f"/static/uploads/{final_name}"

    with engine.begin() as conn:
        inspector = inspect(engine)
        if "admin_direct_messages" not in inspector.get_table_names():
            conn.execute(text("""
                CREATE TABLE admin_direct_messages (
                    id INTEGER PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    admin_id INTEGER NOT NULL,
                    message TEXT DEFAULT '',
                    is_open BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
        order_cols = {c["name"] for c in inspector.get_columns("winner_orders")}
        if "purchase_link" not in order_cols:
            conn.execute(text("ALTER TABLE winner_orders ADD COLUMN purchase_link VARCHAR(600) DEFAULT ''"))
        if "purchase_status" not in order_cols:
            conn.execute(text("ALTER TABLE winner_orders ADD COLUMN purchase_status VARCHAR(40) DEFAULT ''"))
        if "purchased_at" not in order_cols:
            conn.execute(text("ALTER TABLE winner_orders ADD COLUMN purchased_at DATETIME"))
        if "sent_at" not in order_cols:
            conn.execute(text("ALTER TABLE winner_orders ADD COLUMN sent_at DATETIME"))
        if "delivered_at" not in order_cols:
            conn.execute(text("ALTER TABLE winner_orders ADD COLUMN delivered_at DATETIME"))


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_columns()
    db = SessionLocal()
    try:
        admin_email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
        admin_password = (os.getenv("ADMIN_PASSWORD") or "").strip()
        admin_name = (os.getenv("ADMIN_NAME") or "Administrador Geral").strip()

        if admin_email:
            admin = db.query(User).filter(User.email == admin_email).first()
            if not admin:
                admin = User(
                    full_name=admin_name,
                    public_name=normalize_public_name(os.getenv("ADMIN_PUBLIC_NAME") or "admin"),
                    nickname=normalize_public_name(os.getenv("ADMIN_PUBLIC_NAME") or "admin"),
                    email=admin_email,
                    email_verified=True,
                    email_verified_at=datetime.utcnow(),
                    password=hash_password(admin_password or secrets.token_urlsafe(12)),
                    cpf="",
                    phone="",
                    is_admin=True,
                    is_superadmin=True,
                    identity_status="verified",
                    verified_at=datetime.utcnow(),
                    wallet_balance=0.0,
                )
                db.add(admin)
            else:
                admin.is_admin = True
                admin.is_superadmin = True
                admin.identity_status = "verified"
                if not admin.public_name:
                    admin.public_name = normalize_public_name(os.getenv("ADMIN_PUBLIC_NAME") or "admin")
                if not getattr(admin, "nickname", ""):
                    admin.nickname = admin.public_name
                if admin_password:
                    admin.password = hash_password(admin_password)
            default_admin = db.query(User).filter(User.email == "admin@lanceiocerto.local").first()
            if default_admin and default_admin.email != admin_email:
                default_admin.is_admin = False
                default_admin.is_superadmin = False
                default_admin.is_banned = True
                default_admin.password = secrets.token_urlsafe(24)
            db.flush()
        elif DATABASE_URL.startswith("sqlite"):
            admin = db.query(User).filter(User.email == "admin@lanceiocerto.local").first()
            if not admin:
                admin = User(
                    full_name="Administrador Principal",
                    public_name="admin",
                    nickname="admin",
                    email="admin@lanceiocerto.local",
                    email_verified=True,
                    email_verified_at=datetime.utcnow(),
                    password=hash_password("123456"),
                    cpf="000.000.000-00",
                    phone="(00) 00000-0000",
                    cep="14000-000",
                    street="Rua Principal",
                    number="100",
                    district="Centro",
                    city="Ribeirão Preto",
                    state="SP",
                    is_admin=True,
                    is_superadmin=True,
                    identity_status="verified",
                    wallet_balance=500.0,
                )
                db.add(admin)
                db.flush()

        if db.query(AuctionItem).count() == 0:
            now = datetime.utcnow()
            db.add_all(
                [
                    AuctionItem(
                        title="iPhone 14 128GB",
                        description="iPhone seminovo em ótimo estado, pronto para entrar em disputa.",
                        image_url="https://images.unsplash.com/photo-1678652197831-2d180705cd2c?q=80&w=1400&auto=format&fit=crop",
                        source_store="Mercado Livre",
                        source_url="https://www.mercadolivre.com.br/",
                        source_price=2000.0,
                        start_price=0.0,
                        current_price=0.0,
                        status="live",
                        scheduled_start=now,
                        ends_at=now + timedelta(seconds=DEFAULT_INITIAL_DURATION_SECONDS),
                        initial_duration_seconds=DEFAULT_INITIAL_DURATION_SECONDS,
                    ),
                    AuctionItem(
                        title="Bicicleta Aro 29",
                        description="Bicicleta urbana pronta para o próximo leilão.",
                        image_url="https://images.unsplash.com/photo-1541625602330-2277a4c46182?q=80&w=1400&auto=format&fit=crop",
                        source_store="Magazine Luiza",
                        source_url="https://www.magazineluiza.com.br/",
                        source_price=1200.0,
                        start_price=0.0,
                        current_price=0.0,
                        status="scheduled",
                        scheduled_start=now + timedelta(hours=2),
                    ),
                ]
            )
        db.commit()
    finally:
        db.close()


@app.on_event("startup")
async def startup_event():
    seed()
    asyncio.create_task(auction_watcher())


async def auction_watcher():
    while True:
        await asyncio.sleep(1)
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            changed_ids = []

            to_start = (
                db.query(AuctionItem)
                .filter(AuctionItem.status.in_(["scheduled", "relisted"]))
                .filter(AuctionItem.scheduled_start <= now)
                .all()
            )
            for item in to_start:
                if start_auction_if_due(item, now):
                    changed_ids.append(item.id)

            live_items = db.query(AuctionItem).filter(AuctionItem.status == "live").all()
            for item in live_items:
                if item.ends_at and item.ends_at <= now:
                    last_bid = db.query(Bid).filter(Bid.auction_id == item.id).order_by(desc(Bid.created_at)).first()
                    if last_bid:
                        item.status = "pending_payment"
                        item.winner_user_id = last_bid.user_id
                        item.winner_deadline = now + timedelta(minutes=PAYMENT_DEADLINE_MINUTES)
                        existing = db.query(WinnerOrder).filter(
                            WinnerOrder.auction_id == item.id,
                            WinnerOrder.status.in_(["pending_payment", "paid", "processing", "purchased", "sent", "delivered"])
                        ).first()
                        if not existing:
                            winner = db.get(User, last_bid.user_id)
                            order = WinnerOrder(
                                auction_id=item.id,
                                user_id=last_bid.user_id,
                                final_price=BR(item.current_price),
                                status="pending_payment",
                                payment_deadline=item.winner_deadline,
                                pix_code=f"PIX-LANCEIOCERTO-{item.id}-{last_bid.user_id}",
                                payment_link=f"/minha-conta/pagamentos/{item.id}",
                                delivery_name=winner.full_name,
                                delivery_cep=winner.cep,
                                delivery_street=winner.street,
                                delivery_number=winner.number,
                                delivery_district=winner.district,
                                delivery_city=winner.city,
                                delivery_state=winner.state,
                            )
                            db.add(order)
                        if ENABLE_CASHBACK_DRAW:
                            if getattr(item, 'cashback_enabled', False):
                                ensure_cashback_event(item, db, now)
                    else:
                        item.status = "ended"
                    changed_ids.append(item.id)

            open_cashbacks = db.query(CashbackEvent).filter(CashbackEvent.status == "open").all()
            for cashback in open_cashbacks:
                draw_cashback_if_due(cashback, db, now)

            pending_orders = db.query(WinnerOrder).filter(WinnerOrder.status == "pending_payment").all()
            for order in pending_orders:
                if order.payment_deadline and order.payment_deadline <= now:
                    order.status = "expired"
                    order.expired_at = now
                    item = db.get(AuctionItem, order.auction_id)
                    if item:
                        item.status = "ended"
                        item.winner_deadline = None
                        item.ends_at = None
                        changed_ids.append(item.id)

            db.commit()

            for auction_id in changed_ids:
                fresh = db.get(AuctionItem, auction_id)
                if fresh:
                    await manager.broadcast(auction_id, {"type": "auction_update", "auction": public_auction_live_payload(fresh, db)})
        finally:
            db.close()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    db = SessionLocal()
    try:
        user = current_user(request, db)
        # A vitrine não precisa carregar o histórico completo de todos os leilões.
        # Limitar os cards evita travamentos quando o banco cresce.
        live_items = db.query(AuctionItem).filter(AuctionItem.status == "live").order_by(AuctionItem.created_at.desc()).limit(12).all()
        upcoming_items = db.query(AuctionItem).filter(AuctionItem.status.in_(["scheduled", "relisted"])).order_by(AuctionItem.scheduled_start.asc()).limit(12).all()
        ended_items = db.query(AuctionItem).filter(AuctionItem.status.in_(["pending_payment", "ended"])).order_by(desc(AuctionItem.created_at)).limit(12).all()
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "user": user,
                "live_items": [public_auction_card_payload(x) for x in live_items],
                "upcoming_items": [public_auction_card_payload(x) for x in upcoming_items],
                "ended_items": [public_auction_card_payload(x) for x in ended_items],
                "suggestion_products": SUGGESTION_PRODUCTS,
                "suggestion_vote_stats": suggestion_vote_stats(db),
                "today_suggestion_vote": user_today_suggestion_vote(db, user),
                "fee_percent": "1%",
            },
        )
    finally:
        db.close()



@app.post("/indicacao/votar")
def vote_product_suggestion(request: Request, product_key: str = Form(...)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        product = suggestion_product_by_key(product_key)
        if not product:
            raise HTTPException(status_code=400, detail="Produto inválido para indicação.")

        already = user_today_suggestion_vote(db, user)
        if already:
            return RedirectResponse("/?indicacao=ja-votou", status_code=303)

        db.add(ProductSuggestionVote(user_id=user.id, product_key=product_key))
        db.commit()
        return RedirectResponse("/?indicacao=ok", status_code=303)
    finally:
        db.close()


@app.get("/termos-de-uso", response_class=HTMLResponse)
def terms_page(request: Request):
    db = SessionLocal()
    try:
        return templates.TemplateResponse("legal_terms.html", {"request": request, "user": current_user(request, db)})
    finally:
        db.close()


@app.get("/politica-de-privacidade", response_class=HTMLResponse)
def privacy_page(request: Request):
    db = SessionLocal()
    try:
        return templates.TemplateResponse("legal_privacy.html", {"request": request, "user": current_user(request, db)})
    finally:
        db.close()


@app.get("/regras-do-leilao", response_class=HTMLResponse)
def auction_rules_page(request: Request):
    db = SessionLocal()
    try:
        return templates.TemplateResponse("legal_rules.html", {"request": request, "user": current_user(request, db)})
    finally:
        db.close()

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@app.post("/register")
async def register(
    request: Request,
    full_name: str = Form(...),
    public_name: str = Form(...),
    email: str = Form(...),
    cpf: str = Form(...),
    phone: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    cep: str = Form(""),
    street: str = Form(""),
    number: str = Form(""),
    district: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    accept_terms: str = Form(""),
    accept_privacy: str = Form(""),
    accept_truth: str = Form(""),
):
    db = SessionLocal()
    try:
        clean_public_name = normalize_public_name(public_name)
        clean_email = normalize_email(email)
        clean_cpf = only_digits(cpf)
        clean_phone = only_digits(phone)

        def fail(message: str):
            return templates.TemplateResponse("register.html", {"request": request, "error": message}, status_code=400)

        if accept_terms != "on" or accept_privacy != "on" or accept_truth != "on":
            return fail("Para criar a conta, aceite os Termos de Uso, a Política de Privacidade e confirme que os dados são verdadeiros.")
        if len((full_name or "").strip().split()) < 2:
            return fail("Informe seu nome completo.")
        if len(clean_public_name) < 3:
            return fail("Escolha um apelido público com pelo menos 3 caracteres.")
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", clean_email):
            return fail("Informe um e-mail válido.")
        if not validate_cpf_digits(clean_cpf):
            return fail("Informe um CPF válido.")
        if not validate_phone_digits(clean_phone):
            return fail("Informe um telefone válido com DDD.")
        if len(password or "") < 8:
            return fail("A senha precisa ter pelo menos 8 caracteres.")
        if password != password_confirm:
            return fail("A confirmação de senha não confere.")

        if db.query(User).filter(User.email == clean_email).first():
            return fail("Este e-mail já está cadastrado.")
        if db.query(User).filter(User.cpf == clean_cpf).first():
            return fail("Este CPF já está cadastrado.")
        if db.query(User).filter(User.phone == clean_phone).first():
            return fail("Este telefone já está cadastrado.")
        if db.query(User).filter(User.public_name == clean_public_name).first():
            return fail("Este apelido público já está em uso.")

        token = make_email_verification_token()
        user = User(
            full_name=full_name.strip(),
            public_name=clean_public_name,
            nickname=clean_public_name,
            email=clean_email,
            email_verified=False,
            email_verification_token=token,
            email_verification_expires_at=datetime.utcnow() + timedelta(hours=24),
            password=hash_password(password.strip()),
            cpf=clean_cpf,
            phone=clean_phone,
            cep=only_digits(cep),
            street=street.strip(),
            number=number.strip(),
            district=district.strip(),
            city=city.strip(),
            state=state.strip().upper()[:2],
            document_type="CPF",
            document_number=clean_cpf,
            document_file_url="",
            selfie_file_url="",
            identity_status="pending",
            identity_note="Conta criada. Verificação de identidade ainda não enviada.",
            terms_accepted_at=datetime.utcnow(),
            privacy_accepted_at=datetime.utcnow(),
            wallet_balance=0.0,
        )
        db.add(user)
        db.flush()
        sent = send_verification_email(user, request)
        audit_event(db, request, "user.register", user, "user", user.id, "Cadastro criado. E-mail pendente de confirmação e KYC pendente.")
        db.commit()
        suffix = "&email_sent=1" if sent else "&email_dev=1"
        return RedirectResponse(f"/login?created=1&email_pending=1{suffix}", status_code=303)
    finally:
        db.close()


@app.get("/confirmar-email", response_class=HTMLResponse)
def confirm_email(request: Request, token: str = ""):
    db = SessionLocal()
    try:
        token = (token or "").strip()
        user = db.query(User).filter(User.email_verification_token == token).first() if token else None
        if not user:
            return templates.TemplateResponse("login.html", {"request": request, "error": "Link de confirmação inválido.", "created": 0, "email_pending": 0, "email_verified": 0}, status_code=400)
        if user.email_verification_expires_at and user.email_verification_expires_at < datetime.utcnow():
            user.email_verification_token = make_email_verification_token()
            user.email_verification_expires_at = datetime.utcnow() + timedelta(hours=24)
            send_verification_email(user, request)
            db.commit()
            return templates.TemplateResponse("login.html", {"request": request, "error": "O link expirou. Enviamos uma nova confirmação para seu e-mail.", "created": 0, "email_pending": 1, "email_verified": 0}, status_code=400)
        user.email_verified = True
        user.email_verified_at = datetime.utcnow()
        user.email_verification_token = ""
        user.email_verification_expires_at = None
        audit_event(db, request, "user.email_verified", user, "user", user.id, "E-mail confirmado pelo link de verificação.")
        db.commit()
        return RedirectResponse("/login?email_verified=1", status_code=303)
    finally:
        db.close()


@app.post("/reenviar-confirmacao")
def resend_email_confirmation(request: Request, login_identifier: str = Form("")):
    db = SessionLocal()
    try:
        value = (login_identifier or "").strip()
        digits = only_digits(value)
        query = db.query(User)
        user = query.filter(User.email == normalize_email(value)).first() if "@" in value else query.filter(User.cpf == digits).first()
        if user and not getattr(user, "email_verified", False):
            user.email_verification_token = make_email_verification_token()
            user.email_verification_expires_at = datetime.utcnow() + timedelta(hours=24)
            send_verification_email(user, request)
            audit_event(db, request, "user.email_confirmation_resent", user, "user", user.id, "Reenvio de confirmação solicitado.")
            db.commit()
        return templates.TemplateResponse("login.html", {"request": request, "error": "Se a conta existir e ainda estiver pendente, um novo link de confirmação será enviado.", "created": 0, "email_pending": 1, "email_verified": 0})
    finally:
        db.close()


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, created: int = 0, email_pending: int = 0, email_verified: int = 0, email_sent: int = 0, email_dev: int = 0):
    return templates.TemplateResponse("login.html", {"request": request, "error": None, "created": created, "email_pending": email_pending, "email_verified": email_verified, "email_sent": email_sent, "email_dev": email_dev})


@app.post("/login")
def login(request: Request, login_identifier: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    try:
        identifier = (login_identifier or "").strip().lower()
        cpf_digits = re.sub(r"\D", "", identifier)

        if "@" in identifier:
            user = db.query(User).filter(User.email == identifier).first()
        else:
            user = db.query(User).filter(User.cpf == cpf_digits).first()

        if not user or not verify_password(password.strip(), user.password):
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "E-mail/CPF ou senha inválidos.",
                "login_identifier": login_identifier,
            })

        token = secrets.token_urlsafe(24)
        SESSIONS[token] = user.id

        response = RedirectResponse("/minha-conta", status_code=303)
        response.set_cookie("session_token", token, httponly=True, samesite="lax")
        return response
    finally:
        db.close()


@app.get("/logout")
def logout(request: Request):
    response = RedirectResponse("/", status_code=303)
    token = request.cookies.get("session_token")
    if token and token in SESSIONS:
        del SESSIONS[token]
    response.delete_cookie("session_token")
    return response


@app.get("/auction/{auction_id}", response_class=HTMLResponse)
def auction_page(request: Request, auction_id: int):
    db = SessionLocal()
    try:
        user = current_user(request, db)
        item = db.get(AuctionItem, auction_id)
        if not item:
            raise HTTPException(status_code=404, detail="Leilão não encontrado.")

        changed = start_auction_if_due(item)
        if changed:
            db.commit()

        messages = (
            db.query(ChatMessage)
            .filter(ChatMessage.auction_id == auction_id)
            .order_by(desc(ChatMessage.created_at))
            .limit(50)
            .all()
        )
        messages = list(reversed(messages))
        return templates.TemplateResponse(
            "auction.html",
            {
                "request": request,
                "user": user,
                "item": {**public_auction_payload(item, db), "cashback": cashback_payload(item, db, user)},
                "entity": item,
                "chat_messages": messages,
                "allowed_bids": sorted(ALLOWED_BIDS),
                "fee_percent": "1%",
            },
        )
    finally:
        db.close()


@app.post("/api/auction/{auction_id}/bid")
async def place_bid(request: Request, auction_id: int, bid_value: float = Form(...)):
    payload = None
    button_cooldown = 0

    # Serializa lances do mesmo leilão dentro deste processo.
    # Isso evita clique duplo/race condition gerando contagem, preço e último lance inconsistentes.
    async with AUCTION_BID_LOCKS[auction_id]:
        db = SessionLocal()
        try:
            user = require_user(request, db)
            item = db.get(AuctionItem, auction_id)
            if not item:
                raise HTTPException(status_code=404, detail="Leilão não encontrado.")

            now = datetime.utcnow()
            if start_auction_if_due(item, now):
                db.commit()
                item = db.get(AuctionItem, auction_id)

            if item.status != "live":
                raise HTTPException(status_code=400, detail="Leilão não está ao vivo.")
            if item.ends_at and item.ends_at <= now:
                raise HTTPException(status_code=400, detail="Este leilão já está encerrando. Aguarde a atualização.")

            bid_value = BR(bid_value)
            if bid_value not in ALLOWED_BIDS:
                raise HTTPException(status_code=400, detail="Valor de lance inválido.")

            active_turbo = compute_turbo_level(item)
            button_cooldown = bid_button_cooldown_seconds(bid_value, active_turbo)

            previous_user_bid_count = db.query(func.count(Bid.id)).filter(
                Bid.auction_id == item.id,
                Bid.user_id == user.id,
            ).scalar() or 0
            if active_turbo >= 2 and previous_user_bid_count <= 0:
                raise HTTPException(status_code=403, detail="O modo turbo é exclusivo para quem já participou deste leilão antes da ativação.")

            last_same_button = (
                db.query(Bid.created_at)
                .filter(Bid.auction_id == item.id, Bid.user_id == user.id, Bid.bid_value == bid_value)
                .order_by(desc(Bid.created_at))
                .first()
            )
            if last_same_button:
                elapsed = (now - last_same_button[0]).total_seconds()
                if elapsed < button_cooldown:
                    remaining_cd = math.ceil(button_cooldown - elapsed)
                    return JSONResponse(
                        {"ok": False, "detail": f"Aguarde {remaining_cd}s para usar esse botão novamente.", "retry_after": remaining_cd},
                        status_code=429,
                    )

            fee_value = 0.0
            increment = BR(bid_value)

            item.current_price = BR(item.current_price + increment)
            item.total_bid_fees = BR(getattr(item, "total_bid_fees", 0.0) or 0.0)
            item.total_bid_spent = BR(getattr(item, "total_bid_spent", 0.0) or 0.0)

            turbo = compute_turbo_level(item)
            item.turbo_level = turbo
            button_cooldown = bid_button_cooldown_seconds(bid_value, turbo)

            current_end = item.ends_at if item.ends_at and item.ends_at > now else now
            if turbo == 0:
                delta_seconds = normal_time_delta_seconds(item, bid_value)
                item.ends_at = current_end + timedelta(seconds=delta_seconds)
                if item.ends_at <= now:
                    item.ends_at = now + timedelta(seconds=1)
            else:
                seconds = turbo_bid_seconds(bid_value, turbo)
                item.ends_at = now + timedelta(seconds=seconds)

            db.add(Bid(
                auction_id=item.id,
                user_id=user.id,
                bid_value=bid_value,
                fee_value=fee_value,
                price_increment=increment,
            ))
            db.commit()
            db.refresh(item)

            payload = public_auction_live_payload(item, db)
            payload["button_cooldown"] = button_cooldown
            payload["bid_value"] = bid_value
            payload["server_time"] = datetime.utcnow().isoformat()
        finally:
            db.close()

    if payload:
        asyncio.create_task(manager.broadcast(auction_id, {"type": "auction_update", "auction": payload}))
    return JSONResponse({"ok": True, "auction": payload, "button_cooldown": button_cooldown})


@app.get("/api/auction/{auction_id}/state")
async def auction_state(request: Request, auction_id: int):
    db = SessionLocal()
    try:
        item = db.get(AuctionItem, auction_id)
        if not item:
            raise HTTPException(status_code=404, detail="Leilão não encontrado.")
        changed = start_auction_if_due(item)
        if changed:
            db.commit()
            payload = public_auction_live_payload(item, db)
            asyncio.create_task(manager.broadcast(auction_id, {"type": "auction_update", "auction": payload}))
            return JSONResponse({"ok": True, "auction": payload})
        return JSONResponse({"ok": True, "auction": public_auction_live_payload(item, db)})
    finally:
        db.close()


@app.post("/api/auction/{auction_id}/cashback/join")
def join_cashback(request: Request, auction_id: int):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        item = db.get(AuctionItem, auction_id)
        if not item:
            raise HTTPException(status_code=404, detail="Leilão não encontrado.")
        event = db.query(CashbackEvent).filter(CashbackEvent.auction_id == auction_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="Sorteio de cashback ainda não disponível.")
        if event.status != "open" or event.join_deadline <= datetime.utcnow():
            raise HTTPException(status_code=400, detail="Prazo para participar do cashback encerrado.")
        spent = sum((b.bid_value or 0.0) for b in db.query(Bid).filter(Bid.auction_id == auction_id, Bid.user_id == user.id).all())
        if spent <= 0:
            raise HTTPException(status_code=403, detail="Apenas participantes deste leilão podem entrar no cashback.")
        existing = db.query(CashbackEntry).filter(CashbackEntry.event_id == event.id, CashbackEntry.user_id == user.id).first()
        if not existing:
            db.add(CashbackEntry(event_id=event.id, user_id=user.id, auction_id=auction_id, amount_spent=BR(spent)))
        db.commit()
        fresh = db.get(AuctionItem, auction_id)
        return JSONResponse({"ok": True, "cashback": cashback_payload(fresh, db, user)})
    finally:
        db.close()

@app.post("/api/auction/{auction_id}/chat")
async def send_chat(request: Request, auction_id: int, message: str = Form(...)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        item = db.get(AuctionItem, auction_id)
        if not item:
            raise HTTPException(status_code=404, detail="Leilão não encontrado.")
        if not auction_chat_is_open(item):
            raise HTTPException(status_code=403, detail="O chat está disponível apenas próximo ao início ou durante o leilão.")
        if item.chat_paused:
            raise HTTPException(status_code=403, detail="O chat está pausado pelo administrador.")
        if user.chat_muted:
            raise HTTPException(status_code=403, detail="Seu chat está bloqueado por um administrador.")
        text_msg = (message or "").strip()
        if not text_msg:
            raise HTTPException(status_code=400, detail="Mensagem vazia.")
        text_lower = text_msg.lower()
        if any(bad in text_lower for bad in BANNED_WORDS):
            raise HTTPException(status_code=400, detail="Mensagem bloqueada pela moderação automática.")
        text_msg = re.sub(r"\s+", " ", text_msg)[:250]
        msg = ChatMessage(auction_id=auction_id, user_id=user.id, message=text_msg)
        db.add(msg)
        db.commit()
        db.refresh(msg)
        payload = {
            "type": "chat_message",
            "message": {"author": public_user_name(user), "text": msg.message, "created_at": msg.created_at.strftime("%H:%M:%S")},
        }
    finally:
        db.close()

    await manager.broadcast(auction_id, payload)
    return JSONResponse({"ok": True})


@app.get("/minha-conta", response_class=HTMLResponse)
def my_account(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        stats = user_stats(db, user)
        pending_orders = (
            db.query(WinnerOrder)
            .options(selectinload(WinnerOrder.auction))
            .filter(WinnerOrder.user_id == user.id, WinnerOrder.status == "pending_payment")
            .order_by(desc(WinnerOrder.created_at))
            .limit(10)
            .all()
        )
        won_orders = (
            db.query(WinnerOrder)
            .options(selectinload(WinnerOrder.auction))
            .filter(WinnerOrder.user_id == user.id)
            .order_by(desc(WinnerOrder.created_at))
            .limit(30)
            .all()
        )
        latest = [build_order_card(x) for x in won_orders[:5]]
        pending = [build_order_card(x) for x in pending_orders[:3]]
        return templates.TemplateResponse(
            "account_pages.html",
            {
                "request": request,
                "user": user,
                "section": "dashboard",
                "stats": stats,
                "pending_orders": pending,
                "latest_orders": latest,
                "wallet_transactions": db.query(WalletTransaction).filter(WalletTransaction.user_id == user.id).order_by(desc(WalletTransaction.created_at)).limit(30).all(),
                "withdrawals": db.query(WithdrawalRequest).filter(WithdrawalRequest.user_id == user.id).order_by(desc(WithdrawalRequest.created_at)).limit(20).all(),
                "tickets": db.query(SupportTicket).filter(SupportTicket.user_id == user.id).order_by(desc(SupportTicket.created_at)).limit(20).all(),
                "orders_raw": won_orders,
                "account_status_label": account_status_label(user),
            },
        )
    finally:
        db.close()


@app.post("/minha-conta/saldo")
@app.post("/minha-conta/saldo/pix")
@app.post("/minha-conta/saldo/cartao")
def account_add_balance(request: Request, amount: float = Form(...)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        amount = BR(amount)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Valor inválido.")
        # Em produção, o saldo só deve ser creditado por webhook confirmado do gateway de pagamento.
        tx = WalletTransaction(user_id=user.id, amount=0.0, kind="deposit_pending", note=f"Depósito solicitado: R$ {fmt_money(amount)}. Aguardando integração/webhook do gateway.")
        db.add(tx)
        audit_event(db, request, "wallet.deposit_requested", user, "wallet_transaction", "pending", f"Valor solicitado: R$ {fmt_money(amount)}")
        db.commit()
        return RedirectResponse("/minha-conta?saldo=1", status_code=303)
    finally:
        db.close()



@app.get("/minha-conta/comprovantes", response_class=HTMLResponse)
def my_receipts(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        transactions = db.query(WalletTransaction).filter(WalletTransaction.user_id == user.id).order_by(desc(WalletTransaction.created_at)).limit(100).all()
        orders = db.query(WinnerOrder).filter(WinnerOrder.user_id == user.id).order_by(desc(WinnerOrder.created_at)).limit(100).all()
        audits = db.query(AuditLog).filter(AuditLog.user_id == user.id).order_by(desc(AuditLog.created_at)).limit(100).all()
        return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, "section": "receipts", "wallet_transactions": transactions, "orders_raw": orders, "audit_logs": audits})
    finally:
        db.close()

@app.get("/minha-conta/cadastro", response_class=HTMLResponse)
def my_profile(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, "section": "profile"})
    finally:
        db.close()


@app.get("/minha-conta/verificacao", response_class=HTMLResponse)
def account_identity_page(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        return templates.TemplateResponse("identity_verification.html", {"request": request, "user": user, "error": None, "success": None})
    finally:
        db.close()


@app.post("/minha-conta/verificacao")
async def account_identity_submit(request: Request, document_file: UploadFile | None = File(None), selfie_file: UploadFile | None = File(None)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        if not document_file or not document_file.filename or not selfie_file or not selfie_file.filename:
            return templates.TemplateResponse("identity_verification.html", {"request": request, "user": user, "error": "Envie o documento e a selfie para análise.", "success": None}, status_code=400)
        user.document_file_url = save_uploaded_image(document_file)
        user.selfie_file_url = save_uploaded_image(selfie_file)
        user.identity_status = "pending"
        user.identity_note = "Documentos enviados. Aguardando análise do administrador."
        audit_event(db, request, "user.identity_submitted", user, "user", user.id, "Documentos de identidade enviados para análise.")
        db.commit()
        return templates.TemplateResponse("identity_verification.html", {"request": request, "user": user, "error": None, "success": "Documentos enviados com sucesso. Sua conta ficará em análise até a conferência administrativa."})
    finally:
        db.close()


@app.get("/minha-conta/participacoes", response_class=HTMLResponse)
def my_participations(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        bids = db.query(Bid).filter(Bid.user_id == user.id).order_by(desc(Bid.created_at)).all()
        grouped = {}
        for bid in bids:
            aid = bid.auction_id
            if aid not in grouped:
                item = bid.auction
                grouped[aid] = {
                    "auction_id": aid,
                    "title": item.title,
                    "image_url": safe_image_url(item.image_url),
                    "status": item.status,
                    "total_bids": 0,
                    "total_spent": 0.0,
                    "won": item.winner_user_id == user.id,
                    "last_activity": bid.created_at.strftime("%d/%m/%Y %H:%M"),
                }
            grouped[aid]["total_bids"] += 1
            grouped[aid]["total_spent"] = BR(grouped[aid]["total_spent"] + bid.bid_value)
        data = list(grouped.values())
        return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, "section": "participations", "items": data})
    finally:
        db.close()


@app.get("/minha-conta/ganhos", response_class=HTMLResponse)
def my_wins(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        orders = db.query(WinnerOrder).filter(WinnerOrder.user_id == user.id).order_by(desc(WinnerOrder.created_at)).all()
        data = [build_order_card(x) for x in orders]
        return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, "section": "wins", "orders": data})
    finally:
        db.close()


@app.get("/minha-conta/pagamentos", response_class=HTMLResponse)
def my_pending_payments(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        orders = (
            db.query(WinnerOrder)
            .filter(WinnerOrder.user_id == user.id, WinnerOrder.status == "pending_payment")
            .order_by(desc(WinnerOrder.created_at))
            .all()
        )
        data = [build_order_card(x) for x in orders]
        return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, "section": "payments", "orders": data})
    finally:
        db.close()


@app.get("/minha-conta/pagamentos/{auction_id}", response_class=HTMLResponse)
def my_payment_checkout(request: Request, auction_id: int):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        order = (
            db.query(WinnerOrder)
            .filter(WinnerOrder.user_id == user.id, WinnerOrder.auction_id == auction_id)
            .order_by(desc(WinnerOrder.created_at))
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        return templates.TemplateResponse(
            "account_pages.html",
            {"request": request, "user": user, "section": "checkout", "order": build_order_card(order), "entity": order},
        )
    finally:
        db.close()


@app.post("/minha-conta/pagamentos/{auction_id}/confirmar")
def confirm_payment_flow(
    request: Request,
    auction_id: int,
    delivery_name: str = Form(...),
    delivery_cep: str = Form(...),
    delivery_street: str = Form(...),
    delivery_number: str = Form(...),
    delivery_district: str = Form(...),
    delivery_city: str = Form(...),
    delivery_state: str = Form(...),
    payment_method: str = Form("pix"),  # 🔥 NOVO
):
    db = SessionLocal()
    try:
        user = require_user(request, db)

        order = (
            db.query(WinnerOrder)
            .filter(WinnerOrder.user_id == user.id, WinnerOrder.auction_id == auction_id)
            .order_by(desc(WinnerOrder.created_at))
            .first()
        )

        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        if not user_is_verified(user):
            raise HTTPException(status_code=403, detail="Para liberar pagamento/envio do produto, sua conta precisa estar confirmada pelo administrador.")

        # 📦 Atualiza endereço
        order.delivery_name = delivery_name.strip()
        order.delivery_cep = delivery_cep.strip()
        order.delivery_street = delivery_street.strip()
        order.delivery_number = delivery_number.strip()
        order.delivery_district = delivery_district.strip()
        order.delivery_city = delivery_city.strip()
        order.delivery_state = delivery_state.strip()

        # 💰 PAGAMENTO COM SALDO
        if payment_method == "wallet":
            if user.wallet_balance < order.final_price:
                raise HTTPException(status_code=400, detail="Saldo insuficiente.")

            user.wallet_balance = BR(user.wallet_balance - order.final_price)

            db.add(WalletTransaction(
                user_id=user.id,
                amount=-order.final_price,
                kind="payment",
                note=f"Pagamento do leilão #{order.auction_id}"
            ))

        # PIX/CARTÃO: em homologação, não marca como pago sem webhook real do gateway.
        elif payment_method in ["pix", "card"]:
            order.status = "pending_gateway"
            order.admin_note = "Pagamento iniciado. Aguardando confirmação oficial do gateway/webhook."
            audit_event(db, request, "order.payment_gateway_pending", user, "order", order.id, f"Método: {payment_method}")
            db.commit()
            return RedirectResponse("/minha-conta/ganhos", status_code=303)

        # Pagamento com saldo interno confirmado.
        order.status = "paid"
        order.paid_at = datetime.utcnow()
        order.admin_note = "Pagamento confirmado com saldo interno."
        audit_event(db, request, "order.payment_wallet_confirmed", user, "order", order.id, f"Valor: R$ {fmt_money(order.final_price)}")

        db.commit()

        return RedirectResponse("/minha-conta/ganhos", status_code=303)

    finally:
        db.close()


@app.get("/minha-conta/expirados", response_class=HTMLResponse)
def my_expired_orders(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        orders = (
            db.query(WinnerOrder)
            .filter(WinnerOrder.user_id == user.id, WinnerOrder.status == "expired")
            .order_by(desc(WinnerOrder.created_at))
            .all()
        )
        data = [build_order_card(x) for x in orders]
        return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, "section": "expired", "orders": data})
    finally:
        db.close()



def build_finished_auctions(db: Session) -> list[dict]:
    # Evita uma consulta de WinnerOrder para cada leilão finalizado.
    finished_status = ["ended", "delivered", "expired"]
    items = (
        db.query(AuctionItem)
        .options(selectinload(AuctionItem.winner))
        .filter(AuctionItem.status.in_(finished_status))
        .order_by(desc(AuctionItem.created_at))
        .limit(80)
        .all()
    )
    item_ids = [item.id for item in items]
    latest_order_by_auction: dict[int, WinnerOrder] = {}
    if item_ids:
        orders = (
            db.query(WinnerOrder)
            .filter(WinnerOrder.auction_id.in_(item_ids))
            .order_by(desc(WinnerOrder.created_at))
            .all()
        )
        for order in orders:
            latest_order_by_auction.setdefault(order.auction_id, order)

    rows = []
    for item in items:
        order = latest_order_by_auction.get(item.id)
        source_price = BR(item.source_price or 0.0)
        final_price = BR((order.final_price if order else item.current_price) or 0.0)
        fees_total = BR(item.total_bid_fees or 0.0)
        result = BR(final_price + fees_total + (final_price if order and order.status in ["paid", "processing", "purchased", "sent", "delivered"] else 0.0) - source_price)
        rows.append({
            "title": item.title,
            "winner_name": public_user_name(item.winner) if item.winner else "",
            "source_price": source_price,
            "final_price": final_price,
            "fees_total": fees_total,
            "site_complement": BR(max(0.0, source_price - (final_price + fees_total))),
            "result": result,
            "status_label": order.status if order else item.status,
        })
    return rows


def user_audit_map(db: Session, users: list[User]) -> dict[int, dict]:
    # Versão em lote. A anterior fazia várias consultas por usuário.
    user_ids = [u.id for u in users]
    if not user_ids:
        return {}

    def grouped_count(model, column):
        return {
            user_id: int(total or 0)
            for user_id, total in db.query(column, func.count(model.id)).filter(column.in_(user_ids)).group_by(column).all()
        }

    bid_counts = grouped_count(Bid, Bid.user_id)
    order_counts = grouped_count(WinnerOrder, WinnerOrder.user_id)

    data = {u.id: {"bids": bid_counts.get(u.id, 0), "orders": order_counts.get(u.id, 0), "transactions": [], "withdrawals": [], "tickets": []} for u in users}

    transactions = db.query(WalletTransaction).filter(WalletTransaction.user_id.in_(user_ids)).order_by(desc(WalletTransaction.created_at)).limit(300).all()
    withdrawals = db.query(WithdrawalRequest).filter(WithdrawalRequest.user_id.in_(user_ids)).order_by(desc(WithdrawalRequest.created_at)).limit(300).all()
    tickets = db.query(SupportTicket).filter(SupportTicket.user_id.in_(user_ids)).order_by(desc(SupportTicket.created_at)).limit(300).all()

    for tx in transactions:
        bucket = data.get(tx.user_id)
        if bucket is not None and len(bucket["transactions"]) < 10:
            bucket["transactions"].append(tx)
    for wd in withdrawals:
        bucket = data.get(wd.user_id)
        if bucket is not None and len(bucket["withdrawals"]) < 10:
            bucket["withdrawals"].append(wd)
    for ticket in tickets:
        bucket = data.get(ticket.user_id)
        if bucket is not None and len(bucket["tickets"]) < 10:
            bucket["tickets"].append(ticket)
    return data


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    db = SessionLocal()
    try:
        admin = current_user(request, db)

        if not admin:
            return RedirectResponse("/login", status_code=303)

        if admin.is_banned:
            return RedirectResponse("/login", status_code=303)

        if not admin.is_admin:
            return RedirectResponse("/", status_code=303)

        search = (request.query_params.get("q") or "").strip()
        users_query = db.query(User)
        if search:
            like = f"%{search}%"
            users_query = users_query.filter((User.full_name.ilike(like)) | (User.public_name.ilike(like)) | (User.email.ilike(like)) | (User.cpf.ilike(like)) | (User.phone.ilike(like)))
        users = users_query.order_by(desc(User.created_at)).limit(40).all()
        items = db.query(AuctionItem).filter(AuctionItem.status.in_(["live", "scheduled", "relisted", "paused"])).order_by(desc(AuctionItem.created_at)).limit(40).all()
        for item in items:
            item.collected_percent = auction_progress_percent(item)
            item.cash_reserved = auction_cash_reserved_before_payment(item)
            item.expected_total_if_paid = auction_total_if_paid(item)
            item.expected_profit_if_paid = auction_expected_profit_if_paid(item)
        orders = db.query(WinnerOrder).options(selectinload(WinnerOrder.auction), selectinload(WinnerOrder.user)).order_by(desc(WinnerOrder.created_at)).limit(50).all()
        pending_payment_orders = [o for o in orders if o.status == "pending_payment"]
        shipping_orders = [o for o in orders if o.status in ["paid", "processing", "purchased"]]
        consultation_orders = [o for o in orders if o.status in ["sent", "delivered", "dispute", "resolved", "closed"]]
        withdrawal_requests = db.query(WithdrawalRequest).options(selectinload(WithdrawalRequest.user)).order_by(desc(WithdrawalRequest.created_at)).limit(40).all()
        support_tickets = db.query(SupportTicket).options(selectinload(SupportTicket.user), selectinload(SupportTicket.order)).order_by(desc(SupportTicket.created_at)).limit(40).all()
        admin_order_cards = build_admin_order_cards(db, orders)
        returned_items = build_returned_items(db)
        finance = build_finance_dashboard(db)
        cashflow_movements = build_cashflow_movements(db)
        auction_results = build_auction_results(db)
        stats = {
            "users": db.query(User).count(),
            "live": db.query(AuctionItem).filter(AuctionItem.status == "live").count(),
            "scheduled": db.query(AuctionItem).filter(AuctionItem.status.in_(["scheduled", "relisted"])).count(),
            "pending_payment": len(pending_payment_orders),
            "completed": db.query(AuctionItem).filter(AuctionItem.status.in_(["ended"])).count(),
            "pending_shipping": len(shipping_orders),
            "active_users": db.query(User).filter(User.is_banned == False).count(),
            "identity_pending": db.query(User).filter(User.identity_status == "pending").count(),
            "pending_withdrawals": db.query(WithdrawalRequest).filter(WithdrawalRequest.status == "pending").count(),
            "open_tickets": db.query(SupportTicket).filter(SupportTicket.status.in_(["open", "in_review", "dispute"])).count(),
            "returned_products": len(returned_items),
        }
        return templates.TemplateResponse(
            "admin.html",
            {
                "request": request,
                "user": admin,
                "stats": stats,
                "users": users,
                "items": items,
                "orders": orders,
                "admin_order_cards": admin_order_cards,
                "pending_payment_orders": pending_payment_orders,
                "shipping_orders": shipping_orders,
                "consultation_orders": consultation_orders,
                "withdrawal_requests": withdrawal_requests,
                "support_tickets": support_tickets,
                "user_audit": user_audit_map(db, users),
                "audit_logs": db.query(AuditLog).options(selectinload(AuditLog.user)).order_by(desc(AuditLog.created_at)).limit(40).all(),
                "search": search,
                "finished_auctions": build_finished_auctions(db),
                "returned_items": returned_items,
                "finance": finance,
                "cashflow_movements": cashflow_movements,
                "auction_results": auction_results,
                "recent_chat_messages": db.query(ChatMessage).options(selectinload(ChatMessage.user), selectinload(ChatMessage.auction)).order_by(desc(ChatMessage.created_at)).limit(25).all(),
                "moderation_users": db.query(User).filter((User.is_banned == True) | (User.chat_muted == True)).order_by(desc(User.created_at)).limit(40).all(),
                "payment_deadline_minutes": PAYMENT_DEADLINE_MINUTES,
                "returned_items": returned_items,
                "suggestion_vote_stats": suggestion_vote_stats(db),
            },
        )
    finally:
        db.close()


@app.post("/admin/item/create")
async def admin_create_item(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    image_url: str = Form(""),
    image_file: UploadFile | None = File(None),
    source_store: str = Form("Mercado Livre"),
    source_url: str = Form(""),
    source_price: float = Form(...),
    start_in_minutes: int = Form(60),
    schedule_mode: str = Form("minutes"),
    scheduled_start_at: str = Form(""),
    initial_duration_minutes: int = Form(30),
    turbo_enabled: int = Form(1),
    turbo_trigger_percent: float = Form(0),
    turbo_fee_target_percent: float = Form(10),
    winner_min_percent: float = Form(50),
    bid_fee_percent: float = Form(1),
    max_site_complement_percent: float = Form(50),
    cashback_enabled: int = Form(0),
):
    db = SessionLocal()
    try:
        require_admin(request, db)
        final_image = save_uploaded_image(image_file) or image_url.strip() or "https://via.placeholder.com/900x600?text=Produto"
        now = datetime.utcnow()
        if schedule_mode == "datetime" and scheduled_start_at.strip():
            try:
                scheduled_start = datetime.fromisoformat(scheduled_start_at.strip())
            except ValueError:
                scheduled_start = now + timedelta(minutes=int(start_in_minutes))
        else:
            scheduled_start = now + timedelta(minutes=int(start_in_minutes))
        initial_seconds = clamp_initial_duration(initial_duration_minutes)
        if float(turbo_trigger_percent or 0) > 0:
            turbo_start = max(1.0, min(95.0, float(turbo_trigger_percent)))
        else:
            turbo_start = calculate_turbo_trigger_percent(winner_min_percent, turbo_fee_target_percent)
        item = AuctionItem(
            title=title.strip(),
            description=description.strip(),
            image_url=final_image,
            source_store=source_store.strip(),
            source_url=source_url.strip(),
            source_price=BR(source_price),
            start_price=0.0,
            current_price=0.0,
            status="scheduled",
            scheduled_start=scheduled_start,
            initial_duration_seconds=initial_seconds,
            turbo_enabled=bool(int(turbo_enabled)),
            turbo_trigger_percent=turbo_start,
            turbo_level_3_percent=turbo_start + 5.0,
            turbo_level_4_percent=turbo_start + 10.0,
            bid_fee_percent=BR(bid_fee_percent),
            winner_min_percent=BR(winner_min_percent),
            target_profit_percent=BR(turbo_fee_target_percent),
            turbo_base_value=BR(source_price * (turbo_start / 100.0)),
            cashback_enabled=bool(int(cashback_enabled)),
        )
        db.add(item)
        db.commit()
        return RedirectResponse("/admin", status_code=303)
    finally:
        db.close()



@app.post("/admin/item/{item_id}/returned-update-relist")
async def admin_returned_update_relist(
    request: Request,
    item_id: int,
    title: str = Form(...),
    description: str = Form(""),
    source_store: str = Form(""),
    source_url: str = Form(""),
    image_url: str = Form(""),
    image_file: UploadFile | None = File(None),
    turbo_base_value: float = Form(...),
    start_in_minutes: int = Form(5),
    initial_duration_minutes: int = Form(30),
    bid_fee_percent: float = Form(10),
):
    db = SessionLocal()
    try:
        require_admin(request, db)
        item = db.get(AuctionItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Leilão não encontrado.")

        source_price = BR(item.source_price or 0.0)
        turbo_base_value = BR(turbo_base_value)
        if source_price <= 0:
            raise HTTPException(status_code=400, detail="Preço original inválido.")
        if turbo_base_value <= 0:
            raise HTTPException(status_code=400, detail="Valor-base do turbo inválido.")

        final_image = save_uploaded_image(image_file) or image_url.strip() or item.image_url

        turbo_trigger_amount = BR(turbo_base_value / 2.0)
        turbo_trigger_percent = max(1.0, min(95.0, (turbo_trigger_amount / source_price) * 100.0))

        item.title = title.strip()
        item.description = description.strip()
        item.source_store = source_store.strip()
        item.source_url = source_url.strip()
        item.image_url = final_image
        item.bid_fee_percent = BR(bid_fee_percent)

        reset_relisted_public_history(db, item)
        item.status = "relisted"
        item.chat_paused = False
        item.initial_duration_seconds = clamp_initial_duration(initial_duration_minutes)
        item.scheduled_start = datetime.utcnow() + timedelta(minutes=int(start_in_minutes))

        item.winner_min_percent = round((turbo_base_value / source_price) * 100.0, 2)
        item.target_profit_percent = 0.0
        item.turbo_trigger_percent = turbo_trigger_percent
        item.turbo_level_3_percent = min(98.0, turbo_trigger_percent + 5.0)
        item.turbo_level_4_percent = min(99.0, turbo_trigger_percent + 10.0)
        item.turbo_level = 0
        item.turbo_enabled = True

        db.commit()
        return RedirectResponse("/admin", status_code=303)
    finally:
        db.close()


@app.post("/admin/order/{order_id}/admin-update")
def admin_update_order_control(
    request: Request,
    order_id: int,
    purchase_link: str = Form(""),
    purchase_status: str = Form(""),
    tracking_code: str = Form(""),
    admin_note: str = Form(""),
    mark_purchased: int = Form(0),
    mark_sent: int = Form(0),
    mark_delivered: int = Form(0),
):
    db = SessionLocal()
    try:
        require_admin(request, db)
        order = db.get(WinnerOrder, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        order.purchase_link = purchase_link.strip()
        order.purchase_status = purchase_status.strip()
        order.tracking_code = tracking_code.strip()
        order.admin_note = admin_note.strip()
        now = datetime.utcnow()
        if int(mark_purchased or 0):
            order.status = "purchased"
            order.purchased_at = now
        if int(mark_sent or 0):
            order.status = "sent"
            order.sent_at = now
        if int(mark_delivered or 0):
            order.status = "delivered"
            order.delivered_at = now
        db.commit()
        return RedirectResponse("/admin", status_code=303)
    finally:
        db.close()


@app.post("/admin/order/{order_id}/message")
def admin_send_order_message(request: Request, order_id: int, message: str = Form(...)):
    db = SessionLocal()
    try:
        admin_user = require_admin(request, db)
        order = db.get(WinnerOrder, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        clean = (message or "").strip()
        if not clean:
            raise HTTPException(status_code=400, detail="Mensagem vazia.")
        db.add(AdminDirectMessage(order_id=order.id, user_id=order.user_id, admin_id=admin_user.id, message=clean, is_open=True))
        db.commit()
        return RedirectResponse("/admin", status_code=303)
    finally:
        db.close()


@app.post("/admin/order/{order_id}/close-chat")
def admin_close_order_chat(request: Request, order_id: int):
    db = SessionLocal()
    try:
        require_admin(request, db)
        for msg in db.query(AdminDirectMessage).filter(AdminDirectMessage.order_id == order_id).all():
            msg.is_open = False
        db.commit()
        return RedirectResponse("/admin", status_code=303)
    finally:
        db.close()


@app.post("/admin/order/{order_id}/reopen-chat")
def admin_reopen_order_chat(request: Request, order_id: int):
    db = SessionLocal()
    try:
        admin_user = require_admin(request, db)
        order = db.get(WinnerOrder, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        last = db.query(AdminDirectMessage).filter(AdminDirectMessage.order_id == order_id).order_by(desc(AdminDirectMessage.created_at)).first()
        if last:
            last.is_open = True
        else:
            db.add(AdminDirectMessage(order_id=order.id, user_id=order.user_id, admin_id=admin_user.id, message="Atendimento aberto pelo admin.", is_open=True))
        db.commit()
        return RedirectResponse("/admin", status_code=303)
    finally:
        db.close()


@app.post("/admin/item/{item_id}/start-now")
def admin_start_now(request: Request, item_id: int):
    db = SessionLocal()
    try:
        require_admin(request, db)
        item = db.get(AuctionItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Leilão não encontrado.")
        item.status = "live"
        item.scheduled_start = datetime.utcnow()
        duration = getattr(item, "initial_duration_seconds", DEFAULT_INITIAL_DURATION_SECONDS) or DEFAULT_INITIAL_DURATION_SECONDS
        item.ends_at = datetime.utcnow() + timedelta(seconds=min(MAX_INITIAL_DURATION_SECONDS, duration))
        item.chat_paused = False
        db.commit()
        return RedirectResponse(f"/auction/{item_id}", status_code=303)
    finally:
        db.close()


@app.post("/admin/item/{item_id}/delete")
def admin_delete_item(request: Request, item_id: int):
    db = SessionLocal()
    try:
        require_admin(request, db)
        item = db.get(AuctionItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Leilão não encontrado.")
        if item.status == "live":
            raise HTTPException(status_code=400, detail="Não é possível excluir um leilão em andamento.")
        db.delete(item)
        db.commit()
        return RedirectResponse("/admin", status_code=303)
    finally:
        db.close()


@app.post("/admin/item/{item_id}/toggle-chat")
def admin_toggle_chat(request: Request, item_id: int):
    db = SessionLocal()
    try:
        require_admin(request, db)
        item = db.get(AuctionItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Leilão não encontrado.")
        if item.status != "live":
            raise HTTPException(status_code=400, detail="O chat só pode ser pausado ou iniciado enquanto o leilão estiver ao vivo.")
        item.chat_paused = not item.chat_paused
        db.commit()
        return RedirectResponse(f"/auction/{item_id}", status_code=303)
    finally:
        db.close()


@app.post("/admin/user/{user_id}/credit")
def admin_credit_user(request: Request, user_id: int, amount: float = Form(...)):
    db = SessionLocal()
    try:
        require_admin(request, db)
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        amount = BR(amount)
        user.wallet_balance = BR(user.wallet_balance + amount)
        db.add(WalletTransaction(user_id=user.id, amount=amount, kind="manual_adjustment", note="Crédito admin"))
        db.commit()
        return RedirectResponse("/admin", status_code=303)
    finally:
        db.close()


@app.post("/admin/user/{user_id}/toggle-ban")
def admin_toggle_ban(request: Request, user_id: int):
    db = SessionLocal()
    try:
        admin = require_admin(request, db)
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        if user.id == admin.id:
            raise HTTPException(status_code=400, detail="Você não pode bloquear a si mesmo.")
        user.is_banned = not user.is_banned
        db.commit()
        return RedirectResponse("/admin", status_code=303)
    finally:
        db.close()


@app.post("/admin/user/{user_id}/toggle-mute")
def admin_toggle_mute(request: Request, user_id: int):
    db = SessionLocal()
    try:
        admin = require_admin(request, db)
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        if user.id == admin.id:
            raise HTTPException(status_code=400, detail="Você não pode mutar a si mesmo.")
        user.chat_muted = not user.chat_muted
        db.commit()
        return RedirectResponse("/admin", status_code=303)
    finally:
        db.close()


@app.post("/admin/order/{order_id}/tracking")
def admin_set_tracking(request: Request, order_id: int, tracking_code: str = Form(""), admin_note: str = Form("")):
    db = SessionLocal()
    try:
        require_admin(request, db)
        order = db.get(WinnerOrder, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        if tracking_code.strip():
            order.tracking_code = tracking_code.strip()
            if order.status in {"paid", "processing", "purchased"}:
                order.status = "sent"
        if admin_note.strip():
            order.admin_note = admin_note.strip()
        db.commit()
        return RedirectResponse("/admin", status_code=303)
    finally:
        db.close()


@app.post("/admin/order/{order_id}/set-status")
def admin_set_order_status(request: Request, order_id: int, status: str = Form(...), admin_note: str = Form("")):
    db = SessionLocal()
    try:
        require_admin(request, db)
        order = db.get(WinnerOrder, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        allowed = {"pending_payment", "paid", "processing", "purchased", "sent", "delivered", "expired"}
        if status not in allowed:
            raise HTTPException(status_code=400, detail="Status inválido.")
        order.status = status
        if admin_note.strip():
            order.admin_note = admin_note.strip()
        item = db.get(AuctionItem, order.auction_id)
        if item and status == "delivered":
            item.status = "ended"
        db.commit()
        return RedirectResponse("/admin", status_code=303)
    finally:
        db.close()


@app.post("/admin/item/{item_id}/relist")
def admin_relist(request: Request, item_id: int, start_in_minutes: int = Form(...), initial_duration_minutes: int = Form(30)):
    db = SessionLocal()
    try:
        require_admin(request, db)
        item = db.get(AuctionItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Leilão não encontrado.")
        reset_relisted_public_history(db, item)
        item.status = "relisted"
        item.chat_paused = False
        item.initial_duration_seconds = clamp_initial_duration(initial_duration_minutes)
        item.scheduled_start = datetime.utcnow() + timedelta(minutes=int(start_in_minutes))
        db.commit()
        return RedirectResponse("/admin", status_code=303)
    finally:
        db.close()



@app.post("/minha-conta/saque")
def account_request_withdrawal(request: Request, amount: float = Form(...), pix_key: str = Form(...)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        if not user_is_verified(user):
            raise HTTPException(status_code=403, detail="Para solicitar saque, envie seus documentos e aguarde a confirmação da conta.")
        amount = BR(amount)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Valor inválido.")
        if user.wallet_balance < amount:
            raise HTTPException(status_code=400, detail="Saldo insuficiente para solicitar saque.")
        user.wallet_balance = BR(user.wallet_balance - amount)
        req = WithdrawalRequest(user_id=user.id, amount=amount, pix_key=pix_key.strip(), status="pending")
        db.add(req)
        db.add(WalletTransaction(user_id=user.id, amount=-amount, kind="withdrawal_request", note=f"Solicitação de saque via Pix: {pix_key.strip()}"))
        audit_event(db, request, "wallet.withdrawal_requested", user, "withdrawal", "pending", f"Valor: R$ {fmt_money(amount)}")
        db.commit()
        return RedirectResponse("/minha-conta", status_code=303)
    finally:
        db.close()


@app.post("/minha-conta/chamado")
async def account_open_ticket(request: Request, subject: str = Form(...), message: str = Form(...), order_id: int = Form(0), proof_file: UploadFile | None = File(None)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        proof_url = save_uploaded_image(proof_file)
        linked_order_id = order_id if int(order_id or 0) > 0 else None
        if linked_order_id:
            order = db.get(WinnerOrder, linked_order_id)
            if not order or order.user_id != user.id:
                raise HTTPException(status_code=403, detail="Pedido inválido.")
            if proof_url:
                db.add(OrderProof(order_id=order.id, user_id=user.id, file_url=proof_url, kind="user_proof", note=subject.strip()))
            order.status = "dispute"
        db.add(SupportTicket(user_id=user.id, order_id=linked_order_id, subject=subject.strip(), message=message.strip(), proof_url=proof_url, status="open"))
        db.commit()
        return RedirectResponse("/minha-conta", status_code=303)
    finally:
        db.close()


@app.post("/admin/user/{user_id}/verify")
def admin_verify_user(request: Request, user_id: int, note: str = Form("")):
    db = SessionLocal()
    try:
        require_admin(request, db)
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        user.identity_status = "verified"
        user.identity_note = note.strip()
        user.verified_at = datetime.utcnow()
        audit_event(db, request, "kyc.verified", user, "user", user.id, note.strip())
        db.commit()
        return RedirectResponse("/admin#admin-users", status_code=303)
    finally:
        db.close()


@app.post("/admin/user/{user_id}/reject")
def admin_reject_user(request: Request, user_id: int, note: str = Form("")):
    db = SessionLocal()
    try:
        require_admin(request, db)
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        user.identity_status = "rejected"
        user.identity_note = note.strip()
        user.verified_at = None
        db.commit()
        return RedirectResponse("/admin#admin-users", status_code=303)
    finally:
        db.close()


@app.post("/admin/withdrawal/{withdrawal_id}/set-status")
def admin_set_withdrawal_status(request: Request, withdrawal_id: int, status: str = Form(...), admin_note: str = Form("")):
    db = SessionLocal()
    try:
        require_admin(request, db)
        req = db.get(WithdrawalRequest, withdrawal_id)
        if not req:
            raise HTTPException(status_code=404, detail="Saque não encontrado.")
        if status not in {"pending", "approved", "rejected", "paid"}:
            raise HTTPException(status_code=400, detail="Status inválido.")
        if req.status == "pending" and status == "rejected":
            user = db.get(User, req.user_id)
            if user:
                user.wallet_balance = BR(user.wallet_balance + req.amount)
                db.add(WalletTransaction(user_id=user.id, amount=req.amount, kind="withdrawal_reversal", note=f"Saque #{req.id} recusado/devolvido"))
        req.status = status
        req.admin_note = admin_note.strip()
        req.updated_at = datetime.utcnow()
        db.commit()
        return RedirectResponse("/admin#admin-cashflow", status_code=303)
    finally:
        db.close()


@app.post("/admin/order/{order_id}/extend-payment")
def admin_extend_payment(request: Request, order_id: int, extra_minutes: int = Form(10)):
    db = SessionLocal()
    try:
        require_admin(request, db)
        order = db.get(WinnerOrder, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        base = order.payment_deadline if order.payment_deadline and order.payment_deadline > datetime.utcnow() else datetime.utcnow()
        order.payment_deadline = base + timedelta(minutes=max(1, int(extra_minutes)))
        order.status = "pending_payment"
        db.commit()
        return RedirectResponse("/admin#admin-pending-payments", status_code=303)
    finally:
        db.close()


@app.post("/admin/order/{order_id}/refund")
def admin_refund_order(request: Request, order_id: int, amount: float = Form(...), admin_note: str = Form("")):
    db = SessionLocal()
    try:
        require_admin(request, db)
        order = db.get(WinnerOrder, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        amount = BR(amount)
        if amount <= 0 or amount > BR(order.final_price or 0.0):
            raise HTTPException(status_code=400, detail="Valor de estorno inválido.")
        user = db.get(User, order.user_id)
        if user:
            user.wallet_balance = BR(user.wallet_balance + amount)
            db.add(WalletTransaction(user_id=user.id, amount=amount, kind="refund", note=f"Estorno pedido #{order.id}: {admin_note.strip()}"))
        order.status = "resolved"
        order.admin_note = (order.admin_note or "") + f"\nEstorno de R$ {fmt_money(amount)} registrado. {admin_note.strip()}"
        db.commit()
        return RedirectResponse("/admin#admin-search-orders", status_code=303)
    finally:
        db.close()


@app.post("/admin/ticket/{ticket_id}/set-status")
def admin_set_ticket_status(request: Request, ticket_id: int, status: str = Form(...), admin_note: str = Form("")):
    db = SessionLocal()
    try:
        require_admin(request, db)
        ticket = db.get(SupportTicket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Chamado não encontrado.")
        if status not in {"open", "in_review", "dispute", "resolved", "closed"}:
            raise HTTPException(status_code=400, detail="Status inválido.")
        ticket.status = status
        ticket.admin_note = admin_note.strip()
        ticket.updated_at = datetime.utcnow()
        if ticket.order_id:
            order = db.get(WinnerOrder, ticket.order_id)
            if order and status in {"dispute", "resolved"}:
                order.status = status
        db.commit()
        return RedirectResponse("/admin#admin-search-orders", status_code=303)
    finally:
        db.close()


@app.websocket("/ws/auction/{auction_id}")
async def auction_socket(websocket: WebSocket, auction_id: int):
    await manager.connect(auction_id, websocket)
    db = SessionLocal()
    try:
        item = db.get(AuctionItem, auction_id)
        if item:
            if start_auction_if_due(item):
                db.commit()
                item = db.get(AuctionItem, auction_id)
            await websocket.send_json({"type": "auction_update", "auction": public_auction_live_payload(item, db)})
    finally:
        db.close()

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(auction_id, websocket)
