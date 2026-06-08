from __future__ import annotations

import asyncio
import math
import mimetypes
import os
import re
import secrets
import shutil
import threading
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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
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
from sqlalchemy.exc import IntegrityError


APP_NAME = "Lanceio Certo"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./lanceiocerto.db")
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))
ALLOWED_UPLOAD_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "application/pdf",
}
ALLOWED_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"}


if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


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
    email_verification_code: Mapped[str] = mapped_column(String(12), default="")
    email_verification_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    password: Mapped[str] = mapped_column(String(120))
    cpf: Mapped[str] = mapped_column(String(20), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    gender: Mapped[str] = mapped_column(String(30), default="")
    birth_date: Mapped[str] = mapped_column(String(20), default="")
    cep: Mapped[str] = mapped_column(String(20), default="")
    street: Mapped[str] = mapped_column(String(150), default="")
    number: Mapped[str] = mapped_column(String(30), default="")
    complement: Mapped[str] = mapped_column(String(100), default="")
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
    document_back_file_url: Mapped[str] = mapped_column(String(600), default="")
    selfie_file_url: Mapped[str] = mapped_column(String(600), default="")
    residence_proof_file_url: Mapped[str] = mapped_column(String(600), default="")
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
    bids_count_cached: Mapped[int] = mapped_column(Integer, default=0)
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
    client_bid_id: Mapped[str] = mapped_column(String(80), default="", index=True)
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


class ProductSuggestionNomination(Base):
    __tablename__ = "product_suggestion_nominations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    product_key: Mapped[str] = mapped_column(String(100), index=True)
    product_name: Mapped[str] = mapped_column(String(180), default="")
    category: Mapped[str] = mapped_column(String(80), default="")
    price_level: Mapped[str] = mapped_column(String(40), default="")
    week_start: Mapped[datetime] = mapped_column(DateTime, index=True)
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
        # Broadcast não pode travar lance. Envia para todos em paralelo e corta
        # conexões lentas/travadas rapidamente. Antes, um websocket ruim podia
        # segurar atualizações em sequência.
        sockets = list(self.connections.get(auction_id, []))
        if not sockets:
            return

        async def _send(ws: WebSocket) -> tuple[WebSocket, bool]:
            try:
                await asyncio.wait_for(ws.send_json(payload), timeout=1.2)
                return ws, True
            except Exception:
                return ws, False

        results = await asyncio.gather(*(_send(ws) for ws in sockets), return_exceptions=True)
        for result in results:
            if isinstance(result, tuple):
                ws, ok = result
                if not ok:
                    self.disconnect(auction_id, ws)


app = FastAPI(title=APP_NAME)
app.add_middleware(GZipMiddleware, minimum_size=1000)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
manager = ConnectionManager()
AUCTION_BID_LOCKS: dict[int, threading.Lock] = defaultdict(threading.Lock)
SUGGESTION_WEEK_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)
SUGGESTION_USER_VOTE_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)
WITHDRAWAL_USER_LOCKS: dict[int, threading.Lock] = defaultdict(threading.Lock)
BID_COOLDOWN_MEMORY: dict[str, datetime] = {}


class AuctionStateHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str, auction_payload: Optional[dict] = None, retry_after: Optional[int] = None):
        super().__init__(status_code=status_code, detail=detail)
        self.auction_payload = auction_payload
        self.retry_after = retry_after



@app.middleware("http")
async def navigation_cache_and_diagnostics(request: Request, call_next):
    """Cache seguro + diagnóstico de navegação.

    O problema de navegação duplicada não vinha mais do nosso app.js. Alguns
    navegadores/extensões/proxies podem disparar requisições especulativas
    (prefetch/prerender) ou repetir navegações muito próximas. Este middleware:
    - bloqueia requisições especulativas antes de bater nas rotas pesadas;
    - aplica cache PRIVATE curto em páginas HTML navegáveis;
    - registra tempo real e cabeçalhos úteis para confirmar a origem.
    """
    import time

    path = request.url.path
    method = request.method.upper()
    purpose = (
        request.headers.get("purpose")
        or request.headers.get("sec-purpose")
        or request.headers.get("x-purpose")
        or request.headers.get("x-moz")
        or ""
    ).lower()
    sec_fetch_mode = (request.headers.get("sec-fetch-mode") or "").lower()
    sec_fetch_dest = (request.headers.get("sec-fetch-dest") or "").lower()
    user_agent = (request.headers.get("user-agent") or "")[:80]

    # Corta carregamentos especulativos. Uma navegação real nunca depende destes
    # headers. Isso evita que /admin, /minha-conta e /login sejam montadas sem clique.
    if method == "GET" and ("prefetch" in purpose or "prerender" in purpose):
        print(f"[NAV-SKIP] {method} {path} speculative=1 purpose={purpose} mode={sec_fetch_mode} dest={sec_fetch_dest}")
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000

    if path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=604800, stale-while-revalidate=86400")
    elif method == "GET" and response.status_code == 200:
        content_type = (response.headers.get("content-type") or "").lower()
        if "text/html" in content_type:
            # Cache privado: só no navegador do próprio usuário. Não é cache público
            # nem compartilhado. Ajuda muito nos cliques repetidos/back-forward.
            if path == "/":
                response.headers.setdefault("Cache-Control", "private, max-age=8, stale-while-revalidate=20")
            elif path.startswith("/minha-conta"):
                response.headers.setdefault("Cache-Control", "private, max-age=5, stale-while-revalidate=15")
            elif path.startswith("/admin"):
                response.headers.setdefault("Cache-Control", "private, max-age=3, stale-while-revalidate=10")
            elif path == "/login":
                response.headers.setdefault("Cache-Control", "private, max-age=10")

    if method in {"GET", "POST"} and path in {"/", "/login", "/minha-conta", "/admin"}:
        print(
            f"[NAV-REQ] {method} {request.url.path}"
            f"{('?' + request.url.query) if request.url.query else ''} "
            f"status={response.status_code} total={elapsed_ms:.1f}ms "
            f"purpose={purpose or '-'} mode={sec_fetch_mode or '-'} dest={sec_fetch_dest or '-'} "
            f"ua={user_agent}"
        )

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

# Cooldown visual e operacional após cada lance.
# Regra atual: modo normal 20s; qualquer modo turbo 30s.
# O cooldown é aplicado para todos os botões do usuário naquele leilão, evitando clique em sequência.
BID_COOLDOWN_NORMAL_SECONDS = 20
TURBO_BUTTON_COOLDOWN_SECONDS = {
    2: 30,
    3: 30,
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
ENABLE_CASHBACK_DRAW = os.getenv("ENABLE_CASHBACK_DRAW", "1").strip().lower() not in {"0", "false", "no", "off"}
DEFAULT_BID_FEE_PERCENT = 10.0
PLATFORM_PROFIT_PERCENT = 10.0


SUGGESTION_WEEK_LIMIT = 20

# Catálogo público usado para indicação. A lista é local para evitar depender de
# scraping/API externa em tempo real. Pode ser ampliada depois com importação de CSV
# ou integração oficial de marketplace.
PRODUCT_SUGGESTION_CATALOG = [
    {"key": "fone_bluetooth", "name": "Fone Bluetooth", "category": "Áudio", "price_level": "Baixo"},
    {"key": "caixa_som_bluetooth", "name": "Caixa de Som Bluetooth", "category": "Áudio", "price_level": "Baixo/Médio"},
    {"key": "soundbar", "name": "Soundbar", "category": "Áudio", "price_level": "Alto"},
    {"key": "microfone_usb", "name": "Microfone USB", "category": "Áudio", "price_level": "Médio"},
    {"key": "headset_gamer", "name": "Headset Gamer", "category": "Áudio", "price_level": "Médio"},
    {"key": "mouse_gamer", "name": "Mouse Gamer", "category": "Informática", "price_level": "Baixo"},
    {"key": "teclado_mecanico", "name": "Teclado Mecânico", "category": "Informática", "price_level": "Baixo/Médio"},
    {"key": "monitor_24", "name": "Monitor 24 polegadas", "category": "Informática", "price_level": "Médio"},
    {"key": "notebook", "name": "Notebook", "category": "Informática", "price_level": "Alto"},
    {"key": "ssd_externo", "name": "SSD Externo", "category": "Informática", "price_level": "Médio"},
    {"key": "webcam_fullhd", "name": "Webcam Full HD", "category": "Informática", "price_level": "Médio"},
    {"key": "impressora_multifuncional", "name": "Impressora Multifuncional", "category": "Informática", "price_level": "Médio"},
    {"key": "carregador_portatil", "name": "Carregador Portátil", "category": "Acessórios", "price_level": "Baixo/Médio"},
    {"key": "smartwatch", "name": "Smartwatch", "category": "Wearable", "price_level": "Médio"},
    {"key": "echo_dot_alexa", "name": "Echo Dot / Alexa", "category": "Casa inteligente", "price_level": "Médio"},
    {"key": "lampada_inteligente", "name": "Lâmpada Inteligente", "category": "Casa inteligente", "price_level": "Baixo"},
    {"key": "camera_wifi", "name": "Câmera Wi-Fi", "category": "Casa inteligente", "price_level": "Médio"},
    {"key": "controle_gamer", "name": "Controle Gamer", "category": "Games", "price_level": "Médio"},
    {"key": "nintendo_switch", "name": "Nintendo Switch", "category": "Games", "price_level": "Médio/Alto"},
    {"key": "playstation_4", "name": "PlayStation 4", "category": "Games", "price_level": "Médio/Alto"},
    {"key": "xbox_series_s", "name": "Xbox Series S", "category": "Games", "price_level": "Médio/Alto"},
    {"key": "kindle", "name": "Kindle", "category": "Leitura", "price_level": "Médio"},
    {"key": "tablet", "name": "Tablet", "category": "Eletrônicos", "price_level": "Médio"},
    {"key": "celular_android", "name": "Celular Android", "category": "Smartphone", "price_level": "Médio/Alto"},
    {"key": "iphone", "name": "iPhone", "category": "Smartphone", "price_level": "Alto"},
    {"key": "tv_43", "name": "TV 43 polegadas", "category": "TV", "price_level": "Alto"},
    {"key": "tv_50", "name": "TV 50 polegadas", "category": "TV", "price_level": "Alto"},
    {"key": "air_fryer", "name": "Air Fryer", "category": "Cozinha", "price_level": "Médio"},
    {"key": "cafeteira_capsula", "name": "Cafeteira de Cápsula", "category": "Cozinha", "price_level": "Médio"},
    {"key": "liquidificador", "name": "Liquidificador", "category": "Cozinha", "price_level": "Baixo/Médio"},
    {"key": "batedeira", "name": "Batedeira", "category": "Cozinha", "price_level": "Baixo/Médio"},
    {"key": "panela_eletrica", "name": "Panela Elétrica", "category": "Cozinha", "price_level": "Médio"},
    {"key": "jogo_panelas", "name": "Jogo de Panelas", "category": "Cozinha", "price_level": "Médio"},
    {"key": "faqueiro", "name": "Faqueiro", "category": "Cozinha", "price_level": "Baixo/Médio"},
    {"key": "aspirador_robo", "name": "Aspirador Robô", "category": "Eletrodomésticos", "price_level": "Alto"},
    {"key": "aspirador_po", "name": "Aspirador de Pó", "category": "Eletrodomésticos", "price_level": "Médio"},
    {"key": "ventilador", "name": "Ventilador", "category": "Eletrodomésticos", "price_level": "Baixo/Médio"},
    {"key": "climatizador", "name": "Climatizador", "category": "Eletrodomésticos", "price_level": "Médio"},
    {"key": "microondas", "name": "Micro-ondas", "category": "Eletrodomésticos", "price_level": "Médio/Alto"},
    {"key": "lavadora_alta_pressao", "name": "Lavadora de Alta Pressão", "category": "Casa e Jardim", "price_level": "Médio/Alto"},
    {"key": "furadeira_parafusadeira", "name": "Furadeira/Parafusadeira", "category": "Ferramentas", "price_level": "Médio"},
    {"key": "kit_ferramentas", "name": "Kit de Ferramentas", "category": "Ferramentas", "price_level": "Baixo/Médio"},
    {"key": "mala_viagem", "name": "Mala de Viagem", "category": "Viagem", "price_level": "Médio"},
    {"key": "cadeira_gamer", "name": "Cadeira Gamer", "category": "Móveis", "price_level": "Médio/Alto"},
    {"key": "cadeira_escritorio", "name": "Cadeira de Escritório", "category": "Móveis", "price_level": "Médio"},
    {"key": "bicicleta", "name": "Bicicleta", "category": "Esporte", "price_level": "Médio/Alto"},
    {"key": "patinete_eletrico", "name": "Patinete Elétrico", "category": "Esporte", "price_level": "Alto"},
    {"key": "kit_musculacao", "name": "Kit Musculação", "category": "Esporte", "price_level": "Médio"},
    {"key": "mochila_notebook", "name": "Mochila para Notebook", "category": "Acessórios", "price_level": "Baixo/Médio"},
    {"key": "maquina_cortar_cabelo", "name": "Máquina de Cortar Cabelo", "category": "Beleza", "price_level": "Baixo/Médio"},
    {"key": "secador_cabelo", "name": "Secador de Cabelo", "category": "Beleza", "price_level": "Médio"},
    {"key": "escova_secadora", "name": "Escova Secadora", "category": "Beleza", "price_level": "Médio"},
]

# Compatibilidade com templates antigos. Agora a votação usa as indicações semanais,
# não a lista fixa completa.
SUGGESTION_PRODUCTS = PRODUCT_SUGGESTION_CATALOG

# Caches leves para navegação pública/admin. Não guardam saldo nem dados sensíveis.
# Servem para evitar recalcular blocos pesados em todo clique de navegação.
SUGGESTION_STATS_CACHE: dict[str, object] = {"expires_at": None, "value": []}
# Cache curto de navegação. Mantém a plataforma responsiva sem guardar páginas inteiras
# com saldo/sessão. Os blocos cacheados são dados já serializados ou resumos públicos.
NAV_CACHE: dict[str, dict[str, object]] = {}
HOME_SYNC_LAST_AT: Optional[datetime] = None
HOME_SYNC_INTERVAL_SECONDS = 15


def nav_cache_get(key: str):
    item = NAV_CACHE.get(key)
    if not item:
        return None
    expires_at = item.get("expires_at")
    if isinstance(expires_at, datetime) and expires_at > datetime.utcnow():
        return item.get("value")
    NAV_CACHE.pop(key, None)
    return None


def nav_cache_set(key: str, value, ttl_seconds: int = 8):
    NAV_CACHE[key] = {"value": value, "expires_at": datetime.utcnow() + timedelta(seconds=ttl_seconds)}
    return value


def nav_cache_clear(prefix: str | None = None) -> None:
    if not prefix:
        NAV_CACHE.clear()
        return
    for key in list(NAV_CACHE.keys()):
        if key.startswith(prefix):
            NAV_CACHE.pop(key, None)


def BR(v: float) -> float:
    return float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def split_bid_amount(bid_value: float, fee_percent: float | None = None) -> tuple[float, float]:
    """Separa o lance bruto entre taxa da plataforma e aumento real do preço.

    Exemplo com taxa de 10%:
    - Lance R$ 0,10 -> taxa R$ 0,01 e preço sobe R$ 0,09.
    - O total bruto do lance continua sendo R$ 0,10 para progresso/controle.
    """
    gross = Decimal(str(BR(bid_value)))
    pct = Decimal(str(fee_percent if fee_percent is not None else DEFAULT_BID_FEE_PERCENT))
    if pct < 0:
        pct = Decimal("0")
    if pct > 100:
        pct = Decimal("100")
    fee = (gross * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    increment = (gross - fee).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(fee), float(increment)


def fmt_money(v: float) -> str:
    return f"{BR(v):.2f}".replace(".", ",")


def br_time(dt: Optional[datetime]) -> Optional[datetime]:
    """Converte datas gravadas em UTC para horário de Brasília na exibição."""
    if not dt:
        return None
    return dt - timedelta(hours=3)


def fmt_br_datetime(dt: Optional[datetime]) -> str:
    local = br_time(dt)
    return local.strftime("%d/%m/%Y %H:%M") if local else "—"


def platform_product_outgoing_exists(db: Session, order_id: int) -> bool:
    marker = f"Pedido #{order_id}"
    return bool(
        db.query(WalletTransaction.id)
        .filter(WalletTransaction.kind == "product_outgoing", WalletTransaction.note.like(f"%{marker}%"))
        .first()
    )


def register_product_outgoing_if_needed(db: Session, order: WinnerOrder, now: Optional[datetime] = None) -> None:
    """Registra a saída real do caixa quando o produto é comprado/enviado.

    O saldo do usuário não é alterado aqui. Esta movimentação é contábil do
    caixa operacional da plataforma, para que o admin veja a saída do produto.
    """
    if not order or platform_product_outgoing_exists(db, order.id):
        return
    item = db.get(AuctionItem, order.auction_id)
    cost = BR(getattr(item, "source_price", 0.0) or 0.0)
    if cost <= 0:
        return
    db.add(WalletTransaction(
        user_id=order.user_id,
        amount=-cost,
        kind="product_outgoing",
        note=f"Saída compra/envio do produto • Pedido #{order.id} • Leilão #{order.auction_id} • {getattr(item, 'title', '')}",
        created_at=now or datetime.utcnow(),
    ))



templates.env.globals["fmt_br_datetime"] = fmt_br_datetime
templates.env.globals["br_time"] = br_time
def public_display_status(status: str) -> str:
    """Status público do leilão.

    Internamente o banco mantém pending_payment para o vencedor pagar na área
    "Minha Conta". Publicamente, porém, o leilão já terminou e deve aparecer
    apenas como encerrado, com o vencedor.
    """
    value = (status or "").strip().lower()
    return "ended" if value == "pending_payment" else value


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


def make_email_verification_code() -> str:
    return f"{secrets.randbelow(900000) + 100000}"


def public_base_url(request: Optional[Request] = None) -> str:
    env_url = (os.getenv("PUBLIC_BASE_URL") or os.getenv("SERVER_URL") or "").strip().rstrip("/")
    if env_url:
        return env_url
    if request:
        return str(request.base_url).rstrip("/")
    return ""


def send_verification_code_email(user: User, request: Optional[Request] = None) -> bool:
    code = (getattr(user, "email_verification_code", "") or "").strip()
    if not code:
        return False
    subject = "Seu código de confirmação — Lancei o Certo"
    body = (
        f"Olá, {user.full_name}.\n\n"
        "Use o código abaixo para confirmar seu e-mail no Lancei o Certo:\n\n"
        f"Código: {code}\n\n"
        "Este código expira em 15 minutos. Se você não criou essa conta, ignore esta mensagem.\n"
    )
    smtp_host = (os.getenv("SMTP_HOST") or "").strip()
    smtp_port = int(os.getenv("SMTP_PORT") or "587")
    smtp_user = (os.getenv("SMTP_USER") or "").strip()
    smtp_password = (os.getenv("SMTP_PASSWORD") or "").strip()
    smtp_from = (os.getenv("SMTP_FROM") or smtp_user or "no-reply@lanceiocerto.com.br").strip()
    if not smtp_host or not smtp_from:
        print(f"[EMAIL CODE DEV] {user.email}: {code}")
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
        print(f"[EMAIL CODE ERROR] {user.email}: {exc} | code={code}")
        return False


def send_identity_rejection_email(user: User, note: str = "") -> bool:
    subject = "Não foi possível concluir sua verificação — Lancei o Certo"
    reason = (note or "documentação ilegível ou incompatível com os dados informados").strip()
    body = (
        f"Olá, {user.full_name}. Tudo bem?\n\n"
        "Analisamos a sua documentação e infelizmente não foi possível concluir a verificação neste momento.\n\n"
        f"Motivo: {reason}.\n\n"
        "Você pode acessar sua conta e enviar os documentos novamente com uma imagem legível, atual e compatível com os dados informados no cadastro.\n\n"
        "Equipe Lancei o Certo.\n"
    )
    smtp_host = (os.getenv("SMTP_HOST") or "").strip()
    smtp_port = int(os.getenv("SMTP_PORT") or "587")
    smtp_user = (os.getenv("SMTP_USER") or "").strip()
    smtp_password = (os.getenv("SMTP_PASSWORD") or "").strip()
    smtp_from = (os.getenv("SMTP_FROM") or smtp_user or "no-reply@lanceiocerto.com.br").strip()
    if not smtp_host or not smtp_from:
        print(f"[IDENTITY REJECTION DEV] {user.email}: {reason}")
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
        print(f"[IDENTITY REJECTION ERROR] {user.email}: {exc}")
        return False

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


def require_superadmin(request: Request, db: Session) -> User:
    user = require_admin(request, db)
    if not user.is_superadmin:
        raise HTTPException(status_code=403, detail="Acesso restrito ao super admin.")
    return user


def is_superadmin_user(user: Optional[User]) -> bool:
    return bool(user and getattr(user, "is_superadmin", False))


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
    """Valor em que o Turbo 2.0 deve começar.

    Regra financeira ajustada:
    - winner_min_percent é o percentual que o vencedor deve pagar no mínimo (ex.: 50%).
    - target_profit_percent é a margem desejada sobre o produto (ex.: 10%).
    - Como o vencedor paga novamente o preço final, a margem desejada entra pela metade no gatilho.

    Ex.: produto R$ 16,00, mínimo 50% e meta 10%:
    Turbo em 55% = R$ 8,80. O vencedor paga R$ 8,80, totalizando R$ 17,60,
    com lucro bruto de R$ 1,60 antes de somar as taxas dos lances.
    """
    source_price = float(getattr(item, "source_price", 0.0) or 0.0)
    pct = float(getattr(item, "turbo_trigger_percent", 0.0) or 0.0)
    if pct <= 0:
        pct = calculate_turbo_trigger_percent(
            getattr(item, "winner_min_percent", 50.0),
            getattr(item, "target_profit_percent", PLATFORM_PROFIT_PERCENT),
        )
    return BR(source_price * (pct / 100.0))


def turbo_trigger_amount(item: AuctionItem) -> float:
    return BR((getattr(item, "source_price", 0.0) or 0.0) * ((getattr(item, "turbo_trigger_percent", 60.0) or 60.0) / 100.0))

def auction_progress_percent(item: AuctionItem) -> float:
    if item.source_price <= 0:
        return 0.0
    return round((auction_collected_total(item) / item.source_price) * 100, 2)

def calculate_turbo_trigger_percent(winner_min_percent: float = 50.0, target_profit_percent: float = 10.0) -> float:
    """Calcula o gatilho automático do Turbo 2.0.

    Regra correta definida para o projeto:
    - O vencedor paga o preço final do leilão.
    - Então, para buscar 10% de margem sobre o produto, o gatilho não deve ser 50% + 10%,
      e sim 50% + metade da meta.

    Ex.: produto R$ 16,00, mínimo 50%, meta 10%:
    50 + (10 / 2) = 55% => Turbo em R$ 8,80, não R$ 9,60.
    """
    try:
        winner_min = float(winner_min_percent)
    except Exception:
        winner_min = 50.0
    try:
        target = float(target_profit_percent)
    except Exception:
        target = 10.0
    return max(1.0, min(95.0, winner_min + (target / 2.0)))


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


def auction_last_bid_id(db: Session, auction_id: int) -> int:
    return int(db.query(func.coalesce(func.max(Bid.id), 0)).filter(Bid.auction_id == auction_id).scalar() or 0)


def user_has_bid_in_auction(db: Session, auction_id: int, user_id: Optional[int]) -> bool:
    if not user_id:
        return False
    return db.query(Bid.id).filter(Bid.auction_id == auction_id, Bid.user_id == user_id).first() is not None


def user_can_bid_current_phase(db: Session, item: AuctionItem, user: Optional[User], turbo_level: Optional[int] = None) -> bool:
    if not user:
        return False
    level = compute_turbo_level(item) if turbo_level is None else int(turbo_level or 0)
    if level >= 2:
        return user_has_bid_in_auction(db, item.id, user.id)
    return True


def turbo_lock_message(level: int) -> str:
    return f"O modo Turbo {level}.0 é exclusivo para quem já deu lance antes da ativação."


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
    """Cooldown por botão, não global.

    O usuário pode apertar outros valores de lance sem travar a grade inteira.
    Apenas o botão usado entra em contagem, respeitando a regra do modo atual.
    """
    level = int(turbo_level or 0)
    if level >= 4:
        return TURBO_BUTTON_COOLDOWN_SECONDS[4]
    if level >= 3:
        return TURBO_BUTTON_COOLDOWN_SECONDS[3]
    if level >= 2:
        return TURBO_BUTTON_COOLDOWN_SECONDS[2]
    return int(NORMAL_BID_BUTTON_COOLDOWN_SECONDS.get(BR(bid_value), BID_COOLDOWN_NORMAL_SECONDS))


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
    item.bids_count_cached = 0
    item.turbo_level = 0
    item.winner_user_id = None
    item.winner_deadline = None
    item.ends_at = None

def start_auction_if_due(item: AuctionItem, now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    if item and item.status in {"scheduled", "relisted"} and item.scheduled_start and item.scheduled_start <= now:
        item.status = "live"
        duration = getattr(item, "initial_duration_seconds", DEFAULT_INITIAL_DURATION_SECONDS) or DEFAULT_INITIAL_DURATION_SECONDS

        # Regra importante para alinhar home e página do leilão:
        # o tempo do leilão deve contar a partir do horário agendado, não do momento
        # em que alguém abre a página. Antes, se a home mostrava "começa agora" e
        # o usuário entrava depois, a página reiniciava o tempo completo.
        logical_start = item.scheduled_start or now
        item.ends_at = logical_start + timedelta(seconds=min(MAX_INITIAL_DURATION_SECONDS, duration))

        item.chat_paused = False
        return True
    return False


def finish_auction_if_due(item: AuctionItem, db: Session, now: Optional[datetime] = None, create_side_effects: bool = True) -> bool:
    """Finaliza imediatamente um leilão live cujo relógio chegou a zero.

    Essa função é chamada pelo watcher, pelo endpoint /state e antes de aceitar lances.
    Assim o frontend não recebe um estado live vencido e não reinicia o cronômetro.
    """
    now = now or datetime.utcnow()
    if not item or item.status != "live" or not item.ends_at or item.ends_at > now:
        return False

    last_bid = (
        db.query(Bid)
        .filter(Bid.auction_id == item.id)
        .order_by(desc(Bid.created_at))
        .first()
    )

    if not last_bid:
        item.status = "ended"
        item.ends_at = None
        item.winner_user_id = None
        item.winner_deadline = None
        item.chat_paused = True
        return True

    item.status = "pending_payment"
    item.winner_user_id = last_bid.user_id
    item.winner_deadline = now + timedelta(minutes=PAYMENT_DEADLINE_MINUTES)
    item.ends_at = None
    item.chat_paused = True

    if create_side_effects:
        existing = db.query(WinnerOrder).filter(
            WinnerOrder.auction_id == item.id,
            WinnerOrder.status.in_(["pending_payment", "paid", "processing", "purchased", "sent", "delivered"]),
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
                delivery_name=getattr(winner, "full_name", "") if winner else "",
                delivery_cep=getattr(winner, "cep", "") if winner else "",
                delivery_street=getattr(winner, "street", "") if winner else "",
                delivery_number=getattr(winner, "number", "") if winner else "",
                delivery_district=getattr(winner, "district", "") if winner else "",
                delivery_city=getattr(winner, "city", "") if winner else "",
                delivery_state=getattr(winner, "state", "") if winner else "",
            )
            db.add(order)

        if ENABLE_CASHBACK_DRAW and getattr(item, "cashback_enabled", False):
            ensure_cashback_event(item, db, now)

    return True


def ensure_finished_auction_side_effects(auction_id: int) -> None:
    """Cria pedido/cashback após o fechamento visual rápido.

    O endpoint /state precisa responder imediatamente quando o cronômetro chega a
    zero. Por isso ele só muda o status e devolve o payload. Esta função roda logo
    depois em segundo plano para criar WinnerOrder e cashback sem segurar a tela.
    """
    db = SessionLocal()
    try:
        item = db.get(AuctionItem, auction_id)
        if not item or item.status not in {"pending_payment", "ended"}:
            return

        last_bid = (
            db.query(Bid)
            .filter(Bid.auction_id == auction_id)
            .order_by(desc(Bid.created_at))
            .first()
        )
        if not last_bid:
            db.commit()
            return

        if item.status == "pending_payment":
            if not item.winner_user_id:
                item.winner_user_id = last_bid.user_id
            if not item.winner_deadline:
                item.winner_deadline = datetime.utcnow() + timedelta(minutes=PAYMENT_DEADLINE_MINUTES)

            existing = db.query(WinnerOrder).filter(
                WinnerOrder.auction_id == item.id,
                WinnerOrder.status.in_(["pending_payment", "paid", "processing", "purchased", "sent", "delivered"]),
            ).first()
            if not existing:
                winner = db.get(User, last_bid.user_id)
                db.add(WinnerOrder(
                    auction_id=item.id,
                    user_id=last_bid.user_id,
                    final_price=BR(item.current_price),
                    status="pending_payment",
                    payment_deadline=item.winner_deadline,
                    pix_code=f"PIX-LANCEIOCERTO-{item.id}-{last_bid.user_id}",
                    payment_link=f"/minha-conta/pagamentos/{item.id}",
                    delivery_name=getattr(winner, "full_name", "") if winner else "",
                    delivery_cep=getattr(winner, "cep", "") if winner else "",
                    delivery_street=getattr(winner, "street", "") if winner else "",
                    delivery_number=getattr(winner, "number", "") if winner else "",
                    delivery_district=getattr(winner, "district", "") if winner else "",
                    delivery_city=getattr(winner, "city", "") if winner else "",
                    delivery_state=getattr(winner, "state", "") if winner else "",
                ))

        if ENABLE_CASHBACK_DRAW and getattr(item, "cashback_enabled", False):
            ensure_cashback_event(item, db, datetime.utcnow())

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def sync_due_auction_states(db: Session, now: Optional[datetime] = None, limit: int = 80) -> bool:
    """Sincroniza leilões vencidos/iniciados antes de montar páginas públicas.

    Sem isso, a home pode mostrar "próximo" ou um cronômetro diferente enquanto
    a página interna já muda para "ao vivo" ou encerrado. A sincronização é leve
    e limitada para não pesar no Railway.
    """
    now = now or datetime.utcnow()
    changed = False

    due_items = (
        db.query(AuctionItem)
        .filter(AuctionItem.status.in_(["scheduled", "relisted", "live"]))
        .order_by(AuctionItem.scheduled_start.asc())
        .limit(limit)
        .all()
    )

    for item in due_items:
        if start_auction_if_due(item, now):
            changed = True
        if finish_auction_if_due(item, db, now):
            changed = True

    return changed


def clamp_initial_duration(minutes: int | float | None) -> int:
    try:
        minutes_int = int(minutes or 30)
    except Exception:
        minutes_int = 30
    minutes_int = max(1, min(60, minutes_int))
    return minutes_int * 60


def public_auction_payload(item: AuctionItem, db: Session, user: Optional[User] = None) -> dict:
    # Em produtos agendados/relançados, a vitrine deve parecer uma nova disputa.
    # Lances antigos ficam fora da visualização pública e o relançamento limpa o histórico.
    if item.status in {"scheduled", "relisted"}:
        bids_count = 0
        last_bid = None
        last_bidder = None
        last_bid_id = 0
    else:
        bids_count = int(getattr(item, "bids_count_cached", 0) or 0)
        last_bid = db.query(Bid).filter(Bid.auction_id == item.id).order_by(desc(Bid.created_at)).first()
        last_bidder = public_user_name(last_bid.user) if last_bid else None
        last_bid_id = int(last_bid.id if last_bid else 0)
    remaining = 0
    if item.status == "live" and item.ends_at:
        remaining = max(0, int((item.ends_at - datetime.utcnow()).total_seconds()))
    start_remaining = 0
    if item.status in {"scheduled", "relisted"} and item.scheduled_start:
        start_remaining = max(0, int((item.scheduled_start - datetime.utcnow()).total_seconds()))
    level = compute_turbo_level(item)
    user_turbo_eligible = None
    if user is not None:
        user_turbo_eligible = user_can_bid_current_phase(db, item, user, level)
    return {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "status": public_display_status(item.status),
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
        "cashback_enabled": bool(getattr(item, "cashback_enabled", False)),
        "bid_fee_percent": getattr(item, "bid_fee_percent", DEFAULT_BID_FEE_PERCENT),
        "total_bid_fees": BR(getattr(item, "total_bid_fees", 0.0) or 0.0),
        "initial_duration_seconds": getattr(item, "initial_duration_seconds", DEFAULT_INITIAL_DURATION_SECONDS),
        "bids_count": bids_count,
        "last_bid_id": last_bid_id,
        "state_version": int(last_bid_id),
        "user_turbo_eligible": user_turbo_eligible,
        "last_bidder": last_bidder,
        "image_url": safe_image_url(item.image_url),
        "chat_paused": item.chat_paused,
        "chat_open": auction_chat_is_open(item),
        "wallet_balance": BR(getattr(user, "wallet_balance", 0.0) or 0.0) if user is not None else None,
        "cashback": cashback_payload(item, db),
    }


def public_auction_live_payload(item: AuctionItem, db: Session, *, include_cashback: bool = False, bids_count_override: Optional[int] = None, last_bidder_override: Optional[str] = None, last_bid_id_override: Optional[int] = None, user: Optional[User] = None, user_turbo_eligible_override: Optional[bool] = None) -> dict:
    """Payload leve para atualizações em tempo real do leilão.

    O endpoint de lance precisa ser rápido. O payload completo chama cashback e
    outras informações que não precisam ser recalculadas a cada clique. Este
    payload mantém todos os campos usados pelo JavaScript da tela do leilão,
    mas evita trabalho extra desnecessário.
    """
    last_bid_id = 0
    if item.status in {"scheduled", "relisted"}:
        bids_count = 0
        last_bidder = None
    else:
        if bids_count_override is None:
            bids_count = int(getattr(item, "bids_count_cached", 0) or 0)
        else:
            bids_count = int(bids_count_override or 0)

        if last_bid_id_override is not None:
            last_bid_id = int(last_bid_id_override or 0)
        else:
            last_bid_id = auction_last_bid_id(db, item.id)

        if last_bidder_override is not None:
            last_bidder = last_bidder_override
        else:
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

    level = compute_turbo_level(item)
    user_turbo_eligible = user_turbo_eligible_override
    if user_turbo_eligible is None and user is not None:
        user_turbo_eligible = user_can_bid_current_phase(db, item, user, level)
    payload = {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "status": public_display_status(item.status),
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
        "last_bid_id": last_bid_id,
        "state_version": int(last_bid_id),
        "user_turbo_eligible": user_turbo_eligible,
        "last_bidder": last_bidder,
        "image_url": safe_image_url(item.image_url),
        "chat_paused": item.chat_paused,
        "chat_open": auction_chat_is_open(item),
        "wallet_balance": BR(getattr(user, "wallet_balance", 0.0) or 0.0) if user is not None else None,
        "button_cooldown": bid_button_cooldown_seconds(0.10, level),
    }
    if include_cashback:
        payload["cashback"] = cashback_payload(item, db)
    return payload



def fast_bid_auction_payload(
    item: AuctionItem,
    *,
    bids_count: int,
    last_bidder: str,
    last_bid_id: int,
    user_turbo_eligible: Optional[bool],
    button_cooldown: int,
    mode_for_bid: int,
    mode_before_bid: int,
    bid_value: float,
    fee_value: float,
    price_increment: float,
    client_bid_id: str = "",
    wallet_balance: Optional[float] = None,
) -> dict:
    """Payload rápido para resposta de lance.

    Evita consultas extras logo depois do commit. O /state continua existindo
    para sincronização completa, mas o clique precisa voltar o mais rápido possível.
    """
    now = datetime.utcnow()
    status = (getattr(item, "status", "") or "").lower()
    remaining = 0
    if status == "live" and item.ends_at:
        remaining = max(0, int((item.ends_at - now).total_seconds()))
    start_remaining = 0
    if status in {"scheduled", "relisted"} and item.scheduled_start:
        start_remaining = max(0, int((item.scheduled_start - now).total_seconds()))

    level = compute_turbo_level(item)
    payload = {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "status": public_display_status(item.status),
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
        "bids_count": int(bids_count or 0),
        "last_bid_id": int(last_bid_id or 0),
        "state_version": int(last_bid_id or 0),
        "user_turbo_eligible": user_turbo_eligible,
        "last_bidder": last_bidder,
        "image_url": safe_image_url(item.image_url),
        "chat_paused": item.chat_paused,
        "chat_open": auction_chat_is_open(item),
        "button_cooldown": button_cooldown,
        "mode_for_bid": mode_for_bid,
        "mode_before_bid": mode_before_bid,
        "cooldown_scope": "button",
        "bid_value": bid_value,
        "fee_value": fee_value,
        "price_increment": price_increment,
        "client_bid_id": client_bid_id,
        "server_time": datetime.utcnow().isoformat(),
    }
    if wallet_balance is not None:
        payload["wallet_balance"] = BR(wallet_balance or 0.0)
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
        "status": public_display_status(item.status),
        "current_price": BR(item.current_price),
        "source_price": BR(item.source_price),
        "scheduled_start": item.scheduled_start.isoformat() if item.scheduled_start else None,
        "start_remaining": start_remaining,
        "ends_at": item.ends_at.isoformat() if item.ends_at else None,
        "remaining_seconds": remaining,
        "winner_name": public_user_name(item.winner) if item.winner else None,
        "image_url": safe_image_url(item.image_url),
    }



def user_stats(db: Session, user: User) -> dict:
    """Resumo da conta com poucas idas ao banco.

    A versão anterior fazia 5 contagens separadas. Em ambiente remoto isso
    custava mais de 1 segundo só para abrir /minha-conta. Aqui consolidamos
    os lances em uma consulta e os pedidos em outra.
    """
    bid_row = (
        db.query(
            func.count(Bid.id),
            func.count(func.distinct(Bid.auction_id)),
        )
        .filter(Bid.user_id == user.id)
        .first()
    )
    bids_total = int((bid_row[0] if bid_row else 0) or 0)
    distinct_auctions = int((bid_row[1] if bid_row else 0) or 0)

    order_rows = (
        db.query(WinnerOrder.status, func.count(WinnerOrder.id))
        .filter(WinnerOrder.user_id == user.id)
        .group_by(WinnerOrder.status)
        .all()
    )
    by_status = {status: int(total or 0) for status, total in order_rows}
    won = sum(by_status.values())
    pending = by_status.get("pending_payment", 0)
    expired = by_status.get("expired", 0)
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
    """Indicadores de caixa da plataforma.

    Regra importante:
    - Bid.bid_value é dinheiro que saiu da carteira do usuário e entrou no caixa do site.
    - Bid.fee_value é a parte desse lance que é taxa/receita da plataforma.
    - Bid.price_increment é a parte que forma o valor/preço do leilão.
    - WinnerOrder.final_price entra no caixa quando o vencedor paga.
    - product_outgoing registra a saída real quando o admin compra/envia o produto.
    """
    paid_statuses = ["paid", "processing", "purchased", "sent", "delivered"]

    total_bid_spent = _sum_scalar(db, Bid.bid_value)
    total_fees = _sum_scalar(db, Bid.fee_value)
    bid_product_cash = _sum_scalar(db, Bid.price_increment)
    total_payments = _sum_scalar(db, WinnerOrder.final_price, WinnerOrder.status.in_(paid_statuses))

    product_outgoing = abs(_sum_scalar(db, WalletTransaction.amount, WalletTransaction.kind == "product_outgoing"))
    refunds = abs(_sum_scalar(db, WalletTransaction.amount, WalletTransaction.kind == "refund"))
    paid_withdrawals = _sum_scalar(db, WithdrawalRequest.amount, WithdrawalRequest.status == "paid")
    pending_withdrawals = _sum_scalar(db, WithdrawalRequest.amount, WithdrawalRequest.status == "pending")
    user_wallet_total = _sum_scalar(db, User.wallet_balance)

    # Saídas previstas: pedidos pagos que ainda não tiveram saída de compra/envio registrada.
    recorded_order_ids = set()
    outgoing_txs = db.query(WalletTransaction.note).filter(WalletTransaction.kind == "product_outgoing").all()
    for (note,) in outgoing_txs:
        m = re.search(r"Pedido #(\d+)", note or "")
        if m:
            recorded_order_ids.add(int(m.group(1)))

    expected_products_query = (
        db.query(func.coalesce(func.sum(AuctionItem.source_price), 0.0))
        .join(WinnerOrder, WinnerOrder.auction_id == AuctionItem.id)
        .filter(WinnerOrder.status.in_(["paid", "processing", "purchased"]))
    )
    if recorded_order_ids:
        expected_products_query = expected_products_query.filter(~WinnerOrder.id.in_(recorded_order_ids))
    expected_products = BR(expected_products_query.scalar() or 0.0)

    total_income = BR(total_bid_spent + total_payments)
    total_outgoing = BR(product_outgoing + refunds + paid_withdrawals)
    expected_outgoing = BR(expected_products + pending_withdrawals)
    available_cash = BR(total_income - total_outgoing - pending_withdrawals)
    net_result = BR(total_income - total_outgoing)
    estimated_profit = BR(total_fees + total_payments + bid_product_cash - product_outgoing - refunds)

    return {
        "total_fees": total_fees,
        "total_bid_spent": total_bid_spent,
        "bid_product_cash": bid_product_cash,
        "total_payments": total_payments,
        "user_wallet_total": user_wallet_total,
        "expected_outgoing": expected_outgoing,
        "total_income": total_income,
        "total_outgoing": total_outgoing,
        "net_result": net_result,
        "estimated_profit": estimated_profit,
        "available_cash": available_cash,
        "accumulated_loss": BR(abs(estimated_profit) if estimated_profit < 0 else 0),
        "pending_withdrawals": pending_withdrawals,
        "product_outgoing": product_outgoing,
        "expected_products": expected_products,
        "paid_withdrawals": paid_withdrawals,
        "refunds": refunds,
    }


def build_cashflow_movements(db: Session) -> list[dict]:
    """Movimentações do caixa do ponto de vista da plataforma.

    WalletTransaction é originalmente extrato do usuário; aqui normalizamos o
    sinal para o caixa do site. Ex.: bid_spent é negativo para o usuário, mas
    positivo para o caixa do leilão.
    """
    rows: list[dict] = []
    transactions = db.query(WalletTransaction).order_by(desc(WalletTransaction.created_at)).limit(220).all()
    user_ids = {tx.user_id for tx in transactions if tx.user_id}
    users_by_id = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

    type_labels = {
        "bid_spent": "lance_recebido",
        "payment": "pagamento_vencedor",
        "product_outgoing": "saida_produto",
        "withdrawal_reserved": "saque_reservado",
        "withdrawal_reversal": "saque_estornado",
        "refund": "estorno",
        "manual_adjustment": "ajuste_manual",
        "deposit_pending": "deposito_pendente",
    }

    for tx in transactions:
        raw = BR(tx.amount or 0.0)
        if tx.kind in {"deposit_pending"} and abs(raw) < 0.00001:
            continue
        tx_user = users_by_id.get(tx.user_id)
        cpf = getattr(tx_user, "cpf", "—") or "—"
        name = public_user_name(tx_user) if tx_user else f"Usuário #{tx.user_id}"

        if tx.kind in {"bid_spent", "payment"}:
            amount = abs(raw)
        elif tx.kind in {"withdrawal_reversal"}:
            # Devolução de saque volta para o saldo do cliente: reduz uma saída reservada.
            amount = abs(raw)
        elif tx.kind in {"product_outgoing", "refund", "withdrawal_reserved"}:
            amount = -abs(raw)
        else:
            amount = raw

        rows.append({
            "created_at": tx.created_at,
            "type": type_labels.get(tx.kind, tx.kind),
            "description": f"{name} • CPF {cpf} • {tx.note or 'Movimentação'}",
            "amount": BR(amount),
            "balance_after": None,
            "status": "registrado",
        })

    rows.sort(key=lambda r: r.get("created_at") or datetime.min)
    running = 0.0
    for r in rows:
        running = BR(running + BR(r.get("amount") or 0.0))
        r["balance_after"] = running
    rows.sort(key=lambda r: r.get("created_at") or datetime.min, reverse=True)
    return rows[:160]


def build_auction_results(db: Session) -> list[dict]:
    rows = []
    items = db.query(AuctionItem).order_by(desc(AuctionItem.created_at)).limit(160).all()
    item_ids = [i.id for i in items]
    if not item_ids:
        return rows

    bid_aggs = {
        row.auction_id: row for row in db.query(
            Bid.auction_id.label("auction_id"),
            func.coalesce(func.sum(Bid.bid_value), 0.0).label("gross_bids"),
            func.coalesce(func.sum(Bid.fee_value), 0.0).label("fees_total"),
            func.coalesce(func.sum(Bid.price_increment), 0.0).label("product_cash"),
        ).filter(Bid.auction_id.in_(item_ids)).group_by(Bid.auction_id).all()
    }

    orders_by_auction: dict[int, WinnerOrder] = {}
    for order in db.query(WinnerOrder).filter(WinnerOrder.auction_id.in_(item_ids)).order_by(desc(WinnerOrder.created_at)).all():
        orders_by_auction.setdefault(order.auction_id, order)

    outgoing_by_order: dict[int, float] = {}
    for tx in db.query(WalletTransaction).filter(WalletTransaction.kind == "product_outgoing").all():
        m = re.search(r"Pedido #(\d+)", tx.note or "")
        if m:
            outgoing_by_order[int(m.group(1))] = BR(outgoing_by_order.get(int(m.group(1)), 0.0) + abs(tx.amount or 0.0))

    for item in items:
        agg = bid_aggs.get(item.id)
        order = orders_by_auction.get(item.id)
        source_price = BR(item.source_price or 0.0)
        gross_bids = BR(getattr(agg, "gross_bids", 0.0) if agg else 0.0)
        fees_total = BR(getattr(agg, "fees_total", 0.0) if agg else (item.total_bid_fees or 0.0))
        product_cash = BR(getattr(agg, "product_cash", 0.0) if agg else max(0.0, gross_bids - fees_total))
        final_price = BR(order.final_price if order and order.status in ["paid", "processing", "purchased", "sent", "delivered"] else 0.0)
        outgoing = BR(outgoing_by_order.get(order.id, 0.0) if order else 0.0)
        cash_total = BR(gross_bids + final_price)
        result = BR(cash_total - outgoing)
        rows.append({
            "title": item.title,
            "source_price": source_price,
            "final_price": final_price,
            "gross_bids": gross_bids,
            "fees_total": fees_total,
            "product_cash": product_cash,
            "site_complement": BR(max(0.0, source_price - cash_total)),
            "outgoing": outgoing,
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


def current_week_start_utc(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.utcnow()
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return base - timedelta(days=base.weekday())


def next_week_start_utc(now: Optional[datetime] = None) -> datetime:
    return current_week_start_utc(now) + timedelta(days=7)


def suggestion_product_by_key(product_key: str) -> Optional[dict]:
    for product in PRODUCT_SUGGESTION_CATALOG:
        if product["key"] == product_key:
            return product
    return None


def suggestion_categories() -> list[str]:
    return sorted({p["category"] for p in PRODUCT_SUGGESTION_CATALOG})


def current_week_nominations(db: Session) -> list[ProductSuggestionNomination]:
    week_start = current_week_start_utc()
    return (
        db.query(ProductSuggestionNomination)
        .options(selectinload(ProductSuggestionNomination.user))
        .filter(ProductSuggestionNomination.week_start == week_start)
        .order_by(ProductSuggestionNomination.created_at.asc())
        .all()
    )


def user_week_nomination(db: Session, user: Optional[User]) -> Optional[ProductSuggestionNomination]:
    if not user:
        return None
    week_start = current_week_start_utc()
    return (
        db.query(ProductSuggestionNomination)
        .filter(ProductSuggestionNomination.user_id == user.id, ProductSuggestionNomination.week_start == week_start)
        .order_by(desc(ProductSuggestionNomination.created_at))
        .first()
    )


def user_today_suggestion_vote(db: Session, user: Optional[User]) -> Optional[ProductSuggestionVote]:
    if not user:
        return None
    return (
        db.query(ProductSuggestionVote)
        .filter(ProductSuggestionVote.user_id == user.id, ProductSuggestionVote.created_at >= today_start_utc())
        .order_by(desc(ProductSuggestionVote.created_at))
        .first()
    )


def user_today_nomination(db: Session, user: Optional[User]) -> Optional[ProductSuggestionNomination]:
    if not user:
        return None
    return (
        db.query(ProductSuggestionNomination)
        .filter(ProductSuggestionNomination.user_id == user.id, ProductSuggestionNomination.created_at >= today_start_utc())
        .order_by(desc(ProductSuggestionNomination.created_at))
        .first()
    )


def suggestion_vote_stats(db: Session) -> list[dict]:
    """Ranking público da semana.

    Apenas produtos indicados na semana entram na votação. A lista zera
    automaticamente na segunda-feira porque as consultas usam week_start.
    """
    week_start = current_week_start_utc()
    week_end = week_start + timedelta(days=7)
    nominations = current_week_nominations(db)
    if not nominations:
        return []

    keys = [n.product_key for n in nominations]
    grouped = (
        db.query(ProductSuggestionVote.product_key, func.count(ProductSuggestionVote.id))
        .filter(ProductSuggestionVote.created_at >= week_start, ProductSuggestionVote.created_at < week_end)
        .filter(ProductSuggestionVote.product_key.in_(keys))
        .group_by(ProductSuggestionVote.product_key)
        .all()
    )
    votes_by_key = {key: int(total or 0) for key, total in grouped}
    total_votes = sum(votes_by_key.values())
    rows = []
    for n in nominations:
        votes = votes_by_key.get(n.product_key, 0)
        percent = round((votes / total_votes) * 100, 2) if total_votes else 0.0
        indicated_by = n.user.public_name or n.user.nickname or (n.user.full_name.split()[0] if n.user and n.user.full_name else "usuario")
        rows.append({
            "key": n.product_key,
            "name": n.product_name,
            "category": n.category,
            "price_level": n.price_level,
            "votes": votes,
            "percent": percent,
            "indicated_by": indicated_by,
            "created_at": n.created_at,
        })
    rows.sort(key=lambda item: (-item["votes"], item["created_at"]))
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
                "gender": "VARCHAR(30) DEFAULT ''",
                "birth_date": "VARCHAR(20) DEFAULT ''",
                "complement": "VARCHAR(100) DEFAULT ''",
                "identity_status": "VARCHAR(30) DEFAULT 'pending'",
                "identity_note": "TEXT DEFAULT ''",
                "document_type": "VARCHAR(40) DEFAULT 'CPF'",
                "document_number": "VARCHAR(40) DEFAULT ''",
                "document_file_url": "VARCHAR(600) DEFAULT ''",
                "document_back_file_url": "VARCHAR(600) DEFAULT ''",
                "selfie_file_url": "VARCHAR(600) DEFAULT ''",
                "residence_proof_file_url": "VARCHAR(600) DEFAULT ''",
                "verified_at": "TIMESTAMP NULL",
                "terms_accepted_at": "TIMESTAMP NULL",
                "privacy_accepted_at": "TIMESTAMP NULL",
                "email_verified": "BOOLEAN DEFAULT FALSE",
                "email_verified_at": "TIMESTAMP NULL",
                "email_verification_token": "VARCHAR(120) DEFAULT ''",
                "email_verification_code": "VARCHAR(12) DEFAULT ''",
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
                "bids_count_cached": "INTEGER DEFAULT 0",
            }.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE auction_items ADD COLUMN {name} {ddl}"))
            if "bids_count_cached" in {c["name"] for c in inspector.get_columns("auction_items")}:
                conn.execute(text("""
                    UPDATE auction_items
                    SET bids_count_cached = COALESCE((
                        SELECT COUNT(*) FROM bids WHERE bids.auction_id = auction_items.id
                    ), 0)
                    WHERE bids_count_cached IS NULL
                """))

        if inspector.has_table("bids"):
            cols = {c["name"] for c in inspector.get_columns("bids")}
            if "client_bid_id" not in cols:
                conn.execute(text("ALTER TABLE bids ADD COLUMN client_bid_id VARCHAR(80) DEFAULT ''"))

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
            "CREATE INDEX IF NOT EXISTS ix_bids_auction_user_created ON bids (auction_id, user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_bids_auction_user_value_created ON bids (auction_id, user_id, bid_value, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_bids_client_bid_id ON bids (client_bid_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bids_auction_user_client_bid ON bids (auction_id, user_id, client_bid_id) WHERE client_bid_id <> ''",
            "CREATE INDEX IF NOT EXISTS ix_winner_orders_user_status_created ON winner_orders (user_id, status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_winner_orders_auction_created ON winner_orders (auction_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_wallet_transactions_user_created ON wallet_transactions (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_withdrawals_user_created ON withdrawal_requests (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_support_tickets_user_created ON support_tickets (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_audit_logs_created ON audit_logs (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_suggestion_votes_key ON product_suggestion_votes (product_key)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_suggestion_vote_user_day ON product_suggestion_votes (user_id, date(created_at))",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_suggestion_nomination_week_user ON product_suggestion_nominations (week_start, user_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_suggestion_nomination_week_product ON product_suggestion_nominations (week_start, product_key)",
            "CREATE INDEX IF NOT EXISTS ix_suggestion_nomination_week ON product_suggestion_nominations (week_start)",
            "CREATE INDEX IF NOT EXISTS ix_cashback_events_auction ON cashback_events (auction_id)",
        ]:
            try:
                conn.execute(text(ddl))
            except Exception:
                pass


def save_uploaded_image(file: Optional[UploadFile]) -> str:
    """Salva uploads com validação de extensão, MIME e tamanho.

    A função é usada tanto para imagens de produto quanto para documentos de
    identidade. Por isso aceita imagens e PDF, mas bloqueia arquivos grandes ou
    extensões perigosas antes de gravar no disco.
    """
    if not file or not file.filename:
        return ""

    original_name = Path(file.filename).name
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Tipo de arquivo não permitido. Envie JPG, PNG, WEBP, GIF ou PDF.")

    declared_type = (file.content_type or mimetypes.guess_type(original_name)[0] or "").lower()
    if declared_type and declared_type not in ALLOWED_UPLOAD_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Formato de arquivo inválido para upload.")

    safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", Path(original_name).stem).strip("_") or "upload"
    final_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}_{secrets.token_hex(4)}_{safe_stem}{ext}"
    target = UPLOAD_DIR / final_name

    total = 0
    try:
        file.file.seek(0)
    except Exception:
        pass

    with target.open("wb") as out:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                out.close()
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="Arquivo muito grande. Envie um arquivo menor.")
            out.write(chunk)

    if total <= 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Arquivo vazio ou inválido.")

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
    """Atualiza transições de leilão sem varrer tabelas inteiras a cada ciclo."""
    while True:
        await asyncio.sleep(1)
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            changed_ids: set[int] = set()

            to_start = (
                db.query(AuctionItem)
                .filter(AuctionItem.status.in_(["scheduled", "relisted"]))
                .filter(AuctionItem.scheduled_start <= now)
                .order_by(AuctionItem.scheduled_start.asc())
                .limit(50)
                .all()
            )
            for item in to_start:
                if start_auction_if_due(item, now):
                    changed_ids.add(item.id)

            # Só leilões cujo prazo já venceu precisam ser finalizados. Antes o
            # watcher varria todos os leilões ao vivo a cada segundo.
            live_to_finish = (
                db.query(AuctionItem)
                .filter(AuctionItem.status == "live")
                .filter(AuctionItem.ends_at.isnot(None))
                .filter(AuctionItem.ends_at <= now)
                .order_by(AuctionItem.ends_at.asc())
                .limit(50)
                .all()
            )
            for item in live_to_finish:
                if finish_auction_if_due(item, db, now):
                    changed_ids.add(item.id)

            due_cashbacks = (
                db.query(CashbackEvent)
                .filter(CashbackEvent.status == "open")
                .filter(CashbackEvent.join_deadline <= now)
                .order_by(CashbackEvent.join_deadline.asc())
                .limit(50)
                .all()
            )
            for cashback in due_cashbacks:
                draw_cashback_if_due(cashback, db, now)

            expired_orders = (
                db.query(WinnerOrder)
                .filter(WinnerOrder.status == "pending_payment")
                .filter(WinnerOrder.payment_deadline.isnot(None))
                .filter(WinnerOrder.payment_deadline <= now)
                .order_by(WinnerOrder.payment_deadline.asc())
                .limit(50)
                .all()
            )
            for order in expired_orders:
                order.status = "expired"
                order.expired_at = now
                item = db.get(AuctionItem, order.auction_id)
                if item:
                    item.status = "ended"
                    item.winner_deadline = None
                    item.ends_at = None
                    item.chat_paused = True
                    changed_ids.add(item.id)

            if changed_ids:
                db.commit()
                for auction_id in changed_ids:
                    fresh = db.get(AuctionItem, auction_id)
                    if fresh:
                        asyncio.create_task(manager.broadcast(auction_id, {"type": "auction_update", "auction": public_auction_live_payload(fresh, db)}))
            else:
                db.rollback()
        finally:
            db.close()


def cached_suggestion_vote_stats(db: Session, ttl_seconds: int = 45) -> list[dict]:
    """Ranking público com cache curto.

    A indicação da semana não precisa ser recalculada em todo carregamento da
    Home. O cache é curto para manter a página leve sem perder atualização.
    """
    now = datetime.utcnow()
    expires_at = SUGGESTION_STATS_CACHE.get("expires_at")
    if isinstance(expires_at, datetime) and expires_at > now:
        return list(SUGGESTION_STATS_CACHE.get("value") or [])
    value = suggestion_vote_stats(db)
    SUGGESTION_STATS_CACHE["value"] = value
    SUGGESTION_STATS_CACHE["expires_at"] = now + timedelta(seconds=ttl_seconds)
    return list(value)


def should_sync_home_states() -> bool:
    global HOME_SYNC_LAST_AT
    now = datetime.utcnow()
    if HOME_SYNC_LAST_AT and (now - HOME_SYNC_LAST_AT).total_seconds() < HOME_SYNC_INTERVAL_SECONDS:
        return False
    HOME_SYNC_LAST_AT = now
    return True


def cached_home_public_context(db: Session, ttl_seconds: int = 8) -> dict:
    """Dados públicos da Home com cache curto.

    A Home era chamada várias vezes seguidas durante a navegação. Cada chamada
    repetia consultas de leilões, indicações e payloads. O usuário/saldo NÃO
    fica aqui; só blocos públicos.
    """
    cached = nav_cache_get("home:public")
    if cached is not None:
        return cached

    if should_sync_home_states() and sync_due_auction_states(db):
        db.commit()

    live_items = db.query(AuctionItem).filter(AuctionItem.status == "live").order_by(AuctionItem.created_at.desc()).limit(12).all()
    upcoming_items = db.query(AuctionItem).filter(AuctionItem.status.in_(["scheduled", "relisted"])).order_by(AuctionItem.scheduled_start.asc()).limit(12).all()
    ended_items = db.query(AuctionItem).filter(AuctionItem.status.in_(["pending_payment", "ended"])).order_by(desc(AuctionItem.created_at)).limit(12).all()
    week_nominations = current_week_nominations(db)

    return nav_cache_set("home:public", {
        "live_items": [public_auction_card_payload(x) for x in live_items],
        "upcoming_items": [public_auction_card_payload(x) for x in upcoming_items],
        "ended_items": [public_auction_card_payload(x) for x in ended_items],
        "suggestion_products": cached_suggestion_vote_stats(db),
        "suggestion_week_count": len(week_nominations),
    }, ttl_seconds)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    db = SessionLocal()
    try:
        user = current_user(request, db)

        public_home = cached_home_public_context(db)

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "user": user,
                "live_items": public_home["live_items"],
                "upcoming_items": public_home["upcoming_items"],
                "ended_items": public_home["ended_items"],
                "suggestion_products": public_home["suggestion_products"],
                "suggestion_catalog": PRODUCT_SUGGESTION_CATALOG,
                "suggestion_categories": suggestion_categories(),
                "suggestion_week_limit": SUGGESTION_WEEK_LIMIT,
                "suggestion_week_count": public_home["suggestion_week_count"],
                "user_week_nomination": user_week_nomination(db, user),
                "today_suggestion_vote": user_today_suggestion_vote(db, user),
                "today_suggestion_nomination": user_today_nomination(db, user),
                "fee_percent": "1%",
            },
        )
    finally:
        db.close()


@app.post("/indicacao/indicar")
def nominate_product_suggestion(request: Request, product_key: str = Form(...)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        product = suggestion_product_by_key(product_key)
        if not product:
            raise HTTPException(status_code=400, detail="Produto inválido para indicação.")

        week_start = current_week_start_utc()
        lock_key = week_start.isoformat()

        # Trava de aplicação + índices únicos no banco. A trava evita passar do limite
        # de 20 indicações quando várias pessoas clicam no mesmo segundo; os índices
        # únicos impedem produto repetido e segunda indicação do mesmo usuário.
        with SUGGESTION_WEEK_LOCKS[lock_key]:
            if user_week_nomination(db, user):
                return RedirectResponse("/?indicacao=ja-indicou", status_code=303)

            current_count = db.query(ProductSuggestionNomination).filter(ProductSuggestionNomination.week_start == week_start).count()
            if current_count >= SUGGESTION_WEEK_LIMIT:
                return RedirectResponse("/?indicacao=lista-cheia", status_code=303)

            duplicate = (
                db.query(ProductSuggestionNomination)
                .filter(ProductSuggestionNomination.week_start == week_start, ProductSuggestionNomination.product_key == product_key)
                .first()
            )
            if duplicate:
                return RedirectResponse("/?indicacao=repetido", status_code=303)

            db.add(ProductSuggestionNomination(
                user_id=user.id,
                product_key=product["key"],
                product_name=product["name"],
                category=product["category"],
                price_level=product["price_level"],
                week_start=week_start,
            ))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                # Se duas requisições empatarem, o banco decide. Aqui só traduzimos
                # para uma resposta amigável sem deixar duplicar.
                if user_week_nomination(db, user):
                    return RedirectResponse("/?indicacao=ja-indicou", status_code=303)
                return RedirectResponse("/?indicacao=repetido", status_code=303)

        return RedirectResponse("/?indicacao=indicou", status_code=303)
    finally:
        db.close()

@app.post("/indicacao/votar")
def vote_product_suggestion(request: Request, product_key: str = Form(...)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        week_start = current_week_start_utc()
        today_key = f"{user.id}:{datetime.utcnow().date().isoformat()}"

        # Trava por usuário/dia para impedir dois votos simultâneos em abas diferentes.
        # O índice único diário é criado em ensure_columns() como segunda camada.
        with SUGGESTION_USER_VOTE_LOCKS[today_key]:
            nominated = (
                db.query(ProductSuggestionNomination)
                .filter(ProductSuggestionNomination.week_start == week_start, ProductSuggestionNomination.product_key == product_key)
                .first()
            )
            if not nominated:
                raise HTTPException(status_code=400, detail="Produto não está na votação desta semana.")

            if user_today_suggestion_vote(db, user):
                return RedirectResponse("/?indicacao=ja-votou", status_code=303)

            if user_today_nomination(db, user):
                return RedirectResponse("/?indicacao=indicou-hoje", status_code=303)

            db.add(ProductSuggestionVote(user_id=user.id, product_key=product_key))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return RedirectResponse("/?indicacao=ja-votou", status_code=303)

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
    gender: str = Form(""),
    birth_date: str = Form(""),
    password: str = Form(...),
    password_confirm: str = Form(...),
    cep: str = Form(""),
    street: str = Form(""),
    number: str = Form(""),
    complement: str = Form(""),
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
        clean_gender = (gender or "").strip()[:30]
        clean_birth_date = (birth_date or "").strip()[:20]
        if not clean_gender:
            return fail("Informe o gênero.")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", clean_birth_date):
            return fail("Informe a data de nascimento.")
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
        code = make_email_verification_code()
        user = User(
            full_name=full_name.strip(),
            public_name=clean_public_name,
            nickname=clean_public_name,
            email=clean_email,
            email_verified=False,
            email_verification_token=token,
            email_verification_code=code,
            email_verification_expires_at=datetime.utcnow() + timedelta(minutes=15),
            password=hash_password(password.strip()),
            cpf=clean_cpf,
            phone=clean_phone,
            gender=clean_gender,
            birth_date=clean_birth_date,
            cep=only_digits(cep),
            street=street.strip(),
            number=number.strip(),
            complement=complement.strip(),
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
        sent = send_verification_code_email(user, request)
        audit_event(db, request, "user.register", user, "user", user.id, "Cadastro criado. Código de e-mail enviado e KYC pendente.")
        db.commit()
        suffix = "&email_sent=1" if sent else "&email_dev=1"
        return RedirectResponse(f"/cadastro/confirmar-email?email={clean_email}{suffix}", status_code=303)
    finally:
        db.close()


@app.get("/cadastro/confirmar-email", response_class=HTMLResponse)
def register_confirm_email_page(request: Request, email: str = "", email_sent: int = 0, email_dev: int = 0):
    return templates.TemplateResponse("register_email_confirm.html", {"request": request, "email": normalize_email(email), "error": None, "email_sent": email_sent, "email_dev": email_dev})


@app.post("/cadastro/confirmar-email")
def register_confirm_email_submit(request: Request, email: str = Form(...), code: str = Form(...)):
    db = SessionLocal()
    try:
        clean_email = normalize_email(email)
        clean_code = only_digits(code)
        user = db.query(User).filter(User.email == clean_email).first()
        if not user:
            return templates.TemplateResponse("register_email_confirm.html", {"request": request, "email": clean_email, "error": "Conta não encontrada.", "email_sent": 0, "email_dev": 0}, status_code=400)
        if user.email_verified:
            token = secrets.token_urlsafe(24)
            SESSIONS[token] = user.id
            response = RedirectResponse("/cadastro/documentos", status_code=303)
            response.set_cookie("session_token", token, httponly=True, samesite="lax")
            return response
        if not clean_code or clean_code != (user.email_verification_code or ""):
            return templates.TemplateResponse("register_email_confirm.html", {"request": request, "email": clean_email, "error": "Código inválido.", "email_sent": 0, "email_dev": 0}, status_code=400)
        if user.email_verification_expires_at and user.email_verification_expires_at < datetime.utcnow():
            user.email_verification_code = make_email_verification_code()
            user.email_verification_expires_at = datetime.utcnow() + timedelta(minutes=15)
            send_verification_code_email(user, request)
            db.commit()
            return templates.TemplateResponse("register_email_confirm.html", {"request": request, "email": clean_email, "error": "O código expirou. Enviamos um novo código para seu e-mail.", "email_sent": 1, "email_dev": 0}, status_code=400)
        user.email_verified = True
        user.email_verified_at = datetime.utcnow()
        user.email_verification_code = ""
        user.email_verification_token = ""
        user.email_verification_expires_at = None
        audit_event(db, request, "user.email_code_verified", user, "user", user.id, "E-mail confirmado por código.")
        db.commit()
        token = secrets.token_urlsafe(24)
        SESSIONS[token] = user.id
        response = RedirectResponse("/cadastro/documentos", status_code=303)
        response.set_cookie("session_token", token, httponly=True, samesite="lax")
        return response
    finally:
        db.close()


@app.post("/cadastro/reenviar-codigo")
def register_resend_code(request: Request, email: str = Form(...)):
    db = SessionLocal()
    try:
        clean_email = normalize_email(email)
        user = db.query(User).filter(User.email == clean_email).first()
        if user and not user.email_verified:
            user.email_verification_code = make_email_verification_code()
            user.email_verification_expires_at = datetime.utcnow() + timedelta(minutes=15)
            send_verification_code_email(user, request)
            db.commit()
        return RedirectResponse(f"/cadastro/confirmar-email?email={clean_email}&email_sent=1", status_code=303)
    finally:
        db.close()


@app.get("/cadastro/documentos", response_class=HTMLResponse)
def register_documents_page(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        return templates.TemplateResponse("register_documents.html", {"request": request, "user": user, "error": None, "success": None})
    finally:
        db.close()


@app.post("/cadastro/documentos")
async def register_documents_submit(request: Request, document_front_file: UploadFile | None = File(None), document_back_file: UploadFile | None = File(None), selfie_file: UploadFile | None = File(None), residence_proof_file: UploadFile | None = File(None)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        if not document_front_file or not document_front_file.filename or not document_back_file or not document_back_file.filename or not selfie_file or not selfie_file.filename:
            return templates.TemplateResponse("register_documents.html", {"request": request, "user": user, "error": "Envie a frente do documento, o verso e uma selfie atual.", "success": None}, status_code=400)
        user.document_file_url = save_uploaded_image(document_front_file)
        user.document_back_file_url = save_uploaded_image(document_back_file)
        user.selfie_file_url = save_uploaded_image(selfie_file)
        if residence_proof_file and residence_proof_file.filename:
            user.residence_proof_file_url = save_uploaded_image(residence_proof_file)
        user.identity_status = "pending"
        user.identity_note = "Documentos enviados. Aguardando análise do administrador."
        audit_event(db, request, "user.identity_submitted", user, "user", user.id, "Documentos enviados no cadastro inicial.")
        db.commit()
        return RedirectResponse("/minha-conta?cadastro=concluido", status_code=303)
    finally:
        db.close()


@app.post("/cadastro/documentos/enviar-depois")
def register_documents_skip(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        if user.identity_status not in {"verified", "pending"}:
            user.identity_status = "pending"
        if not user.identity_note:
            user.identity_note = "Documentos serão enviados depois."
        db.commit()
        return RedirectResponse("/minha-conta?cadastro=concluido", status_code=303)
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
    wants_json = (
        request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
        or "application/json" in request.headers.get("accept", "").lower()
    )
    try:
        identifier = (login_identifier or "").strip().lower()
        cpf_digits = re.sub(r"\D", "", identifier)

        if "@" in identifier:
            user = db.query(User).filter(User.email == identifier).first()
        else:
            user = db.query(User).filter(User.cpf == cpf_digits).first()

        if not user or not verify_password(password.strip(), user.password):
            if wants_json:
                return JSONResponse({"ok": False, "detail": "E-mail/CPF ou senha inválidos."}, status_code=401)
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "E-mail/CPF ou senha inválidos.",
                "login_identifier": login_identifier,
            })

        token = secrets.token_urlsafe(24)
        SESSIONS[token] = user.id

        if wants_json:
            response = JSONResponse({
                "ok": True,
                "redirect_url": "/minha-conta",
                "user": {
                    "id": user.id,
                    "name": public_user_name(user),
                    "wallet_balance": BR(user.wallet_balance or 0.0),
                    "is_admin": bool(user.is_admin),
                },
            })
        else:
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

        now = datetime.utcnow()
        changed = start_auction_if_due(item, now)
        changed = finish_auction_if_due(item, db, now) or changed
        if changed:
            db.commit()
            db.refresh(item)

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
                "item": {**public_auction_payload(item, db, user), "cashback": cashback_payload(item, db, user)},
                "entity": item,
                "chat_messages": messages,
                "allowed_bids": sorted(ALLOWED_BIDS),
                "fee_percent": "1%",
            },
        )
    finally:
        db.close()


def _normalize_client_bid_id(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"[^a-zA-Z0-9_.:-]", "", value)
    return value[:80]


def _bid_cooldown_key(auction_id: int, user_id: int, bid_value: float) -> str:
    return f"{auction_id}:{user_id}:{BR(bid_value):.2f}"


def _get_fast_cooldown_remaining(auction_id: int, user_id: int, bid_value: float, now: datetime) -> int:
    key = _bid_cooldown_key(auction_id, user_id, bid_value)
    until = BID_COOLDOWN_MEMORY.get(key)
    if not until:
        return 0
    remaining = math.ceil((until - now).total_seconds())
    if remaining <= 0:
        BID_COOLDOWN_MEMORY.pop(key, None)
        return 0
    return remaining


def _set_fast_cooldown(auction_id: int, user_id: int, bid_value: float, seconds: int, now: datetime) -> None:
    if seconds <= 0:
        return
    BID_COOLDOWN_MEMORY[_bid_cooldown_key(auction_id, user_id, bid_value)] = now + timedelta(seconds=seconds)


def _place_bid_sync(request: Request, auction_id: int, bid_value: float, client_bid_id: str = "") -> tuple[dict, dict, int]:
    """Processa o lance com proteção real contra duplicidade.

    Camadas de segurança:
    - lock por leilão para serializar preço/tempo;
    - client_bid_id para idempotência do mesmo clique;
    - índice único no banco para impedir duplicidade mesmo sob corrida;
    - payload privado para quem clicou e payload público separado para websocket.
    """
    button_cooldown = 0
    client_bid_id = _normalize_client_bid_id(client_bid_id)

    with AUCTION_BID_LOCKS[auction_id]:
        db = SessionLocal()
        try:
            user = require_user(request, db)
            # Trava a linha do leilão no PostgreSQL durante o processamento do lance.
            # O lock em memória protege dentro de uma réplica; o FOR UPDATE protege
            # caso Railway rode mais de um processo/réplica.
            if DATABASE_URL.startswith("sqlite"):
                item = db.get(AuctionItem, auction_id)
            else:
                item = (
                    db.query(AuctionItem)
                    .filter(AuctionItem.id == auction_id)
                    .with_for_update()
                    .first()
                )
            if not item:
                raise HTTPException(status_code=404, detail="Leilão não encontrado.")

            now = datetime.utcnow()
            if start_auction_if_due(item, now):
                db.flush()

            if finish_auction_if_due(item, db, now):
                db.commit()
                db.refresh(item)
                private_payload = public_auction_live_payload(item, db, include_cashback=True, user=user)
                public_payload = public_auction_live_payload(item, db, include_cashback=True, user_turbo_eligible_override=None)
                raise AuctionStateHTTPException(
                    status_code=400,
                    detail="Este leilão foi encerrado.",
                    auction_payload=private_payload,
                )

            if item.status != "live":
                private_payload = public_auction_live_payload(item, db, user=user)
                raise AuctionStateHTTPException(status_code=400, detail="Leilão não está ao vivo.", auction_payload=private_payload)

            bid_value = BR(bid_value)
            if bid_value not in ALLOWED_BIDS:
                raise HTTPException(status_code=400, detail="Valor de lance inválido.")

            # Idempotência: se o mesmo clique chegou novamente, devolve o estado oficial
            # sem criar novo lance nem aplicar cooldown/tempo outra vez.
            if client_bid_id:
                existing_bid = (
                    db.query(Bid)
                    .filter(Bid.auction_id == item.id, Bid.user_id == user.id, Bid.client_bid_id == client_bid_id)
                    .first()
                )
                if existing_bid:
                    private_payload = public_auction_live_payload(
                        item,
                        db,
                        last_bid_id_override=existing_bid.id,
                        user=user,
                    )
                    private_payload["idempotent"] = True
                    private_payload["client_bid_id"] = client_bid_id
                    public_payload = public_auction_live_payload(item, db, user_turbo_eligible_override=None)
                    public_payload["idempotent"] = True
                    return private_payload, public_payload, 0

            mode_for_bid = compute_turbo_level(item)
            button_cooldown = bid_button_cooldown_seconds(bid_value, mode_for_bid)

            # Só consulta histórico do usuário quando o leilão já está em modo Turbo.
            # No modo normal, essa consulta era feita em todo clique sem necessidade e
            # aumentava a latência percebida do lance.
            if mode_for_bid >= 2:
                prior_user_bid_any = (
                    db.query(Bid.id)
                    .filter(Bid.auction_id == item.id, Bid.user_id == user.id)
                    .first()
                )
                if not prior_user_bid_any:
                    private_payload = public_auction_live_payload(item, db, user=user)
                    raise AuctionStateHTTPException(status_code=403, detail=turbo_lock_message(mode_for_bid), auction_payload=private_payload)

            # Cooldown operacional por botão/valor no caminho quente.
            # Antes era consultado em bids a cada clique. Em produção com 1 réplica,
            # o cache em memória elimina uma ida ao banco sem enfraquecer saldo/idempotência.
            remaining_cd = _get_fast_cooldown_remaining(item.id, user.id, bid_value, now)
            if remaining_cd > 0:
                private_payload = fast_bid_auction_payload(
                    item,
                    bids_count=int(getattr(item, "bids_count_cached", 0) or 0),
                    last_bidder=public_user_name(user),
                    last_bid_id=auction_last_bid_id(db, item.id),
                    user_turbo_eligible=True,
                    button_cooldown=remaining_cd,
                    mode_for_bid=mode_for_bid,
                    mode_before_bid=mode_for_bid,
                    bid_value=bid_value,
                    fee_value=0,
                    price_increment=0,
                    client_bid_id=client_bid_id,
                    wallet_balance=BR(getattr(user, "wallet_balance", 0.0) or 0.0),
                )
                raise AuctionStateHTTPException(
                    status_code=429,
                    detail=f"Aguarde {remaining_cd}s para dar outro lance.",
                    auction_payload=private_payload,
                    retry_after=remaining_cd,
                )

            previous_total_bid_count = int(getattr(item, 'bids_count_cached', 0) or 0)

            fee_value, increment = split_bid_amount(
                bid_value,
                getattr(item, "bid_fee_percent", DEFAULT_BID_FEE_PERCENT),
            )

            # Desconto atômico e rápido do saldo.
            # Em PostgreSQL usamos RETURNING para já obter o saldo final sem db.refresh().
            wallet_after_bid = None
            if not DATABASE_URL.startswith("sqlite"):
                row = db.execute(
                    text("""
                        UPDATE users
                        SET wallet_balance = wallet_balance - :bid_value
                        WHERE id = :user_id
                          AND wallet_balance >= :min_balance
                        RETURNING wallet_balance
                    """),
                    {
                        "bid_value": bid_value,
                        "min_balance": bid_value - 0.000001,
                        "user_id": user.id,
                    },
                ).first()
                if row:
                    wallet_after_bid = BR(row[0] or 0.0)
                updated_wallet = 1 if row else 0
            else:
                updated_wallet = (
                    db.query(User)
                    .filter(User.id == user.id, User.wallet_balance >= (bid_value - 0.000001))
                    .update({User.wallet_balance: User.wallet_balance - bid_value}, synchronize_session=False)
                )
                wallet_after_bid = BR((getattr(user, "wallet_balance", 0.0) or 0.0) - bid_value) if updated_wallet else None

            if updated_wallet == 0:
                db.rollback()
                fresh_user = db.get(User, user.id) or user
                private_payload = public_auction_live_payload(item, db, user=fresh_user)
                private_payload["wallet_balance"] = BR(getattr(fresh_user, "wallet_balance", 0.0) or 0.0)
                raise AuctionStateHTTPException(
                    status_code=402,
                    detail="Saldo insuficiente para dar este lance.",
                    auction_payload=private_payload,
                )

            # Extrato financeiro mantido dentro da mesma transação, mas com INSERT direto
            # para reduzir overhead de ORM no clique.
            db.execute(
                text("""
                    INSERT INTO wallet_transactions (user_id, amount, kind, note, created_at)
                    VALUES (:user_id, :amount, :kind, :note, :created_at)
                """),
                {
                    "user_id": user.id,
                    "amount": -bid_value,
                    "kind": "bid_spent",
                    "note": f"Lance no leilão #{item.id}",
                    "created_at": now,
                },
            )

            item.current_price = BR((item.current_price or 0.0) + increment)
            item.total_bid_fees = BR((getattr(item, "total_bid_fees", 0.0) or 0.0) + fee_value)
            item.total_bid_spent = BR((getattr(item, "total_bid_spent", 0.0) or 0.0) + bid_value)
            accepted_count = int(previous_total_bid_count or 0) + 1
            item.bids_count_cached = accepted_count

            turbo_after_bid = compute_turbo_level(item)
            timing_mode_for_bid = turbo_after_bid if turbo_after_bid >= 2 else mode_for_bid

            current_end = item.ends_at if item.ends_at and item.ends_at > now else now
            if timing_mode_for_bid >= 2:
                seconds = turbo_bid_seconds(bid_value, timing_mode_for_bid)
                item.ends_at = now + timedelta(seconds=seconds)
            else:
                delta_seconds = normal_time_delta_seconds(item, bid_value)
                item.ends_at = current_end + timedelta(seconds=delta_seconds)
                if item.ends_at <= now:
                    item.ends_at = now + timedelta(seconds=1)

            bid = Bid(
                auction_id=item.id,
                user_id=user.id,
                bid_value=bid_value,
                fee_value=fee_value,
                price_increment=increment,
                client_bid_id=client_bid_id,
            )
            db.add(bid)
            try:
                db.flush()
            except IntegrityError:
                # Outro processo pode ter registrado o mesmo client_bid_id primeiro.
                # Rollback desfaz também o desconto de saldo desta tentativa e devolve
                # o estado oficial do lance já aceito, sem duplicar cobrança.
                db.rollback()
                confirm_db = SessionLocal()
                try:
                    existing_bid = (
                        confirm_db.query(Bid)
                        .filter(Bid.auction_id == auction_id, Bid.user_id == user.id, Bid.client_bid_id == client_bid_id)
                        .first()
                    )
                    item_confirmed = confirm_db.get(AuctionItem, auction_id)
                    user_confirmed = confirm_db.get(User, user.id)
                    if existing_bid and item_confirmed and user_confirmed:
                        private_payload = public_auction_live_payload(
                            item_confirmed,
                            confirm_db,
                            last_bid_id_override=existing_bid.id,
                            user=user_confirmed,
                        )
                        private_payload.update({
                            "idempotent": True,
                            "client_bid_id": client_bid_id,
                            "wallet_balance": BR(getattr(user_confirmed, "wallet_balance", 0.0) or 0.0),
                            "server_time": datetime.utcnow().isoformat(),
                        })
                        public_payload = public_auction_live_payload(
                            item_confirmed,
                            confirm_db,
                            last_bid_id_override=existing_bid.id,
                            user_turbo_eligible_override=None,
                        )
                        public_payload["idempotent"] = True
                        return private_payload, public_payload, 0
                finally:
                    confirm_db.close()
                raise

            item.turbo_level = turbo_after_bid
            button_cooldown = bid_button_cooldown_seconds(bid_value, timing_mode_for_bid)

            db.commit()
            _set_fast_cooldown(item.id, user.id, bid_value, button_cooldown, now)

            accepted_bidder = public_user_name(user)

            # Payload rápido: evita consultas extras logo após o commit.
            # O /state continua como sincronização completa, mas o clique retorna rápido.
            private_payload = fast_bid_auction_payload(
                item,
                bids_count=accepted_count,
                last_bidder=accepted_bidder,
                last_bid_id=bid.id,
                user_turbo_eligible=True,
                button_cooldown=button_cooldown,
                mode_for_bid=timing_mode_for_bid,
                mode_before_bid=mode_for_bid,
                bid_value=bid_value,
                fee_value=fee_value,
                price_increment=increment,
                client_bid_id=client_bid_id,
                wallet_balance=wallet_after_bid,
            )

            # Payload público: enviado por websocket para todos. Nunca carrega saldo/elegibilidade privada.
            public_payload = fast_bid_auction_payload(
                item,
                bids_count=accepted_count,
                last_bidder=accepted_bidder,
                last_bid_id=bid.id,
                user_turbo_eligible=None,
                button_cooldown=button_cooldown,
                mode_for_bid=timing_mode_for_bid,
                mode_before_bid=mode_for_bid,
                bid_value=bid_value,
                fee_value=fee_value,
                price_increment=increment,
                client_bid_id="",
                wallet_balance=None,
            )

            return private_payload, public_payload, button_cooldown
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@app.post("/api/auction/{auction_id}/bid")
async def place_bid(request: Request, auction_id: int, bid_value: float = Form(...), client_bid_id: str = Form("")):
    public_payload = None
    try:
        private_payload, public_payload, button_cooldown = await asyncio.to_thread(
            _place_bid_sync,
            request,
            auction_id,
            bid_value,
            client_bid_id,
        )
    except AuctionStateHTTPException as exc:
        body = {"ok": False, "detail": exc.detail, "cooldown_scope": "button"}
        if exc.retry_after:
            body["retry_after"] = exc.retry_after
        if exc.auction_payload:
            body["auction"] = exc.auction_payload
        return JSONResponse(body, status_code=exc.status_code)
    except HTTPException as exc:
        retry_after = None
        msg = str(exc.detail or "")
        m = re.search(r"Aguarde\s+(\d+)s", msg)
        if m:
            retry_after = int(m.group(1))
        body = {"ok": False, "detail": exc.detail, "cooldown_scope": "button"}
        if retry_after:
            body["retry_after"] = retry_after
        return JSONResponse(body, status_code=exc.status_code)

    # Nunca espera WebSocket para responder o clique. O JSON do POST já atualiza a tela.
    if public_payload:
        asyncio.create_task(manager.broadcast(auction_id, {"type": "auction_update", "auction": public_payload}))
    return JSONResponse({"ok": True, "auction": private_payload, "button_cooldown": button_cooldown, "cooldown_scope": "button"})

@app.get("/api/auction/{auction_id}/state")
async def auction_state(request: Request, auction_id: int):
    db = SessionLocal()
    try:
        user = current_user(request, db)
        item = db.get(AuctionItem, auction_id)
        if not item:
            raise HTTPException(status_code=404, detail="Leilão não encontrado.")
        now = datetime.utcnow()
        changed = start_auction_if_due(item, now)
        finished_now = finish_auction_if_due(item, db, now, create_side_effects=False)
        changed = finished_now or changed
        if changed:
            db.commit()
            private_payload = public_auction_live_payload(item, db, user=user)
            public_payload = public_auction_live_payload(item, db, user_turbo_eligible_override=None)
            asyncio.create_task(manager.broadcast(auction_id, {"type": "auction_update", "auction": public_payload}))
            if finished_now:
                asyncio.create_task(asyncio.to_thread(ensure_finished_auction_side_effects, auction_id))
            return JSONResponse({"ok": True, "auction": private_payload})
        return JSONResponse({"ok": True, "auction": public_auction_live_payload(item, db, user=user)})
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


def cached_account_dashboard_context(db: Session, user: User, ttl_seconds: int = 8) -> dict:
    """Resumo da conta com cache curto por usuário.

    Não guarda o objeto User nem saldo; só cards/contadores do painel inicial.
    A área de saldo continua usando o user.wallet_balance atual do banco.
    """
    key = f"account:dashboard:{user.id}"
    cached = nav_cache_get(key)
    if cached is not None:
        return cached

    stats = user_stats(db, user)
    pending_orders = (
        db.query(WinnerOrder)
        .options(selectinload(WinnerOrder.auction))
        .filter(WinnerOrder.user_id == user.id, WinnerOrder.status == "pending_payment")
        .order_by(desc(WinnerOrder.created_at))
        .limit(3)
        .all()
    )
    won_orders = (
        db.query(WinnerOrder)
        .options(selectinload(WinnerOrder.auction))
        .filter(WinnerOrder.user_id == user.id)
        .order_by(desc(WinnerOrder.created_at))
        .limit(5)
        .all()
    )
    return nav_cache_set(key, {
        "stats": stats,
        "pending_orders": [build_order_card(x) for x in pending_orders[:3]],
        "latest_orders": [build_order_card(x) for x in won_orders[:5]],
    }, ttl_seconds)


@app.get("/minha-conta", response_class=HTMLResponse)
def my_account(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        account_summary = cached_account_dashboard_context(db, user)
        return templates.TemplateResponse(
            "account_pages.html",
            {
                "request": request,
                "user": user,
                "section": "dashboard",
                "stats": account_summary["stats"],
                "pending_orders": account_summary["pending_orders"],
                "latest_orders": account_summary["latest_orders"],
                "wallet_transactions": [],
                "withdrawals": [],
                "tickets": [],
                "orders_raw": [],
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
async def account_identity_submit(request: Request, document_front_file: UploadFile | None = File(None), document_back_file: UploadFile | None = File(None), document_file: UploadFile | None = File(None), selfie_file: UploadFile | None = File(None), residence_proof_file: UploadFile | None = File(None)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        front = document_front_file if document_front_file and document_front_file.filename else document_file
        if not front or not front.filename or not selfie_file or not selfie_file.filename:
            return templates.TemplateResponse("identity_verification.html", {"request": request, "user": user, "error": "Envie a frente do documento e a selfie para análise.", "success": None}, status_code=400)
        user.document_file_url = save_uploaded_image(front)
        if document_back_file and document_back_file.filename:
            user.document_back_file_url = save_uploaded_image(document_back_file)
        user.selfie_file_url = save_uploaded_image(selfie_file)
        if residence_proof_file and residence_proof_file.filename:
            user.residence_proof_file_url = save_uploaded_image(residence_proof_file)
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
                    "status": public_display_status(item.status),
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


def blank_admin_context() -> dict:
    return {
        "stats": {
            "users": 0, "active_users": 0, "live": 0, "scheduled": 0,
            "pending_payment": 0, "completed": 0, "identity_pending": 0,
            "pending_withdrawals": 0, "open_tickets": 0, "pending_shipping": 0,
            "returned_products": 0,
        },
        "users": [],
        "moderation_users": [],
        "identity_pending_users": [],
        "items": [],
        "orders": [],
        "admin_order_cards": [],
        "pending_payment_orders": [],
        "shipping_orders": [],
        "consultation_orders": [],
        "withdrawal_requests": [],
        "support_tickets": [],
        "user_audit": {},
        "audit_logs": [],
        "finished_auctions": [],
        "returned_items": [],
        "finance": {},
        "cashflow_movements": [],
        "auction_results": [],
        "recent_chat_messages": [],
        "suggestion_vote_stats": [],
    }


def admin_light_stats(db: Session, is_super_admin: bool, returned_count: int = 0) -> dict:
    """Estatísticas mínimas para o painel abrir rápido."""
    stats = {
        "users": 0,
        "active_users": 0,
        "live": db.query(AuctionItem).filter(AuctionItem.status == "live").count(),
        "scheduled": db.query(AuctionItem).filter(AuctionItem.status.in_(["scheduled", "relisted"])).count(),
        "pending_payment": db.query(WinnerOrder).filter(WinnerOrder.status == "pending_payment").count(),
        "completed": db.query(AuctionItem).filter(AuctionItem.status == "ended").count(),
        "pending_shipping": db.query(WinnerOrder).filter(WinnerOrder.status.in_(["paid", "processing", "purchased"])).count(),
        "identity_pending": 0,
        "pending_withdrawals": 0,
        "open_tickets": db.query(SupportTicket).filter(SupportTicket.status.in_(["open", "in_review", "dispute"])).count(),
        "returned_products": returned_count,
    }
    if is_super_admin:
        stats["users"] = db.query(User).count()
        stats["active_users"] = db.query(User).filter(User.is_banned == False).count()
        stats["identity_pending"] = db.query(User).filter(User.identity_status == "pending").count()
        stats["pending_withdrawals"] = db.query(WithdrawalRequest).filter(WithdrawalRequest.status == "pending").count()
    return stats


def cached_admin_light_stats(db: Session, is_super_admin: bool, returned_count: int = 0, ttl_seconds: int = 10) -> dict:
    key = f"admin:light-stats:{int(is_super_admin)}:{int(returned_count)}"
    cached = nav_cache_get(key)
    if cached is not None:
        return cached
    return nav_cache_set(key, admin_light_stats(db, is_super_admin, returned_count), ttl_seconds)


def cached_admin_cashflow_context(db: Session, ttl_seconds: int = 12) -> dict:
    """Blocos financeiros pesados com cache curto.

    Fluxo de Caixa e Resumo Geral eram as abas mais lentas. Como são telas de
    leitura administrativa, cache de poucos segundos reduz troca de abas sem
    esconder alterações por muito tempo.
    """
    cached = nav_cache_get("admin:cashflow-context")
    if cached is not None:
        return cached
    value = {
        "finance": build_finance_dashboard(db),
        "cashflow_movements": build_cashflow_movements(db),
        "auction_results": build_auction_results(db),
    }
    return nav_cache_set("admin:cashflow-context", value, ttl_seconds)


def cached_finished_auctions(db: Session, ttl_seconds: int = 10) -> list[dict]:
    cached = nav_cache_get("admin:finished-auctions")
    if cached is not None:
        return cached
    return nav_cache_set("admin:finished-auctions", build_finished_auctions(db), ttl_seconds)


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

        is_super_admin = bool(admin.is_superadmin)
        allowed_tabs = {
            "admin-product", "admin-returned", "admin-auctions", "admin-finished",
            "admin-shipping", "admin-tickets", "admin-suggestions",
            "admin-search-orders", "admin-moderation",
        }
        if is_super_admin:
            allowed_tabs.update({
                "admin-dashboard", "admin-cashflow", "admin-pending-payments",
                "admin-users", "admin-identity-pending", "admin-withdrawals",
                "admin-audit",
            })

        requested_tab = (request.query_params.get("tab") or "").strip()
        active_panel = requested_tab if requested_tab in allowed_tabs else "admin-product"
        search = (request.query_params.get("q") or "").strip()

        ctx = blank_admin_context()
        ctx.update({
            "request": request,
            "user": admin,
            "search": search,
            "payment_deadline_minutes": PAYMENT_DEADLINE_MINUTES,
            "is_super_admin": is_super_admin,
            "admin_active_panel": active_panel,
        })

        # Produto é a aba mais leve e padrão do admin operacional. Não precisa
        # carregar financeiro, auditoria, usuários nem pedidos.
        if active_panel == "admin-dashboard":
            ctx["stats"] = cached_admin_light_stats(db, is_super_admin)
        elif active_panel == "admin-cashflow" and is_super_admin:
            cashflow_ctx = cached_admin_cashflow_context(db)
            ctx.update(cashflow_ctx)
            ctx["stats"] = cached_admin_light_stats(db, is_super_admin)
        elif active_panel == "admin-returned":
            returned_items = build_returned_items(db)
            ctx["returned_items"] = returned_items
            ctx["stats"] = cached_admin_light_stats(db, is_super_admin, returned_count=len(returned_items))
        elif active_panel == "admin-auctions":
            items = db.query(AuctionItem).filter(AuctionItem.status.in_(["live", "scheduled", "relisted", "paused"])).order_by(desc(AuctionItem.created_at)).limit(30).all()
            for item in items:
                item.collected_percent = auction_progress_percent(item)
                item.cash_reserved = auction_cash_reserved_before_payment(item)
                item.expected_total_if_paid = auction_total_if_paid(item)
                item.expected_profit_if_paid = auction_expected_profit_if_paid(item)
            ctx["items"] = items
        elif active_panel in {"admin-pending-payments", "admin-shipping", "admin-search-orders"}:
            orders = db.query(WinnerOrder).options(selectinload(WinnerOrder.auction), selectinload(WinnerOrder.user)).order_by(desc(WinnerOrder.created_at)).limit(40).all()
            ctx["orders"] = orders
            ctx["admin_order_cards"] = build_admin_order_cards(db, orders)
            ctx["pending_payment_orders"] = [o for o in orders if o.status == "pending_payment"]
            ctx["shipping_orders"] = [o for o in orders if o.status in ["paid", "processing", "purchased"]]
            ctx["consultation_orders"] = [o for o in orders if o.status in ["sent", "delivered", "dispute", "resolved", "closed"]]
        elif active_panel == "admin-finished":
            ctx["finished_auctions"] = cached_finished_auctions(db)
        elif active_panel == "admin-users" and is_super_admin:
            users_query = db.query(User)
            if search:
                like = f"%{search}%"
                users_query = users_query.filter((User.full_name.ilike(like)) | (User.public_name.ilike(like)) | (User.email.ilike(like)) | (User.cpf.ilike(like)) | (User.phone.ilike(like)))
            users = users_query.order_by(desc(User.created_at)).limit(40).all()
            ctx["users"] = users
            ctx["user_audit"] = user_audit_map(db, users)
        elif active_panel == "admin-identity-pending" and is_super_admin:
            ctx["identity_pending_users"] = db.query(User).filter(User.identity_status == "pending").order_by(desc(User.created_at)).limit(40).all()
        elif active_panel == "admin-withdrawals" and is_super_admin:
            ctx["withdrawal_requests"] = db.query(WithdrawalRequest).options(selectinload(WithdrawalRequest.user)).order_by(desc(WithdrawalRequest.created_at)).limit(25).all()
        elif active_panel == "admin-tickets":
            ctx["support_tickets"] = db.query(SupportTicket).options(selectinload(SupportTicket.user), selectinload(SupportTicket.order)).order_by(desc(SupportTicket.created_at)).limit(25).all()
        elif active_panel == "admin-suggestions":
            ctx["suggestion_vote_stats"] = cached_suggestion_vote_stats(db)
        elif active_panel == "admin-audit" and is_super_admin:
            ctx["audit_logs"] = db.query(AuditLog).options(selectinload(AuditLog.user)).order_by(desc(AuditLog.created_at)).limit(25).all()
            ctx["recent_chat_messages"] = db.query(ChatMessage).options(selectinload(ChatMessage.user), selectinload(ChatMessage.auction)).order_by(desc(ChatMessage.created_at)).limit(15).all()
        elif active_panel == "admin-moderation":
            ctx["moderation_users"] = db.query(User).filter((User.is_banned == True) | (User.chat_muted == True)).order_by(desc(User.created_at)).limit(25).all()

        return templates.TemplateResponse("admin.html", ctx)
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
    bid_fee_percent: float = Form(10),
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
    cashback_enabled: int = Form(0),
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
        item.cashback_enabled = bool(int(cashback_enabled or 0))

        # Ao relançar, remove sorteio antigo para evitar cashback preso de leilão anterior.
        db.query(CashbackEntry).filter(CashbackEntry.auction_id == item.id).delete(synchronize_session=False)
        db.query(CashbackEvent).filter(CashbackEvent.auction_id == item.id).delete(synchronize_session=False)

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
            register_product_outgoing_if_needed(db, order, now)
        if int(mark_sent or 0):
            order.status = "sent"
            order.sent_at = now
            register_product_outgoing_if_needed(db, order, now)
        if int(mark_delivered or 0):
            order.status = "delivered"
            order.delivered_at = now
            register_product_outgoing_if_needed(db, order, now)
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
        require_superadmin(request, db)
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
        admin = require_superadmin(request, db)
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
                order.sent_at = datetime.utcnow()
                register_product_outgoing_if_needed(db, order, order.sent_at)
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
        admin_user = require_admin(request, db)
        order = db.get(WinnerOrder, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        allowed = {"pending_payment", "paid", "processing", "purchased", "sent", "delivered", "expired"}
        if status not in allowed:
            raise HTTPException(status_code=400, detail="Status inválido.")
        if not admin_user.is_superadmin and status in {"pending_payment", "paid", "expired"}:
            raise HTTPException(status_code=403, detail="Apenas o super admin pode alterar status financeiro do pedido.")
        previous_status = order.status
        order.status = status
        now = datetime.utcnow()
        if status == "purchased" and previous_status != "purchased":
            order.purchased_at = order.purchased_at or now
            register_product_outgoing_if_needed(db, order, now)
        if status == "sent" and previous_status != "sent":
            order.sent_at = order.sent_at or now
            register_product_outgoing_if_needed(db, order, now)
        if status == "delivered" and previous_status != "delivered":
            order.delivered_at = order.delivered_at or now
            register_product_outgoing_if_needed(db, order, now)
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

        pix_key_clean = (pix_key or "").strip()
        if not pix_key_clean:
            raise HTTPException(status_code=400, detail="Informe uma chave Pix válida.")

        # Desconto atômico: o banco só reduz o saldo se ainda houver saldo suficiente
        # no exato momento do UPDATE. Isso blinda duas abas/duplo envio simultâneo.
        with WITHDRAWAL_USER_LOCKS[user.id]:
            result = db.execute(
                text("""
                    UPDATE users
                    SET wallet_balance = wallet_balance - :amount
                    WHERE id = :user_id AND wallet_balance >= :amount
                """),
                {"amount": amount, "user_id": user.id},
            )
            if result.rowcount != 1:
                db.rollback()
                raise HTTPException(status_code=400, detail="Saldo insuficiente para solicitar saque.")

            req = WithdrawalRequest(user_id=user.id, amount=amount, pix_key=pix_key_clean, status="pending")
            db.add(req)
            db.add(WalletTransaction(
                user_id=user.id,
                amount=-amount,
                kind="withdrawal_reserved",
                note=f"Saque solicitado e reservado no caixa para pagamento manual via Pix: {pix_key_clean}",
            ))
            audit_event(db, request, "wallet.withdrawal_requested", user, "withdrawal", "pending", f"Valor reservado para saque: R$ {fmt_money(amount)}")
            db.commit()

        return RedirectResponse("/minha-conta#account-balance-panel", status_code=303)
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
        require_superadmin(request, db)
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        user.identity_status = "verified"
        user.identity_note = note.strip()
        user.verified_at = datetime.utcnow()
        audit_event(db, request, "kyc.verified", user, "user", user.id, note.strip())
        db.commit()
        return RedirectResponse("/admin#admin-identity-pending", status_code=303)
    finally:
        db.close()


@app.post("/admin/user/{user_id}/reject")
def admin_reject_user(request: Request, user_id: int, note: str = Form("")):
    db = SessionLocal()
    try:
        require_superadmin(request, db)
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        user.identity_status = "rejected"
        user.identity_note = note.strip() or "Documentação ilegível ou incompatível com os dados informados."
        user.verified_at = None
        send_identity_rejection_email(user, user.identity_note)
        audit_event(db, request, "kyc.rejected", user, "user", user.id, user.identity_note)
        db.commit()
        return RedirectResponse("/admin#admin-identity-pending", status_code=303)
    finally:
        db.close()


@app.post("/admin/withdrawal/{withdrawal_id}/set-status")
def admin_set_withdrawal_status(request: Request, withdrawal_id: int, status: str = Form(...), admin_note: str = Form("")):
    db = SessionLocal()
    try:
        require_superadmin(request, db)
        req = db.get(WithdrawalRequest, withdrawal_id)
        if not req:
            raise HTTPException(status_code=404, detail="Saque não encontrado.")
        if status not in {"pending", "approved", "rejected", "paid"}:
            raise HTTPException(status_code=400, detail="Status inválido.")
        if req.status == "pending" and status == "rejected":
            user = db.get(User, req.user_id)
            if user:
                user.wallet_balance = BR(user.wallet_balance + req.amount)
                db.add(WalletTransaction(user_id=user.id, amount=req.amount, kind="withdrawal_reversal", note=f"Saque #{req.id} recusado/devolvido ao saldo do usuário"))
        req.status = status
        req.admin_note = admin_note.strip()
        req.updated_at = datetime.utcnow()
        db.commit()
        return RedirectResponse("/admin#admin-withdrawals", status_code=303)
    finally:
        db.close()


@app.post("/admin/order/{order_id}/extend-payment")
def admin_extend_payment(request: Request, order_id: int, extra_minutes: int = Form(10)):
    db = SessionLocal()
    try:
        require_superadmin(request, db)
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
        require_superadmin(request, db)
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
