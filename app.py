from __future__ import annotations

import asyncio
import math
import mimetypes
import os
import re
import secrets
import shutil
import threading
import time
import base64
import hashlib
import hmac
import smtplib
import csv
import io
import json
import urllib.request
import urllib.error
from email.message import EmailMessage
from email.utils import parseaddr
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional
from types import SimpleNamespace
from urllib.parse import urlparse

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
    or_,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker, selectinload
from sqlalchemy.exc import IntegrityError


APP_NAME = "Lanceio Certo"
APP_ENV = (os.getenv("APP_ENV") or os.getenv("ENV") or "development").strip().lower()
IS_PRODUCTION = APP_ENV in {"prod", "production", "real"}
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./lanceiocerto.db")
IS_SQLITE = DATABASE_URL.startswith("sqlite")

if IS_PRODUCTION and IS_SQLITE:
    raise RuntimeError(
        "Produção não pode iniciar com SQLite. Configure DATABASE_URL com PostgreSQL antes do deploy."
    )

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))
PRODUCT_IMAGE_MAX_WIDTH = int(os.getenv("PRODUCT_IMAGE_MAX_WIDTH", "1200"))
PRODUCT_IMAGE_WEBP_QUALITY = int(os.getenv("PRODUCT_IMAGE_WEBP_QUALITY", "78"))
ALLOWED_UPLOAD_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "application/pdf",
}
ALLOWED_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"}


if IS_SQLITE:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "1800")),
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
    )
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
    password_reset_token: Mapped[str] = mapped_column(String(120), default="")
    password_reset_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
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
    account_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    account_deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    account_delete_reason: Mapped[str] = mapped_column(Text, default="")
    account_delete_details: Mapped[str] = mapped_column(Text, default="")
    account_delete_ip: Mapped[str] = mapped_column(String(80), default="")
    chat_muted: Mapped[bool] = mapped_column(Boolean, default=False)
    ban_count: Mapped[int] = mapped_column(Integer, default=0)
    banned_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ban_reason: Mapped[str] = mapped_column(Text, default="")
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
    # Aceite único do Contrato de Serviço + Regras de Participação + Política de Privacidade.
    accepted_legal_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    accepted_legal_ip: Mapped[str] = mapped_column(String(80), default="")
    accepted_legal_user_agent: Mapped[str] = mapped_column(String(600), default="")
    accepted_terms_version: Mapped[str] = mapped_column(String(40), default="")
    accepted_rules_version: Mapped[str] = mapped_column(String(40), default="")
    accepted_privacy_version: Mapped[str] = mapped_column(String(40), default="")
    # Compatibilidade: wallet_balance passa a representar Créditos LC comprados/promocionais,
    # não saldo financeiro sacável. A linguagem pública foi alterada para Créditos LC.
    wallet_balance: Mapped[float] = mapped_column(Float, default=0.0)
    referral_code: Mapped[str] = mapped_column(String(40), default="", unique=True, index=True)
    referral_code_customized_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    referred_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    first_credit_purchase_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    referral_bonus_released_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    signup_ip: Mapped[str] = mapped_column(String(80), default="")
    signup_device_hash: Mapped[str] = mapped_column(String(120), default="")
    fraud_risk_score: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    bids: Mapped[list["Bid"]] = relationship(back_populates="user")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="user")
    orders: Mapped[list["WinnerOrder"]] = relationship(back_populates="user")


class AuctionItem(Base):
    __tablename__ = "auction_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str] = mapped_column(Text, default="https://via.placeholder.com/900x600?text=Produto")
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

class MercadoPagoPayment(Base):
    __tablename__ = "mercadopago_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("winner_orders.id"), nullable=True, index=True)
    purpose: Mapped[str] = mapped_column(String(40), default="deposit")  # deposit/order_payment
    mp_payment_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    qr_code: Mapped[str] = mapped_column(Text, default="")
    qr_code_base64: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(String(220), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    order: Mapped[Optional["WinnerOrder"]] = relationship(foreign_keys=[order_id])



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
    fulfillment_mode: Mapped[str] = mapped_column(String(40), default="")  # site_purchase/customer_purchase
    submitted_purchase_link: Mapped[str] = mapped_column(String(900), default="")
    submitted_link_domain: Mapped[str] = mapped_column(String(160), default="")
    submitted_link_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted_link_validation_note: Mapped[str] = mapped_column(Text, default="")
    submitted_link_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    approved_by_admin: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    order_choice_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
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
    # amount = valor bruto solicitado/descontado do saldo do cliente.
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    # fee_amount = taxa de saque de 1% que fica no caixa/lucro do site.
    fee_amount: Mapped[float] = mapped_column(Float, default=0.0)
    # net_amount = valor líquido que o admin deve transferir ao cliente.
    net_amount: Mapped[float] = mapped_column(Float, default=0.0)
    pix_key: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending/approved/rejected/paid
    admin_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class ReferralReward(Base):
    __tablename__ = "referral_rewards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referrer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    referred_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    amount_credits: Mapped[float] = mapped_column(Float, default=5.0)
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending/approved/blocked/canceled
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    referrer: Mapped[User] = relationship(foreign_keys=[referrer_user_id])
    referred: Mapped[User] = relationship(foreign_keys=[referred_user_id])


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("winner_orders.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(60), default="duvida_geral", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="media", index=True)  # baixa/media/alta/urgente
    subject: Mapped[str] = mapped_column(String(160), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    proof_url: Mapped[str] = mapped_column(String(600), default="")
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)  # open/in_review/awaiting_customer/dispute/resolved/closed
    result: Mapped[str] = mapped_column(String(30), default="")  # client/site/agreement/manual_adjustment
    assigned_admin_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    admin_note: Mapped[str] = mapped_column(Text, default="")
    customer_last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_customer_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_admin_response_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sla_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    order: Mapped[Optional[WinnerOrder]] = relationship(foreign_keys=[order_id])
    assigned_admin: Mapped[Optional[User]] = relationship(foreign_keys=[assigned_admin_id])


class SupportTicketMessage(Base):
    __tablename__ = "support_ticket_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    admin_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    message_type: Mapped[str] = mapped_column(String(30), default="customer")  # customer/admin/internal/system
    body: Mapped[str] = mapped_column(Text, default="")
    proof_url: Mapped[str] = mapped_column(String(600), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    ticket: Mapped[SupportTicket] = relationship(foreign_keys=[ticket_id])
    user: Mapped[Optional[User]] = relationship(foreign_keys=[user_id])
    admin: Mapped[Optional[User]] = relationship(foreign_keys=[admin_id])


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
        self.connections: dict[int, dict[WebSocket, asyncio.Queue]] = defaultdict(dict)
        self.writer_tasks: dict[WebSocket, asyncio.Task] = {}

    def _with_realtime_clock(self, payload: dict) -> dict:
        """Carimba o horário real do envio pelo WebSocket.

        O payload do lance é montado quando o banco confirma o lance. Se a rede ou
        uma conexão lenta atrasar o envio, usar aquele horário antigo pode deixar o
        cronômetro de alguns computadores alguns segundos para trás. Por isso o
        relógio é atualizado no último instante antes do send_json.
        """
        try:
            cloned = dict(payload or {})
            auction_payload = cloned.get("auction")
            if isinstance(auction_payload, dict):
                stamped = dict(auction_payload)
                now_payload = server_time_payload()
                stamped.update(now_payload)
                stamped["realtime_sent_ms"] = now_payload["server_time_ms"]
                cloned["auction"] = stamped
            return cloned
        except Exception:
            return payload

    async def _safe_close(self, websocket: WebSocket) -> None:
        try:
            await websocket.close()
        except Exception:
            pass

    async def _writer(self, auction_id: int, websocket: WebSocket, queue: asyncio.Queue) -> None:
        try:
            while True:
                payload = await queue.get()
                await asyncio.wait_for(websocket.send_json(self._with_realtime_clock(payload)), timeout=1.2)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Se uma conexão quebra no envio, remove e fecha de verdade.
            # Antes removia do manager, mas o receive_text podia ficar preso,
            # mantendo tarefa e socket vivos em pico.
            self.disconnect(auction_id, websocket)
            await self._safe_close(websocket)

    async def connect(self, auction_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=24)
        self.connections[auction_id][websocket] = queue
        self.writer_tasks[websocket] = asyncio.create_task(self._writer(auction_id, websocket, queue))

    def disconnect(self, auction_id: int, websocket: WebSocket) -> None:
        if auction_id in self.connections:
            self.connections[auction_id].pop(websocket, None)
            if not self.connections[auction_id]:
                self.connections.pop(auction_id, None)
        task = self.writer_tasks.pop(websocket, None)
        current = asyncio.current_task()
        if task and not task.done() and task is not current:
            task.cancel()

    async def send_to(self, auction_id: int, websocket: WebSocket, payload: dict) -> None:
        queue = self.connections.get(auction_id, {}).get(websocket)
        if not queue:
            return
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            self.disconnect(auction_id, websocket)
            asyncio.create_task(self._safe_close(websocket))

    async def broadcast(self, auction_id: int, payload: dict) -> None:
        # Caminho quente dos lances: não espera rede de usuário nenhum.
        # Cada conexão tem sua própria fila, preservando a ordem dos eventos e
        # evitando send_json simultâneo no mesmo WebSocket. Conexão lenta demais
        # é cortada e fechada para não acumular tarefas/zumbis.
        sockets = list(self.connections.get(auction_id, {}).items())
        if not sockets:
            return
        for ws, queue in sockets:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                self.disconnect(auction_id, ws)
                asyncio.create_task(self._safe_close(ws))


# Configurações de produção/escala.
# Comece conservador; aumente somente após teste de carga.
SECURITY_HEADERS_ENABLED = os.getenv("SECURITY_HEADERS_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_GENERAL_PER_MINUTE = int(os.getenv("RATE_LIMIT_GENERAL_PER_MINUTE", "180"))
RATE_LIMIT_AUTH_PER_MINUTE = int(os.getenv("RATE_LIMIT_AUTH_PER_MINUTE", "20"))
RATE_LIMIT_BID_PER_MINUTE = int(os.getenv("RATE_LIMIT_BID_PER_MINUTE", "40"))
RATE_LIMIT_PAYMENT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PAYMENT_PER_MINUTE", "12"))
SLOW_REQUEST_WARN_MS = float(os.getenv("SLOW_REQUEST_WARN_MS", "1500"))

RATE_LIMIT_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def _rate_limit_key(request: Request) -> tuple[str, int]:
    path = request.url.path
    ip = client_ip(request) if "client_ip" in globals() else ((request.client.host if request.client else "")[:80])
    if path.startswith("/static/") or path in {"/healthz", "/readyz"}:
        return "", 0
    if "/bid" in path:
        return f"bid:{ip}", RATE_LIMIT_BID_PER_MINUTE
    if path.startswith("/login") or path.startswith("/register") or path.startswith("/forgot-password") or path.startswith("/reset-password"):
        return f"auth:{ip}", RATE_LIMIT_AUTH_PER_MINUTE
    if path.startswith("/minha-conta/saldo") or path.startswith("/webhook/mercadopago"):
        return f"payment:{ip}", RATE_LIMIT_PAYMENT_PER_MINUTE
    return f"general:{ip}", RATE_LIMIT_GENERAL_PER_MINUTE


def _rate_limit_allowed(request: Request) -> tuple[bool, int]:
    if not RATE_LIMIT_ENABLED:
        return True, 0
    key, limit = _rate_limit_key(request)
    if not key or limit <= 0:
        return True, 0
    now = time.time()
    bucket = RATE_LIMIT_BUCKETS[key]
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = max(1, int(RATE_LIMIT_WINDOW_SECONDS - (now - bucket[0])))
        return False, retry_after
    bucket.append(now)
    return True, 0


def _apply_security_headers(response: Response) -> Response:
    if not SECURITY_HEADERS_ENABLED:
        return response
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if IS_PRODUCTION:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app = FastAPI(title=APP_NAME)
# HTML grande no Railway estava custando ~1,5s só em compressão.
# Mantemos gzip apenas para respostas muito grandes; páginas normais navegam sem esse peso.
app.add_middleware(GZipMiddleware, minimum_size=int(os.getenv("GZIP_MINIMUM_SIZE", "180000")))
templates = Jinja2Templates(directory="templates")
ASSET_VERSION = os.getenv("ASSET_VERSION", "20260615-realtime-v14-single-home-clock")
templates.env.globals["asset_version"] = ASSET_VERSION

# Versões dos documentos legais aceitos no cadastro.
# Atualize estes valores sempre que houver mudança relevante no contrato/regras/privacidade.
LEGAL_TERMS_VERSION = os.getenv("LEGAL_TERMS_VERSION", "2026-06-16-v1")
LEGAL_RULES_VERSION = os.getenv("LEGAL_RULES_VERSION", "2026-06-16-v1")
LEGAL_PRIVACY_VERSION = os.getenv("LEGAL_PRIVACY_VERSION", "2026-06-16-v1")
templates.env.globals["legal_terms_version"] = LEGAL_TERMS_VERSION
templates.env.globals["legal_rules_version"] = LEGAL_RULES_VERSION
templates.env.globals["legal_privacy_version"] = LEGAL_PRIVACY_VERSION
app.mount("/static", StaticFiles(directory="static"), name="static")
manager = ConnectionManager()
AUCTION_BID_LOCKS: dict[int, threading.Lock] = defaultdict(threading.Lock)
# Fila assíncrona por leilão: evita várias requisições ocupando threads só esperando
# o mesmo lock de lance. Mantém a ordem real dos cliques no caminho quente.
AUCTION_BID_ASYNC_LOCKS: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
SUGGESTION_WEEK_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)
SUGGESTION_USER_VOTE_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)
WITHDRAWAL_USER_LOCKS: dict[int, threading.Lock] = defaultdict(threading.Lock)
BID_COOLDOWN_MEMORY: dict[str, datetime] = {}


class AuctionStateHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str, auction_payload: Optional[dict] = None, retry_after: Optional[int] = None):
        super().__init__(status_code=status_code, detail=detail)
        self.auction_payload = auction_payload
        self.retry_after = retry_after



def is_speculative_navigation_request(request: Request) -> bool:
    """Bloqueia requisições automáticas do navegador que não são navegação real.

    Nos testes em produção o Chrome/Edge enviou GET para /, /admin e /minha-conta
    com Sec-Fetch-Mode: cors e Sec-Fetch-Dest: empty. Essas chamadas vinham
    antes/ao lado da navegação real e faziam o backend montar páginas pesadas
    sem o usuário realmente ter clicado duas vezes.
    """
    if request.method.upper() != "GET":
        return False

    path = request.url.path.rstrip("/") or "/"
    if path.startswith("/static/") or path.startswith("/api/") or path.startswith("/ws/"):
        return False

    # Só tratamos como página HTML principal. Endpoints de formulário/API ficam fora.
    nav_paths = {"/", "/login", "/register", "/forgot-password", "/reset-password", "/admin", "/minha-conta"}
    if path not in nav_paths and not path.startswith("/minha-conta/"):
        return False

    purpose = (request.headers.get("purpose") or request.headers.get("sec-purpose") or "").lower()
    fetch_mode = (request.headers.get("sec-fetch-mode") or "").lower()
    fetch_dest = (request.headers.get("sec-fetch-dest") or "").lower()
    requested_with = (request.headers.get("x-requested-with") or "").lower()
    accept = (request.headers.get("accept") or "").lower()

    if requested_with == "xmlhttprequest" or "application/json" in accept:
        return False

    if "prefetch" in purpose or "prerender" in purpose:
        return True

    # Navegação real deve vir como navigate/document. CORS/empty em página HTML
    # é requisição especulativa ou fetch automático; não deve renderizar template.
    if fetch_mode and fetch_mode != "navigate" and fetch_dest in {"", "empty"}:
        return True

    return False


@app.middleware("http")
async def production_guard_navigation_and_cache(request: Request, call_next):
    request_id = secrets.token_hex(8)
    allowed, retry_after = _rate_limit_allowed(request)
    if not allowed:
        return _apply_security_headers(JSONResponse(
            {"ok": False, "detail": "Muitas requisições em pouco tempo. Aguarde alguns segundos e tente novamente."},
            status_code=429,
            headers={"Retry-After": str(retry_after), "X-Request-ID": request_id},
        ))

    if is_speculative_navigation_request(request):
        now = datetime.utcnow()
        skip_key = (
            f"{request.method}:{request.url.path}:"
            f"{request.headers.get('purpose') or request.headers.get('sec-purpose') or '-'}:"
            f"{request.headers.get('sec-fetch-mode') or '-'}:"
            f"{request.headers.get('sec-fetch-dest') or '-'}"
        )
        last_logged = NAV_SKIP_LOG_MEMORY.get(skip_key)
        if not last_logged or (now - last_logged).total_seconds() >= NAV_SKIP_LOG_INTERVAL_SECONDS:
            NAV_SKIP_LOG_MEMORY[skip_key] = now
            print(
                f"[NAV-SKIP] {request.method} {request.url.path} speculative=1 "
                f"purpose={request.headers.get('purpose') or request.headers.get('sec-purpose') or '-'} "
                f"mode={request.headers.get('sec-fetch-mode') or '-'} "
                f"dest={request.headers.get('sec-fetch-dest') or '-'}"
            )
        return Response(
            status_code=204,
            headers={
                "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
                "Pragma": "no-cache",
                "X-Nav-Skip": "1",
                "X-Request-ID": request_id,
            },
        )

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        elapsed = (time.perf_counter() - started) * 1000
        print(f"[REQ-ERROR] id={request_id} {request.method} {request.url.path} total={elapsed:.1f}ms error={type(exc).__name__}")
        raise

    if request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=604800, stale-while-revalidate=86400")
    elif request.method.upper() == "GET" and response.status_code == 200:
        # Cache privado curtíssimo para o botão voltar/ida-e-volta no mesmo usuário.
        # Não é compartilhado por proxy e não guarda nada público entre contas.
        if request.url.path in {"/", "/admin", "/minha-conta"} or request.url.path.startswith("/minha-conta/"):
            response.headers.setdefault("Cache-Control", "private, max-age=3")

    response.headers.setdefault("X-Request-ID", request_id)
    if request.method.upper() in {"GET", "POST"} and not request.url.path.startswith("/static/"):
        elapsed = (time.perf_counter() - started) * 1000
        level = "NAV-SLOW" if elapsed >= SLOW_REQUEST_WARN_MS else "NAV-REQ"
        print(
            f"[{level}] id={request_id} {request.method} {request.url.path} status={response.status_code} total={elapsed:.1f}ms "
            f"purpose={request.headers.get('purpose') or request.headers.get('sec-purpose') or '-'} "
            f"mode={request.headers.get('sec-fetch-mode') or '-'} "
            f"dest={request.headers.get('sec-fetch-dest') or '-'}"
        )
    return _apply_security_headers(response)
SESSION_MAX_AGE_SECONDS = int(os.getenv("SESSION_MAX_AGE_SECONDS", str(60 * 60 * 24 * 7)))
_session_secure_env = os.getenv("SESSION_COOKIE_SECURE", "auto").strip().lower()
SESSION_COOKIE_SECURE = IS_PRODUCTION if _session_secure_env == "auto" else _session_secure_env in {"1", "true", "yes", "on"}
SESSIONS: dict[str, tuple[int, datetime]] = {}


def _session_user_id(token: str | None) -> Optional[int]:
    if not token:
        return None
    value = SESSIONS.get(token)
    if not value:
        return None
    # Compatibilidade com sessões criadas antes desta correção.
    if isinstance(value, int):
        return value
    user_id, expires_at = value
    if expires_at <= datetime.utcnow():
        SESSIONS.pop(token, None)
        return None
    return user_id


def _create_session_response(response: Response, user_id: int) -> Response:
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = (user_id, datetime.utcnow() + timedelta(seconds=SESSION_MAX_AGE_SECONDS))
    response.set_cookie(
        "session_token",
        token,
        httponly=True,
        samesite="lax",
        secure=SESSION_COOKIE_SECURE,
        max_age=SESSION_MAX_AGE_SECONDS,
    )
    return response
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
# Sorteio/cashback removido do produto. Mantemos as tabelas antigas apenas para compatibilidade,
# mas a funcionalidade fica desligada por padrão.
ENABLE_CASHBACK_DRAW = os.getenv("ENABLE_CASHBACK_DRAW", "0").strip().lower() in {"1", "true", "yes", "on"}
DEFAULT_BID_FEE_PERCENT = 10.0
PLATFORM_PROFIT_PERCENT = 10.0
LC_MIN_CREDIT_PURCHASE_AMOUNT = float(os.getenv("LC_MIN_CREDIT_PURCHASE_AMOUNT", "5"))
REFERRAL_BONUS_CREDITS = float(os.getenv("REFERRAL_BONUS_CREDITS", "5"))
REFERRAL_MIN_FIRST_PURCHASE_AMOUNT = float(os.getenv("REFERRAL_MIN_FIRST_PURCHASE_AMOUNT", "5"))
SUPPORT_ADMIN_ADJUST_LIMIT = float(os.getenv("SUPPORT_ADMIN_ADJUST_LIMIT", "20"))

SUPPORT_CATEGORIES = {
    "pagamento_creditos": "Pagamento de Créditos LC não caiu",
    "creditos_descontados": "Créditos LC descontados indevidamente",
    "indicacao_bonus": "Indicação ou bônus não creditado",
    "lance_leilao": "Problema em lance ou leilão",
    "arremate_pagamento": "Problema no pagamento do arremate",
    "compra_assistida": "Compra assistida / link do pedido",
    "produto_entrega": "Entrega, atraso ou rastreio",
    "produto_defeito": "Produto com defeito ou divergente",
    "documentos_conta": "Documentos, cadastro ou conta",
    "antifraude_bloqueio": "Conta em análise ou bloqueada",
    "cancelamento": "Cancelamento, estorno ou encerramento",
    "duvida_geral": "Dúvida geral",
}
SUPPORT_PRIORITIES = {"baixa": "Baixa", "media": "Média", "alta": "Alta", "urgente": "Urgente"}
SUPPORT_STATUSES = {"open": "Aberto", "in_review": "Em análise", "awaiting_customer": "Aguardando usuário", "dispute": "Em disputa", "resolved": "Resolvido", "closed": "Fechado"}


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
HOME_SYNC_INTERVAL_SECONDS = 1
# Evita poluir os logs com dezenas de prefetch/prerender bloqueados pelo navegador.
# O bloqueio continua ativo, mas o mesmo caminho só é logado em janela curta.
NAV_SKIP_LOG_MEMORY: dict[str, datetime] = {}
NAV_SKIP_LOG_INTERVAL_SECONDS = 20
ADMIN_PERF_LOG_THRESHOLD_MS = int(os.getenv("ADMIN_PERF_LOG_THRESHOLD_MS", "700"))

# Cache curtíssimo apenas para navegação GET do Admin.
# Evita consultar a tabela users em todo clique de aba, que nos logs custava ~645ms.
# Rotas POST/sensíveis continuam usando current_user() normal com leitura real do banco.
ADMIN_USER_NAV_CACHE: dict[str, dict[str, object]] = {}
ADMIN_USER_NAV_CACHE_TTL_SECONDS = int(os.getenv("ADMIN_USER_NAV_CACHE_TTL_SECONDS", "20"))



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


def mp_access_token() -> str:
    return (os.getenv("MP_ACCESS_TOKEN_PROD") or os.getenv("MERCADO_PAGO_ACCESS_TOKEN") or "").strip()


def mp_headers(extra_idempotency: bool = False) -> dict:
    token = mp_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if extra_idempotency:
        headers["X-Idempotency-Key"] = secrets.token_urlsafe(32)
    return headers


def mp_api_request(method: str, url: str, payload: Optional[dict] = None, timeout: int = 30) -> dict:
    if not mp_access_token():
        raise RuntimeError("MP_ACCESS_TOKEN_PROD não configurado no Railway.")

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers=mp_headers(extra_idempotency=(method.upper() == "POST")),
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Mercado Pago HTTP {exc.code}: {body[:800]}")
    except Exception as exc:
        raise RuntimeError(f"Erro de conexão com Mercado Pago: {exc}")


def create_mp_pix_payment(*, amount: float, description: str, payer_email: str) -> dict:
    amount = BR(amount)

    # PIX expira em 15 minutos
    expires_at = datetime.utcnow() + timedelta(minutes=15)

    payload = {
        "transaction_amount": amount,
        "description": description[:220],
        "payment_method_id": "pix",

        # FORMATO EXIGIDO PELO MERCADO PAGO
        "date_of_expiration": expires_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),

        "payer": {
            "email": (payer_email or "cliente@lanceiocerto.com.br").strip(),
        },
    }

    base_url = public_base_url()
    if base_url:
        payload["notification_url"] = f"{base_url}/webhook/mercadopago"

    print("[PIX PAYLOAD]", payload)
    print("[PIX EXPIRATION]", payload["date_of_expiration"])

    payment = mp_api_request(
        "POST",
        "https://api.mercadopago.com/v1/payments",
        payload,
    )

    payment_id = str(payment.get("id") or "").strip()
    status = (payment.get("status") or "pending").strip().lower()

    tx = (
        payment.get("point_of_interaction", {})
        .get("transaction_data", {})
        or {}
    )

    qr_code = tx.get("qr_code") or ""
    qr_base64 = tx.get("qr_code_base64") or ""

    print("[PIX RESPONSE]", payment)

    if not payment_id:
        raise RuntimeError(
            "Mercado Pago não retornou ID de pagamento."
        )

    if not qr_code:
        raise RuntimeError(
            "Mercado Pago não retornou código PIX."
        )

    if not qr_base64:
        raise RuntimeError(
            "Mercado Pago não retornou QR Code PIX."
        )

    return {
        "payment_id": payment_id,
        "status": status,
        "qr_code": qr_code,
        "qr_code_base64": qr_base64,
        "amount": amount,
        "expires_at": expires_at,
    }


def create_mp_card_checkout(*, request: Request, amount: float, description: str, payer_email: str, purpose: str, user_id: int, order_id: Optional[int] = None) -> dict:
    amount = BR(amount)
    base_url = public_base_url(request)
    if not base_url:
        raise RuntimeError("PUBLIC_BASE_URL não configurado. Configure a URL pública do Railway para usar cartão.")

    external_reference = f"lc_{purpose}_{user_id}_{order_id or 0}_{secrets.token_urlsafe(12)}"
    back_url = f"{base_url}/minha-conta/mercadopago/retorno?ref={external_reference}"
    payload = {
        "items": [{
            "title": description[:120],
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": amount,
        }],
        "payer": {"email": (payer_email or "cliente@lanceiocerto.com.br").strip()},
        "external_reference": external_reference,
        "back_urls": {
            "success": back_url,
            "pending": back_url,
            "failure": back_url,
        },
        "auto_return": "approved",
        "notification_url": f"{base_url}/webhook/mercadopago",
        "statement_descriptor": "LANCEIOCERTO",
        "payment_methods": {
            "excluded_payment_types": [
                {"id": "ticket"},
                {"id": "bank_transfer"},
                {"id": "debit_card"},
            ],
            "installments": 12,
        },
    }
    pref = mp_api_request("POST", "https://api.mercadopago.com/checkout/preferences", payload)
    init_point = (pref.get("init_point") or pref.get("sandbox_init_point") or "").strip()
    preference_id = str(pref.get("id") or "").strip()
    if not init_point or not preference_id:
        raise RuntimeError("O Mercado Pago não retornou o link de pagamento por cartão.")
    return {
        "external_reference": external_reference,
        "preference_id": preference_id,
        "init_point": init_point,
        "amount": amount,
    }


def get_mp_payment_status(payment_id: str) -> dict:
    payment_id = (payment_id or "").strip()
    if not payment_id:
        raise RuntimeError("payment_id vazio.")
    payment = mp_api_request("GET", f"https://api.mercadopago.com/v1/payments/{payment_id}", None)
    return {
        "payment_id": str(payment.get("id") or payment_id),
        "status": (payment.get("status") or "").strip().lower(),
        "status_detail": (payment.get("status_detail") or "").strip().lower(),
        "amount": BR(float(payment.get("transaction_amount") or 0)),
        "raw": payment,
    }


def build_pix_payment_view(payment: MercadoPagoPayment) -> dict:
    expires_at = (payment.created_at or datetime.utcnow()) + timedelta(minutes=15)
    return {
        "id": payment.id,
        "payment_id": payment.mp_payment_id,
        "amount": payment.amount,
        "status": payment.status,
        "qr_code": payment.qr_code,
        "qr_code_base64": payment.qr_code_base64,
        "expires_at": expires_at,
        # Usado pelo JS para mostrar contagem regressiva 14:59, 14:58...
        "expires_iso": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_label": fmt_br_datetime(expires_at),
    }


def release_referral_bonus_if_eligible(db: Session, request: Request, user: User, payment_amount: float) -> None:
    """Libera bônus de indicação em Créditos LC após a primeira compra válida."""
    if not user:
        return
    amount = BR(payment_amount or 0.0)
    if amount >= REFERRAL_MIN_FIRST_PURCHASE_AMOUNT and not user.first_credit_purchase_at:
        user.first_credit_purchase_at = datetime.utcnow()
        db.add(user)
    if amount < REFERRAL_MIN_FIRST_PURCHASE_AMOUNT or getattr(user, "referral_bonus_released_at", None):
        return
    referrer_id = int(getattr(user, "referred_by_user_id", 0) or 0)
    if not referrer_id:
        return

    reward = db.query(ReferralReward).filter(ReferralReward.referred_user_id == user.id).first()
    if not reward:
        reward = ReferralReward(referrer_user_id=referrer_id, referred_user_id=user.id, amount_credits=REFERRAL_BONUS_CREDITS, status="pending", reason="Aguardando validação antifraude.")
        db.add(reward)
        db.flush()

    referrer = db.get(User, referrer_id)
    reasons = []
    if not referrer:
        reasons.append("indicador inexistente")
    elif referrer.id == user.id:
        reasons.append("autoindicação")
    else:
        if (referrer.email or "").strip().lower() and (referrer.email or "").strip().lower() == (user.email or "").strip().lower():
            reasons.append("mesmo e-mail")
        if (referrer.cpf or "").strip() and (referrer.cpf or "").strip() == (user.cpf or "").strip():
            reasons.append("mesmo CPF")
        if (referrer.phone or "").strip() and (referrer.phone or "").strip() == (user.phone or "").strip():
            reasons.append("mesmo telefone")
        if (referrer.document_number or "").strip() and (referrer.document_number or "").strip() == (user.document_number or "").strip():
            reasons.append("mesmo documento")
        if (referrer.signup_ip or "").strip() and (user.signup_ip or "").strip() and referrer.signup_ip == user.signup_ip:
            reasons.append("IP de cadastro igual/suspeito")
        if (referrer.signup_device_hash or "").strip() and (user.signup_device_hash or "").strip() and referrer.signup_device_hash == user.signup_device_hash:
            reasons.append("dispositivo igual/suspeito")

    if reasons:
        reward.status = "blocked"
        reward.reason = "; ".join(reasons)
        user.fraud_risk_score = max(int(user.fraud_risk_score or 0), 70)
        db.add(reward)
        db.add(user)
        audit_event(db, request, "referral.bonus_blocked", user, "referral_reward", reward.id, reward.reason)
        return
    if reward.status == "approved":
        return

    referrer.wallet_balance = BR(float(referrer.wallet_balance or 0.0) + REFERRAL_BONUS_CREDITS)
    user.referral_bonus_released_at = datetime.utcnow()
    reward.status = "approved"
    reward.reason = "Bônus liberado após primeira compra mínima de Créditos LC do indicado."
    reward.amount_credits = REFERRAL_BONUS_CREDITS
    reward.released_at = datetime.utcnow()
    db.add(WalletTransaction(user_id=referrer.id, amount=REFERRAL_BONUS_CREDITS, kind="referral_bonus_lc", note=f"Bônus de indicação: {REFERRAL_BONUS_CREDITS:.0f} Créditos LC pelo usuário #{user.id}."))
    db.add(referrer)
    db.add(user)
    db.add(reward)
    audit_event(db, request, "referral.bonus_released", referrer, "referral_reward", reward.id, f"Indicado #{user.id} | Bônus {REFERRAL_BONUS_CREDITS:.0f} LC")


def apply_approved_mp_payment(db: Session, request: Request, payment_row: MercadoPagoPayment) -> bool:
    """Aplica aprovação do Mercado Pago uma única vez."""
    if not payment_row or (payment_row.status == "approved" and payment_row.approved_at):
        return False

    user = db.get(User, payment_row.user_id)
    if not user:
        payment_row.status = "approved"
        payment_row.approved_at = datetime.utcnow()
        db.add(payment_row)
        return False

    payment_row.status = "approved"
    payment_row.approved_at = datetime.utcnow()

    is_pix_payment = bool(payment_row.qr_code)
    payment_label = "Pix" if is_pix_payment else "Cartão"

    if payment_row.purpose == "deposit":
        user.wallet_balance = BR(float(user.wallet_balance or 0) + float(payment_row.amount or 0))
        db.add(WalletTransaction(
            user_id=user.id,
            amount=payment_row.amount,
            kind="credit_purchase_pix" if is_pix_payment else "credit_purchase_card",
            note=f"Compra de Créditos LC via {payment_label} aprovada pelo Mercado Pago. Referência MP #{payment_row.mp_payment_id}",
        ))
        release_referral_bonus_if_eligible(db, request, user, float(payment_row.amount or 0.0))
        audit_event(db, request, "wallet.credit_purchase_approved", user, "mercadopago_payment", payment_row.mp_payment_id, f"Método: {payment_label} | Créditos LC: {fmt_money(payment_row.amount)}")

    elif payment_row.purpose == "order_payment" and payment_row.order_id:
        order = db.get(WinnerOrder, payment_row.order_id)
        paid_statuses = {"aguardando_escolha", "paid", "purchased", "sent", "delivered", "finalized", "completed"}
        if order and order.user_id == user.id and order.status not in paid_statuses:
            order.status = "paid"
            order.paid_at = datetime.utcnow()
            order.admin_note = f"Pagamento confirmado via {payment_label} Mercado Pago. Aguardando escolha do modo de recebimento."
            db.add(WalletTransaction(
                user_id=user.id,
                amount=payment_row.amount,
                kind="order_payment_pix" if is_pix_payment else "order_payment_card",
                note=f"Pagamento {payment_label} do pedido #{order.id}/leilão #{order.auction_id} confirmado pelo Mercado Pago.",
            ))
            audit_event(db, request, "order.payment_approved", user, "order", order.id, f"Método: {payment_label} | Referência MP #{payment_row.mp_payment_id} | Valor R$ {fmt_money(payment_row.amount)}")
            nav_cache_clear("account:")
            db.add(order)

    db.add(user)
    db.add(payment_row)
    return True


def refresh_mp_payment(db: Session, request: Request, payment_id: str) -> MercadoPagoPayment:
    row = db.query(MercadoPagoPayment).filter(MercadoPagoPayment.mp_payment_id == str(payment_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Pagamento não encontrado.")

    mp_status = get_mp_payment_status(row.mp_payment_id)
    status = mp_status["status"] or row.status
    if status == "approved":
        apply_approved_mp_payment(db, request, row)
    else:
        row.status = status
        db.add(row)
    db.commit()
    db.refresh(row)
    return row



def reconcile_recent_pending_payments(request: Request, *, minutes: int = 60, limit: int = 20) -> dict:
    """Consulta pagamentos pendentes recentes no gateway e aplica aprovações faltantes.

    Isso é uma rede de segurança para quando o webhook falha, atrasa ou chega duplicado.
    Pode ser chamado pelo admin master ou por um job externo protegido.
    """
    checked = 0
    approved = 0
    errors: list[str] = []
    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(minutes=max(1, int(minutes)))
        rows = (
            db.query(MercadoPagoPayment)
            .filter(MercadoPagoPayment.status.in_(["pending", "in_process", "authorized"]))
            .filter(MercadoPagoPayment.created_at >= since)
            .order_by(MercadoPagoPayment.created_at.asc())
            .limit(max(1, min(int(limit), 100)))
            .all()
        )
        for row in rows:
            checked += 1
            try:
                mp_status = get_mp_payment_status(row.mp_payment_id)
                status = mp_status["status"] or row.status
                if status == "approved":
                    if apply_approved_mp_payment(db, request, row):
                        approved += 1
                else:
                    row.status = status
                    db.add(row)
                db.commit()
            except Exception as exc:
                db.rollback()
                errors.append(f"{row.mp_payment_id}: {type(exc).__name__}")
        return {"checked": checked, "approved": approved, "errors": errors[:10]}
    finally:
        db.close()



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


def support_category_label(value: str) -> str:
    return SUPPORT_CATEGORIES.get((value or "").strip(), value or "Dúvida geral")


def support_priority_label(value: str) -> str:
    return SUPPORT_PRIORITIES.get((value or "").strip(), value or "Média")


def support_status_label(value: str) -> str:
    return SUPPORT_STATUSES.get((value or "").strip(), value or "Aberto")


templates.env.globals["support_category_label"] = support_category_label
templates.env.globals["support_priority_label"] = support_priority_label
templates.env.globals["support_status_label"] = support_status_label
def public_display_status(status: str) -> str:
    """Status público do leilão.

    Internamente o banco mantém pending_payment para o vencedor pagar na área
    "Minha Conta". Publicamente, porém, o leilão já terminou e deve aparecer
    apenas como encerrado, com o vencedor.
    """
    value = (status or "").strip().lower()
    return "ended" if value == "pending_payment" else value


STATIC_FALLBACK_IMAGE = "/static/lanceio_hero_slide_01.png"


SAFE_IMAGE_URL_CACHE: dict[str, str] = {}


def safe_image_url(value: str) -> str:
    """Evita 404 em imagens de produto removidas após redeploy/cópia local.

    O teste de existência em disco é cacheado por URL para não repetir stat()
    em listas grandes do Admin.
    """
    url = (value or "").strip()
    if not url:
        return STATIC_FALLBACK_IMAGE

    # Imagem persistida no próprio registro do produto. Evita perder imagem
    # enviada pelo admin quando o Railway reinicia ou faz novo deploy.
    if url.startswith("data:image/"):
        return url

    cached = SAFE_IMAGE_URL_CACHE.get(url)
    if cached:
        return cached

    result = url
    if url.startswith("/static/uploads/"):
        local_path = BASE_DIR / url.lstrip("/")
        if not local_path.exists():
            result = STATIC_FALLBACK_IMAGE

    SAFE_IMAGE_URL_CACHE[url] = result
    return result


def public_user_name(user: Optional["User"]) -> str:
    if not user:
        return "—"
    nickname = (getattr(user, "public_name", "") or "").strip()
    if nickname:
        return f"@{nickname}"
    first = (getattr(user, "full_name", "") or "Participante").strip().split()[0]
    return first or "Participante"

def public_name_from_parts(public_name: str | None, full_name: str | None) -> str:
    """Monta o nome público sem precisar hidratar o objeto User inteiro."""
    nickname = (public_name or "").strip()
    if nickname:
        return f"@{nickname}"
    first = (full_name or "Participante").strip().split()[0]
    return first or "Participante"


def auction_last_bid_meta(db: Session, auction_id: int, viewer_user_id: Optional[int] = None) -> tuple[int, Optional[str]]:
    """Busca o último lance do leilão em uma única consulta leve.

    A versão anterior fazia MAX(id) e depois outra busca do último Bid + User.
    Em /state isso era chamado repetidamente e virava gargalo. Ordenar por id
    usa o índice ix_bids_auction_id_desc e devolve bid_id + nome em uma ida só.
    """
    row = (
        db.query(Bid.id, Bid.user_id, User.public_name, User.full_name)
        .join(User, User.id == Bid.user_id)
        .filter(Bid.auction_id == auction_id)
        .order_by(desc(Bid.id))
        .first()
    )
    if not row:
        return 0, None
    bid_id, user_id, public_name, full_name = row
    if viewer_user_id and int(user_id or 0) == int(viewer_user_id):
        return int(bid_id or 0), "Você"
    return int(bid_id or 0), public_name_from_parts(public_name, full_name)


def normalize_public_name(value: str) -> str:
    # Mantém maiúsculas/minúsculas para exibição pública, mas remove espaços extras.
    # A validação de cadastro exige apenas letras sem acento, números, ponto, underline e hífen.
    value = (value or "").strip()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[^A-Za-z0-9._-]", "", value)
    return value[:24]


def public_name_key(value: str) -> str:
    return normalize_public_name(value).lower()


def normalize_referral_code(value: str) -> str:
    value = (value or "").strip().upper()
    value = re.sub(r"[^A-Z0-9]", "", value)
    return value[:24]


def normalize_invite_prefix(value: str) -> str:
    value = (value or "").strip().upper()
    return re.sub(r"[^A-Z]", "", value)[:4]


def make_invite_code_from_prefix(db: Session, prefix: str) -> str:
    prefix = normalize_invite_prefix(prefix)
    if len(prefix) != 4:
        raise ValueError("Digite exatamente 4 letras, sem acento, espaço ou símbolos.")
    for _ in range(80):
        suffix = f"{secrets.randbelow(1_000_000):06d}"
        candidate = f"{prefix}{suffix}"
        exists = db.query(User.id).filter(func.lower(User.referral_code) == candidate.lower()).first()
        if not exists:
            return candidate
    raise ValueError("Não foi possível gerar um código único agora. Tente novamente em instantes.")


def user_has_approved_credit_purchase(db: Session, user_id: int) -> bool:
    return bool(db.query(MercadoPagoPayment.id).filter(
        MercadoPagoPayment.user_id == user_id,
        MercadoPagoPayment.purpose == "deposit",
        or_(MercadoPagoPayment.status == "approved", MercadoPagoPayment.approved_at.isnot(None)),
    ).first())


def can_apply_invite_code(db: Session, user: Optional["User"]) -> bool:
    if not user:
        return False
    if getattr(user, "referred_by_user_id", None):
        return False
    if getattr(user, "referral_bonus_released_at", None):
        return False
    if getattr(user, "first_credit_purchase_at", None):
        return False
    if user_has_approved_credit_purchase(db, int(user.id)):
        return False
    if db.query(ReferralReward.id).filter(ReferralReward.referred_user_id == user.id).first():
        return False
    return True


def apply_invite_code_for_user(db: Session, request: Request, user: "User", invite_code: str, *, require_code: bool = False) -> tuple[bool, str]:
    """Aplica código de convite antes da primeira compra aprovada de Créditos LC.

    Retorna (ok, mensagem). Quando o código é elegível, o bônus do indicador
    continua sendo liberado apenas após a primeira compra aprovada e a validação antifraude.
    """
    ensure_user_referral_code(db, user)
    code = normalize_referral_code(invite_code)
    if not code:
        if require_code:
            return False, "Informe um código de convite válido."
        return True, ""

    if not can_apply_invite_code(db, user):
        return False, "O código de convite só pode ser informado antes da primeira compra aprovada de Créditos LC."

    referrer = db.query(User).filter(func.lower(User.referral_code) == code.lower()).first()
    if not referrer:
        return False, "Código de convite não encontrado."
    if referrer.id == user.id:
        return False, "Você não pode usar o próprio código de convite."

    reasons = []
    if (referrer.email or "").strip().lower() and (referrer.email or "").strip().lower() == (user.email or "").strip().lower():
        reasons.append("mesmo e-mail")
    if (referrer.cpf or "").strip() and (referrer.cpf or "").strip() == (user.cpf or "").strip():
        reasons.append("mesmo CPF")
    if (referrer.phone or "").strip() and (referrer.phone or "").strip() == (user.phone or "").strip():
        reasons.append("mesmo telefone")
    if (referrer.document_number or "").strip() and (referrer.document_number or "").strip() == (user.document_number or "").strip():
        reasons.append("mesmo documento")
    if (referrer.signup_ip or "").strip() and (user.signup_ip or "").strip() and referrer.signup_ip == user.signup_ip:
        reasons.append("IP de cadastro igual/suspeito")
    if (referrer.signup_device_hash or "").strip() and (user.signup_device_hash or "").strip() and referrer.signup_device_hash == user.signup_device_hash:
        reasons.append("dispositivo igual/suspeito")

    reward_status = "pending"
    reward_reason = "Aguardando primeira compra válida de Créditos LC e validação antifraude."
    user_message = f"Convite aplicado. Após a primeira compra aprovada, o bônus de {REFERRAL_BONUS_CREDITS:.0f} LC será validado."
    if reasons:
        reward_status = "blocked"
        reward_reason = "; ".join(reasons)
        user.fraud_risk_score = max(int(user.fraud_risk_score or 0), 70)
        user_message = "Convite recebido, mas ficou em análise de segurança."

    user.referred_by_user_id = referrer.id
    reward = ReferralReward(
        referrer_user_id=referrer.id,
        referred_user_id=user.id,
        amount_credits=REFERRAL_BONUS_CREDITS,
        status=reward_status,
        reason=reward_reason,
    )
    db.add(user)
    db.add(reward)
    audit_event(db, request, "referral.code_applied", user, "referral_reward", None, f"Código {code} | Indicador #{referrer.id} | Status {reward_status} | {reward_reason}")
    return True, user_message


def get_referral_wallet_context(db: Session, user: "User") -> dict:
    ensure_user_referral_code(db, user)
    rewards_given = db.query(ReferralReward).options(selectinload(ReferralReward.referred)).filter(ReferralReward.referrer_user_id == user.id).order_by(desc(ReferralReward.created_at)).limit(10).all()
    reward_received = db.query(ReferralReward).filter(ReferralReward.referred_user_id == user.id).order_by(desc(ReferralReward.created_at)).first()
    referrer = db.get(User, int(user.referred_by_user_id or 0)) if getattr(user, "referred_by_user_id", None) else None
    can_customize = not bool(getattr(user, "referral_code_customized_at", None)) and not bool(rewards_given)
    return {
        "can_apply_invite_code": can_apply_invite_code(db, user),
        "can_customize_invite_code": can_customize,
        "referral_rewards_given": rewards_given,
        "referral_reward_received": reward_received,
        "referrer_user": referrer,
    }


def wallet_context(db: Session, request: Request, user: "User", **extra) -> dict:
    ctx = {"request": request, "user": user, "section": "wallet"}
    ctx.update(get_referral_wallet_context(db, user))
    ctx.update(extra)
    return ctx


def request_device_hash(request: Request) -> str:
    raw = "|".join([
        request.headers.get("user-agent", ""),
        request.headers.get("accept-language", ""),
        request.headers.get("sec-ch-ua", ""),
        request.headers.get("sec-ch-ua-platform", ""),
    ]).strip()
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:64]


def make_unique_referral_code(db: Session, user: Optional["User"] = None, seed: str = "") -> str:
    base = normalize_referral_code(seed or getattr(user, "public_name", "") or getattr(user, "full_name", "") or "LC")
    if len(base) < 4:
        base = f"LC{getattr(user, 'id', '') or secrets.token_hex(2).upper()}"
    base = base[:14]
    for attempt in range(20):
        if attempt == 0 and getattr(user, "id", None):
            candidate = normalize_referral_code(f"{base}{user.id}")
        else:
            candidate = normalize_referral_code(f"{base}{secrets.token_hex(2).upper()}")
        if len(candidate) < 4:
            candidate = f"LC{secrets.token_hex(3).upper()}"
        exists = db.query(User.id).filter(func.lower(User.referral_code) == candidate.lower()).first()
        if not exists:
            return candidate[:24]
    return f"LC{secrets.token_hex(6).upper()}"[:24]


def ensure_user_referral_code(db: Session, user: Optional["User"]) -> str:
    if not user:
        return ""
    code = normalize_referral_code(getattr(user, "referral_code", ""))
    if code:
        if code != getattr(user, "referral_code", ""):
            user.referral_code = code
            db.add(user)
        return code
    user.referral_code = make_unique_referral_code(db, user, getattr(user, "public_name", "") or getattr(user, "email", ""))
    db.add(user)
    return user.referral_code


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


AUDIT_FOLDERS = {
    "geral": {"label": "Tudo", "description": "Todos os registros arquivados na central."},
    "financeiro": {"label": "Financeiro", "description": "Entradas, saídas, taxas, estornos e ajustes de Créditos LC."},
    "leiloes": {"label": "Leilões", "description": "Criação, início, encerramento, relançamento, vencedor e mudanças críticas."},
    "pedidos": {"label": "Pedidos", "description": "Pagamento do vencedor, compra do produto, envio, entrega, disputa e finalização."},
    "usuarios": {"label": "Usuários", "description": "Cadastro, dados, KYC, banimentos, moderação e conta."},
    "admin": {"label": "Admin", "description": "Ações feitas por administradores e alterações sensíveis."},
    "arquivo_morto": {"label": "Arquivo morto", "description": "Registros antigos e operações concluídas para consulta histórica."},
}


def audit_folder_for(action: str = "", entity_type: str = "", details: str = "") -> str:
    text_value = f"{action or ''} {entity_type or ''} {details or ''}".lower()
    if any(k in text_value for k in ["wallet", "withdrawal", "payment", "deposit", "refund", "cashback", "fee", "credit", "saldo", "saque", "pagamento", "estorno"]):
        return "financeiro"
    if any(k in text_value for k in ["auction", "bid", "lance", "leil", "relist", "winner"]):
        return "leiloes"
    if any(k in text_value for k in ["order", "pedido", "tracking", "shipping", "delivered", "purchased", "dispute", "finalized", "rastreio", "envio", "entrega"]):
        return "pedidos"
    if any(k in text_value for k in ["kyc", "identity", "user", "moderation", "ban", "mute", "cadastro", "usuário", "usuario"]):
        return "usuarios"
    if any(k in text_value for k in ["admin", "superadmin", "status_changed", "manual"]):
        return "admin"
    return "geral"


def build_audit_center(db: Session, search: str = "", folder: str = "geral", date_from: str = "", date_to: str = "", limit: int = 120) -> dict:
    folder = folder if folder in AUDIT_FOLDERS else "geral"
    search_clean = (search or "").strip()
    date_from_clean = (date_from or "").strip()
    date_to_clean = (date_to or "").strip()

    query = (
        db.query(
            AuditLog.id, AuditLog.created_at, AuditLog.action, AuditLog.entity_type, AuditLog.entity_id,
            AuditLog.ip_address, AuditLog.details, User.full_name, User.public_name, User.email, User.cpf,
        )
        .outerjoin(User, User.id == AuditLog.user_id)
    )

    if date_from_clean:
        try:
            start_dt = datetime.strptime(date_from_clean, "%Y-%m-%d")
            query = query.filter(AuditLog.created_at >= start_dt)
        except Exception:
            date_from_clean = ""
    if date_to_clean:
        try:
            end_dt = datetime.strptime(date_to_clean, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(AuditLog.created_at < end_dt)
        except Exception:
            date_to_clean = ""

    if search_clean:
        like = f"%{search_clean}%"
        query = query.filter(or_(
            AuditLog.action.ilike(like),
            AuditLog.entity_type.ilike(like),
            AuditLog.entity_id.ilike(like),
            AuditLog.ip_address.ilike(like),
            AuditLog.details.ilike(like),
            User.full_name.ilike(like),
            User.public_name.ilike(like),
            User.email.ilike(like),
            User.cpf.ilike(like),
        ))

    # Puxamos um bloco maior e aplicamos a pasta em Python, porque a categoria é uma
    # organização administrativa derivada da ação/detalhes, não uma coluna fixa antiga.
    raw_rows = query.order_by(desc(AuditLog.created_at)).limit(max(limit * 5, 300)).all()
    rows = []
    folder_counts = {key: 0 for key in AUDIT_FOLDERS}
    folder_counts["geral"] = len(raw_rows)

    for r in raw_rows:
        row_folder = audit_folder_for(r.action, r.entity_type, r.details)
        folder_counts[row_folder] = folder_counts.get(row_folder, 0) + 1
        is_old_or_closed = False
        if r.created_at:
            is_old_or_closed = r.created_at < (datetime.utcnow() - timedelta(days=90))
        closed_words = f"{r.action or ''} {r.details or ''}".lower()
        if any(k in closed_words for k in ["finalized", "closed", "resolved", "delivered", "expired", "rejected", "encerrad", "finaliz", "entreg"]):
            is_old_or_closed = True
        if is_old_or_closed:
            folder_counts["arquivo_morto"] = folder_counts.get("arquivo_morto", 0) + 1

        if folder == "arquivo_morto" and not is_old_or_closed:
            continue
        if folder not in {"geral", "arquivo_morto"} and row_folder != folder:
            continue
        user_label = "Sistema"
        if r.public_name:
            user_label = f"@{r.public_name}"
        elif r.full_name:
            user_label = r.full_name
        elif r.email:
            user_label = r.email
        rows.append(SimpleNamespace(
            id=r.id,
            created_at=r.created_at,
            action=r.action or "",
            entity_type=r.entity_type or "",
            entity_id=r.entity_id or "",
            ip_address=r.ip_address or "",
            details=r.details or "",
            folder=row_folder,
            folder_label=AUDIT_FOLDERS.get(row_folder, AUDIT_FOLDERS["geral"])["label"],
            user_label=user_label,
            user=SimpleNamespace(public_name=r.public_name or "", email=r.email or "", full_name=r.full_name or "", cpf=r.cpf or "") if (r.public_name or r.email or r.full_name or r.cpf) else None,
            archived=is_old_or_closed,
        ))
        if len(rows) >= limit:
            break

    return {
        "audit_logs": rows,
        "audit_folders": [SimpleNamespace(key=k, **v, count=folder_counts.get(k, 0), active=(k == folder)) for k, v in AUDIT_FOLDERS.items()],
        "audit_folder": folder,
        "audit_search": search_clean,
        "audit_date_from": date_from_clean,
        "audit_date_to": date_to_clean,
        "audit_total_loaded": len(rows),
    }

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


def _smtp_settings() -> tuple[str, int, str, str, str, str]:
    """Lê as configurações SMTP do ambiente.

    SMTP_TLS aceita:
    - "1", "true", "yes", "starttls" para STARTTLS, normalmente porta 587.
    - "ssl" ou porta 465 para SMTP_SSL.
    - "0", "false", "no", "off" para envio sem TLS.
    """
    smtp_host = (os.getenv("SMTP_HOST") or "").strip()
    smtp_port = int(os.getenv("SMTP_PORT") or "587")
    smtp_user = (os.getenv("SMTP_USER") or "").strip()
    smtp_password = (os.getenv("SMTP_PASSWORD") or "").strip()
    smtp_from = (os.getenv("SMTP_FROM") or smtp_user or "no-reply@lanceiocerto.com.br").strip()
    smtp_tls = (os.getenv("SMTP_TLS") or "1").strip().lower()
    return smtp_host, smtp_port, smtp_user, smtp_password, smtp_from, smtp_tls


def send_smtp_email(to_email: str, subject: str, body: str, log_prefix: str = "EMAIL") -> bool:
    """Envia e-mail transacional.

    Prioridade profissional:
    1) Se BREVO_API_KEY estiver configurada, usa a API HTTPS da Brevo.
       Isso evita bloqueios/timeouts de SMTP em ambientes cloud como Railway.
    2) Se não houver BREVO_API_KEY, usa SMTP com STARTTLS ou SSL como fallback.
    """
    smtp_host, smtp_port, smtp_user, smtp_password, smtp_from, smtp_tls = _smtp_settings()
    brevo_api_key = (os.getenv("BREVO_API_KEY") or "").strip()

    sender_name, sender_email = parseaddr(smtp_from)
    sender_name = (os.getenv("BREVO_SENDER_NAME") or sender_name or APP_NAME).strip()
    sender_email = (os.getenv("BREVO_SENDER_EMAIL") or sender_email or smtp_from or smtp_user).strip()

    if brevo_api_key:
        if not sender_email:
            print(f"[{log_prefix} DEV] {to_email}: remetente não configurado para Brevo API")
            return False
        payload = {
            "sender": {"name": sender_name, "email": sender_email},
            "to": [{"email": to_email}],
            "subject": subject,
            "textContent": body,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=data,
            headers={
                "accept": "application/json",
                "api-key": brevo_api_key,
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                status = getattr(resp, "status", 0)
                if 200 <= status < 300:
                    print(f"[{log_prefix} SENT] {to_email}: Brevo API status={status}")
                    return True
                response_body = resp.read().decode("utf-8", errors="replace")
                print(f"[{log_prefix} ERROR] {to_email}: Brevo API status={status} body={response_body[:500]}")
                return False
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            print(f"[{log_prefix} ERROR] {to_email}: Brevo API HTTP {exc.code} body={response_body[:800]}")
            return False
        except Exception as exc:
            print(f"[{log_prefix} ERROR] {to_email}: Brevo API {exc}")
            return False

    if not smtp_host or not smtp_from:
        print(f"[{log_prefix} DEV] {to_email}: SMTP não configurado")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg.set_content(body)

    try:
        use_ssl = smtp_tls == "ssl" or smtp_port == 465
        if use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
                if smtp_user and smtp_password:
                    server.login(smtp_user, smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                if smtp_tls not in {"0", "false", "no", "off", "none"}:
                    server.starttls()
                if smtp_user and smtp_password:
                    server.login(smtp_user, smtp_password)
                server.send_message(msg)
        print(f"[{log_prefix} SENT] {to_email}: SMTP {smtp_host}:{smtp_port}")
        return True
    except Exception as exc:
        print(f"[{log_prefix} ERROR] {to_email}: {exc}")
        return False


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
    ok = send_smtp_email(user.email, subject, body, "EMAIL CODE")
    if not ok:
        print(f"[EMAIL CODE DEV] {user.email}: {code}")
    return ok


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
    ok = send_smtp_email(user.email, subject, body, "IDENTITY REJECTION")
    if not ok:
        print(f"[IDENTITY REJECTION DEV] {user.email}: {reason}")
    return ok


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
    ok = send_smtp_email(user.email, subject, body, "EMAIL VERIFICATION")
    if not ok:
        print(f"[EMAIL VERIFICATION DEV] {user.email}: {link}")
    return ok


def send_password_reset_email(user: User, token: str, request: Optional[Request] = None) -> bool:
    """Envia link seguro de recuperação de senha."""
    if not token:
        return False
    base_url = public_base_url(request)
    reset_link = f"{base_url}/reset-password?token={token}"
    subject = "Redefinição de senha — Lancei o Certo"
    body = (
        f"Olá, {user.full_name}.\n\n"
        "Recebemos uma solicitação para redefinir sua senha no Lancei o Certo.\n\n"
        "Para criar uma nova senha, acesse o link abaixo:\n"
        f"{reset_link}\n\n"
        "Este link expira em 1 hora e só pode ser usado uma vez.\n"
        "Se você não solicitou esta alteração, ignore este e-mail.\n\n"
        "Equipe Lancei o Certo.\n"
    )
    ok = send_smtp_email(user.email, subject, body, "PASSWORD RESET")
    if not ok:
        print(f"[PASSWORD RESET DEV] {user.email}: {reset_link}")
    return ok

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
    user_id = _session_user_id(token)
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user and getattr(user, "account_deleted", False):
        return None
    return user


def admin_current_user_fast(request: Request, db: Session) -> Optional[SimpleNamespace]:
    """Usuário leve para navegação GET do Admin.

    O diagnóstico mostrou auth≈645ms em praticamente toda aba. Para navegação
    do painel não precisamos carregar o objeto ORM completo a cada clique; basta
    um retrato curto do usuário logado para menu, permissão e cabeçalho.
    """
    token = request.cookies.get("session_token")
    if not token:
        return None

    user_id = _session_user_id(token)
    if not user_id:
        return None

    now = datetime.utcnow()
    cache_key = f"{token}:{user_id}"
    cached = ADMIN_USER_NAV_CACHE.get(cache_key)
    if cached:
        expires_at = cached.get("expires_at")
        if isinstance(expires_at, datetime) and expires_at > now:
            data = cached.get("data")
            if isinstance(data, dict):
                return SimpleNamespace(**data)
        else:
            ADMIN_USER_NAV_CACHE.pop(cache_key, None)

    row = (
        db.query(
            User.id,
            User.full_name,
            User.public_name,
            User.nickname,
            User.email,
            User.cpf,
            User.phone,
            User.is_admin,
            User.is_superadmin,
            User.is_banned,
            User.account_deleted,
            User.identity_status,
            User.wallet_balance,
        )
        .filter(User.id == user_id)
        .first()
    )
    if not row or bool(getattr(row, "account_deleted", False)):
        return None

    data = {
        "id": row.id,
        "full_name": row.full_name or "",
        "public_name": row.public_name or "",
        "nickname": row.nickname or "",
        "email": row.email or "",
        "cpf": row.cpf or "",
        "phone": row.phone or "",
        "is_admin": bool(row.is_admin),
        "is_superadmin": bool(row.is_superadmin),
        "is_banned": bool(row.is_banned),
        "account_deleted": bool(getattr(row, "account_deleted", False)),
        "identity_status": row.identity_status or "pending",
        "wallet_balance": float(row.wallet_balance or 0.0),
    }
    ADMIN_USER_NAV_CACHE[cache_key] = {
        "data": data,
        "expires_at": now + timedelta(seconds=ADMIN_USER_NAV_CACHE_TTL_SECONDS),
    }
    return SimpleNamespace(**data)


def require_user(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Faça login para continuar.")
    if user.is_banned:
        until = getattr(user, "banned_until", None)
        if until and until <= datetime.utcnow():
            user.is_banned = False
            user.banned_until = None
            db.commit()
        else:
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
    row = db.query(Bid.id).filter(Bid.auction_id == auction_id).order_by(desc(Bid.id)).first()
    return int((row[0] if row else 0) or 0)


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
        .order_by(desc(Bid.id))
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
            .order_by(desc(Bid.id))
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

    # Carrega apenas leilões que realmente precisam mudar de estado agora.
    # Antes a Home varria até 80 leilões scheduled/live em todo cache miss,
    # mesmo quando nada estava vencido. Em produção isso custava segundos.
    due_items = (
        db.query(AuctionItem)
        .filter(
            or_(
                (AuctionItem.status.in_(["scheduled", "relisted"])) & (AuctionItem.scheduled_start <= now),
                (AuctionItem.status == "live") & (AuctionItem.ends_at != None) & (AuctionItem.ends_at <= now),
            )
        )
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




def utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """Retorna ISO UTC com Z para o navegador não interpretar como horário local.

    O banco usa datetime naive em UTC. Sem o Z, alguns navegadores tratam como
    horário local do computador e o cronômetro pode abrir com diferença de horas.
    """
    if not dt:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="milliseconds") + "Z"


def server_time_payload(now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    return {
        "server_time": utc_iso(now),
        "server_time_ms": int(now.timestamp() * 1000),
    }


def money_cents(value: float | int | Decimal | None) -> int:
    """Converte valores monetários para centavos inteiros.

    O front usa esses campos como piso anti-retrocesso: preço/taxas/lances nunca
    podem voltar para trás por causa de WebSocket ou /state atrasado.
    """
    try:
        amount = Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return int(amount * 100)
    except Exception:
        return 0


def datetime_ms(dt: Optional[datetime]) -> Optional[int]:
    if not dt:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return int(dt.timestamp() * 1000)


def auction_state_guard_fields(item: AuctionItem, *, last_bid_id: int, bids_count: int, now: Optional[datetime] = None) -> dict:
    """Campos de versão usados pelo navegador para aceitar somente estado novo.

    Todo payload que atualiza a tela do leilão precisa carregar a mesma base de
    comparação. Assim /state, WebSocket e resposta do POST obedecem uma única
    regra e não conseguem fazer preço/lances/cronômetro voltarem para trás.
    """
    now = now or datetime.utcnow()
    return {
        "state_bid_id": int(last_bid_id or 0),
        "state_bids_count": int(bids_count or 0),
        "state_price_cents": money_cents(getattr(item, "current_price", 0.0)),
        "state_total_bid_fees_cents": money_cents(getattr(item, "total_bid_fees", 0.0) or 0.0),
        "state_generated_ms": int(now.timestamp() * 1000),
        "ends_at_ms": datetime_ms(getattr(item, "ends_at", None)),
        "scheduled_start_ms": datetime_ms(getattr(item, "scheduled_start", None)),
    }

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
        last_bid = db.query(Bid).options(selectinload(Bid.user)).filter(Bid.auction_id == item.id).order_by(desc(Bid.id)).first()
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
        "scheduled_start": utc_iso(item.scheduled_start),
        "start_remaining": start_remaining,
        "ends_at": utc_iso(item.ends_at),
        "remaining_seconds": remaining,
        "winner_name": public_user_name(item.winner) if item.winner else None,
        "winner_deadline": utc_iso(item.winner_deadline),
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
        "cashback": cashback_payload(item, db, user),
        **auction_state_guard_fields(item, last_bid_id=last_bid_id, bids_count=bids_count),
        **server_time_payload(),
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

        if last_bid_id_override is not None and last_bidder_override is not None:
            last_bid_id = int(last_bid_id_override or 0)
            last_bidder = last_bidder_override
        else:
            meta_bid_id, meta_bidder = auction_last_bid_meta(db, item.id, getattr(user, "id", None) if user is not None else None)
            last_bid_id = int(last_bid_id_override if last_bid_id_override is not None else meta_bid_id or 0)
            last_bidder = last_bidder_override if last_bidder_override is not None else meta_bidder

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
        "scheduled_start": utc_iso(item.scheduled_start),
        "start_remaining": start_remaining,
        "ends_at": utc_iso(item.ends_at),
        "remaining_seconds": remaining,
        "winner_name": public_user_name(item.winner) if item.winner else None,
        "winner_deadline": utc_iso(item.winner_deadline),
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
        **auction_state_guard_fields(item, last_bid_id=last_bid_id, bids_count=bids_count, now=now),
        **server_time_payload(now),
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
        "scheduled_start": utc_iso(item.scheduled_start),
        "start_remaining": start_remaining,
        "ends_at": utc_iso(item.ends_at),
        "remaining_seconds": remaining,
        "winner_name": public_user_name(item.winner) if item.winner else None,
        "winner_deadline": utc_iso(item.winner_deadline),
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
        **auction_state_guard_fields(item, last_bid_id=last_bid_id, bids_count=bids_count, now=now),
        **server_time_payload(now),
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
        "scheduled_start": utc_iso(item.scheduled_start),
        "start_remaining": start_remaining,
        "ends_at": utc_iso(item.ends_at),
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
    """Produtos retornados em consulta enxuta.

    A versão anterior carregava WinnerOrder + AuctionItem + User via ORM completo.
    Para a tela de retornados só precisamos de campos específicos, então usamos
    consulta por colunas e reduzimos peso de hidratação do SQLAlchemy.
    """
    rows = (
        db.query(
            WinnerOrder.id.label("order_id"),
            WinnerOrder.final_price,
            WinnerOrder.expired_at,
            User.public_name.label("winner_public_name"),
            User.full_name.label("winner_full_name"),
            AuctionItem.id.label("item_id"),
            AuctionItem.title,
            AuctionItem.description,
            AuctionItem.image_url,
            AuctionItem.source_store,
            AuctionItem.source_url,
            AuctionItem.source_price,
            AuctionItem.current_price,
            AuctionItem.total_bid_fees,
        )
        .join(AuctionItem, AuctionItem.id == WinnerOrder.auction_id)
        .outerjoin(User, User.id == WinnerOrder.user_id)
        .filter(WinnerOrder.status == "expired")
        .order_by(desc(WinnerOrder.expired_at), desc(WinnerOrder.created_at))
        .limit(8)
        .all()
    )

    returned = []
    seen: set[int] = set()
    for row in rows:
        if row.item_id in seen:
            continue
        seen.add(row.item_id)

        source_price = BR(row.source_price or 0.0)
        final_price = BR(row.final_price or row.current_price or 0.0)
        fees_total = BR(row.total_bid_fees or 0.0)
        reserved_cash = BR(final_price + fees_total)
        expected_total_if_paid = BR(final_price + fees_total + final_price)
        expected_profit_if_paid = BR(expected_total_if_paid - source_price)
        suggested_turbo_base = BR(max(1.0, source_price - reserved_cash))
        suggested_turbo_trigger_amount = BR(suggested_turbo_base / 2.0)

        public_name = (row.winner_public_name or "").strip()
        full_name = (row.winner_full_name or "").strip()
        winner_name = f"@{public_name}" if public_name else ((full_name.split()[0] if full_name else "—"))

        returned.append({
            "id": row.item_id,
            "title": row.title,
            "description": row.description,
            "image_url": safe_image_url(row.image_url),
            "source_store": row.source_store,
            "source_url": row.source_url,
            "source_price": source_price,
            "last_final_price": final_price,
            "accumulated_fees": fees_total,
            "reserved_cash": reserved_cash,
            "expected_total_if_paid": expected_total_if_paid,
            "expected_profit_if_paid": expected_profit_if_paid,
            "suggested_turbo_base": suggested_turbo_base,
            "suggested_turbo_trigger_amount": suggested_turbo_trigger_amount,
            "winner_name": winner_name,
            "expired_at": row.expired_at,
        })
    return returned

def _sum_scalar(db: Session, expr, *filters) -> float:
    query = db.query(func.coalesce(func.sum(expr), 0.0))
    if filters:
        query = query.filter(*filters)
    return BR(query.scalar() or 0.0)


def build_finance_periods(db: Session) -> dict:
    """Resumo diário/semanal/mensal para o admin.

    Separa lucro dos lances das taxas e calcula prejuízo quando as saídas
    registradas superam as entradas do período. Usa data de criação/pagamento
    dos registros existentes, sem alterar regra de lance.
    """
    now = datetime.utcnow()
    day_start = datetime(now.year, now.month, now.day)
    week_start = day_start - timedelta(days=day_start.weekday())
    month_start = datetime(now.year, now.month, 1)

    def period(prefix: str, start: datetime) -> dict:
        bid_row = db.query(
            func.coalesce(func.sum(Bid.fee_value), 0.0),
            func.coalesce(func.sum(Bid.price_increment), 0.0),
            func.coalesce(func.sum(Bid.bid_value), 0.0),
        ).filter(Bid.created_at >= start).first()
        fees = BR((bid_row[0] if bid_row else 0.0) or 0.0)
        bid_profit = BR((bid_row[1] if bid_row else 0.0) or 0.0)
        bid_total = BR((bid_row[2] if bid_row else 0.0) or 0.0)
        payments = BR(db.query(func.coalesce(func.sum(WinnerOrder.final_price), 0.0)).filter(
            WinnerOrder.status.in_(["paid", "processing", "purchased", "sent", "delivered", "dispute", "resolved"]),
            func.coalesce(WinnerOrder.paid_at, WinnerOrder.created_at) >= start,
        ).scalar() or 0.0)
        outgoings = BR(db.query(func.coalesce(func.sum(func.abs(WalletTransaction.amount)), 0.0)).filter(
            WalletTransaction.kind.in_(["product_outgoing", "refund", "withdrawal_paid"]),
            WalletTransaction.created_at >= start,
        ).scalar() or 0.0)
        profit = BR(fees + bid_profit + payments - outgoings)
        return {
            f"fees_{prefix}": fees,
            f"bid_profit_{prefix}": bid_profit,
            f"bid_total_{prefix}": bid_total,
            f"payments_{prefix}": payments,
            f"outgoing_{prefix}": outgoings,
            f"estimated_profit_{prefix}": profit if profit > 0 else 0.0,
            f"accumulated_loss_{prefix}": BR(abs(profit) if profit < 0 else 0.0),
        }

    result = {}
    result.update(period("today", day_start))
    result.update(period("week", week_start))
    result.update(period("month", month_start))
    return result


def build_finance_dashboard(db: Session) -> dict:
    """Indicadores completos de caixa com o mínimo de round-trips.

    Essa tela era o gargalo principal. O problema não era matemática pesada;
    era várias viagens ao banco remoto. Consolidamos os totais financeiros em
    um SELECT com subconsultas escalares e mantemos as mesmas chaves usadas no
    template.
    """
    row = db.execute(text("""
        SELECT
          (SELECT COALESCE(SUM(total_bid_spent), 0) FROM auction_items) AS total_bid_spent,
          ((SELECT COALESCE(SUM(total_bid_fees), 0) FROM auction_items) +
           (SELECT COALESCE(SUM(fee_amount), 0) FROM withdrawal_requests WHERE status IN ('pending','approved','paid'))) AS total_fees,
          (SELECT COALESCE(SUM(total_bid_fees), 0) FROM auction_items) AS bid_fee_total,
          (SELECT COALESCE(SUM(fee_amount), 0) FROM withdrawal_requests WHERE status IN ('pending','approved','paid')) AS withdrawal_fee_total,
          (SELECT COALESCE(SUM(current_price), 0) FROM auction_items) AS bid_product_cash,
          (SELECT COALESCE(SUM(final_price), 0) FROM winner_orders WHERE status IN ('paid','processing','purchased','sent','delivered')) AS total_payments,
          (SELECT COALESCE(SUM(CASE
                    WHEN (a.source_price - (COALESCE(a.current_price,0) + COALESCE(o.final_price,0))) > 0
                    THEN (a.source_price - (COALESCE(a.current_price,0) + COALESCE(o.final_price,0)))
                    ELSE 0 END), 0)
             FROM winner_orders o JOIN auction_items a ON a.id = o.auction_id
            WHERE o.status IN ('paid','processing','purchased','sent')) AS expected_products,
          (SELECT COALESCE(SUM(ABS(amount)), 0) FROM wallet_transactions WHERE kind = 'product_outgoing') AS product_outgoing,
          (SELECT COALESCE(SUM(ABS(amount)), 0) FROM wallet_transactions WHERE kind = 'refund') AS refunds,
          (SELECT COALESCE(SUM(COALESCE(NULLIF(net_amount,0), amount)), 0) FROM withdrawal_requests WHERE status IN ('pending','approved')) AS pending_withdrawals,
          (SELECT COALESCE(SUM(COALESCE(NULLIF(net_amount,0), amount)), 0) FROM withdrawal_requests WHERE status = 'paid') AS paid_withdrawals,
          (SELECT COALESCE(SUM(wallet_balance), 0) FROM users) AS user_wallet_total
    """)).mappings().first()

    def val(name: str) -> float:
        return BR((row[name] if row and row[name] is not None else 0.0) or 0.0)

    total_bid_spent = val("total_bid_spent")
    total_fees = val("total_fees")
    bid_fee_total = val("bid_fee_total") if "bid_fee_total" in row else total_fees
    withdrawal_fee_total = val("withdrawal_fee_total") if "withdrawal_fee_total" in row else 0.0
    bid_product_cash = val("bid_product_cash")
    total_payments = val("total_payments")
    expected_products = val("expected_products")
    product_outgoing = val("product_outgoing")
    refunds = val("refunds")
    pending_withdrawals = val("pending_withdrawals")
    paid_withdrawals = val("paid_withdrawals")
    user_wallet_total = val("user_wallet_total")

    total_income = BR(total_bid_spent + total_payments)
    total_outgoing = BR(product_outgoing + refunds + paid_withdrawals)
    expected_outgoing = BR(expected_products + pending_withdrawals)
    available_cash = BR(total_income - total_outgoing - pending_withdrawals)
    net_result = BR(total_income - total_outgoing)
    # Caixa real: dinheiro estimado depois de considerar todas as saídas ainda previstas
    # (complemento dos produtos + saques líquidos pendentes). Não confundir com saldo
    # dos usuários, que continua separado como custódia.
    real_cash = BR(total_income - total_outgoing - expected_outgoing)
    coverage_percent = BR((available_cash / expected_outgoing * 100.0) if expected_outgoing > 0 else 100.0)
    estimated_profit = BR(total_fees + bid_product_cash + total_payments - product_outgoing - refunds - pending_withdrawals)

    period_finance = build_finance_periods(db)

    return {
        **period_finance,
        "total_fees": total_fees,
        "bid_fee_total": bid_fee_total,
        "withdrawal_fee_total": withdrawal_fee_total,
        "bid_profit_total": bid_product_cash,
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
        "real_cash": real_cash,
        "coverage_percent": coverage_percent,
        "accumulated_loss": BR(abs(estimated_profit) if estimated_profit < 0 else 0),
        "pending_withdrawals": pending_withdrawals,
        "product_outgoing": product_outgoing,
        "expected_products": expected_products,
        "paid_withdrawals": paid_withdrawals,
        "refunds": refunds,
    }



def build_finance_dashboard_light(db: Session) -> dict:
    """Resumo financeiro enxuto para o Resumo Geral em uma ida ao banco."""
    row = db.execute(text("""
        SELECT
          (SELECT COALESCE(SUM(total_bid_spent), 0) FROM auction_items) AS total_bid_spent,
          ((SELECT COALESCE(SUM(total_bid_fees), 0) FROM auction_items) +
           (SELECT COALESCE(SUM(fee_amount), 0) FROM withdrawal_requests WHERE status IN ('pending','approved','paid'))) AS total_fees,
          (SELECT COALESCE(SUM(total_bid_fees), 0) FROM auction_items) AS bid_fee_total,
          (SELECT COALESCE(SUM(fee_amount), 0) FROM withdrawal_requests WHERE status IN ('pending','approved','paid')) AS withdrawal_fee_total,
          (SELECT COALESCE(SUM(current_price), 0) FROM auction_items) AS bid_product_cash,
          (SELECT COALESCE(SUM(final_price), 0) FROM winner_orders WHERE status IN ('paid','processing','purchased','sent','delivered')) AS total_payments
    """)).mappings().first()

    total_bid_spent = BR((row["total_bid_spent"] if row else 0.0) or 0.0)
    total_fees = BR((row["total_fees"] if row else 0.0) or 0.0)
    bid_fee_total = BR((row["bid_fee_total"] if row and "bid_fee_total" in row else total_fees) or 0.0)
    withdrawal_fee_total = BR((row["withdrawal_fee_total"] if row and "withdrawal_fee_total" in row else 0.0) or 0.0)
    bid_product_cash = BR((row["bid_product_cash"] if row else 0.0) or 0.0)
    total_payments = BR((row["total_payments"] if row else 0.0) or 0.0)
    total_income = BR(total_bid_spent + total_payments)
    expected_outgoing = 0.0
    real_cash = total_income
    coverage_percent = 100.0
    estimated_profit = BR(total_fees + total_payments + bid_product_cash)

    return {
        "total_fees": total_fees,
        "bid_fee_total": bid_fee_total,
        "withdrawal_fee_total": withdrawal_fee_total,
        "bid_profit_total": bid_product_cash,
        "total_bid_spent": total_bid_spent,
        "bid_product_cash": bid_product_cash,
        "total_payments": total_payments,
        "user_wallet_total": 0.0,
        "expected_outgoing": 0.0,
        "total_income": total_income,
        "total_outgoing": 0.0,
        "net_result": total_income,
        "estimated_profit": estimated_profit,
        "available_cash": total_income,
        "real_cash": real_cash,
        "coverage_percent": coverage_percent,
        "accumulated_loss": 0.0,
        "pending_withdrawals": 0.0,
        "product_outgoing": 0.0,
        "expected_products": 0.0,
        "paid_withdrawals": 0.0,
        "refunds": 0.0,
    }

def build_cashflow_movements(db: Session) -> list[dict]:
    """Movimentações recentes do caixa em uma consulta leve.

    Antes esta função fazia uma consulta para transações e outra para usuários.
    Em banco remoto, isso aparecia como atraso visível. Agora trazemos os dados
    já com LEFT JOIN e limitamos o histórico inicial; a página completa pode ser
    paginada depois sem travar a primeira abertura.
    """
    rows: list[dict] = []
    transactions = db.execute(text("""
        SELECT
          wt.created_at, wt.kind, wt.amount, wt.note, wt.user_id,
          u.full_name, u.public_name, u.nickname, u.cpf
        FROM wallet_transactions wt
        LEFT JOIN users u ON u.id = wt.user_id
        WHERE NOT (wt.kind = 'deposit_pending' AND ABS(COALESCE(wt.amount, 0)) < 0.00001)
        ORDER BY wt.created_at DESC
        LIMIT 100
    """)).mappings().all()

    type_labels = {
        "bid_spent": "lance_recebido",
        "payment": "pagamento_vencedor",
        "product_outgoing": "saida_produto",
        "withdrawal_reserved": "saque_reservado",
        "withdrawal_fee": "taxa_de_saque",
        "withdrawal_reversal": "saque_estornado",
        "refund": "estorno",
        "manual_adjustment": "ajuste_manual",
        "deposit_pending": "deposito_pendente",
    }

    tmp: list[dict] = []
    for tx in transactions:
        raw = BR(tx["amount"] or 0.0)
        public_name = (tx["public_name"] or "").strip()
        full_name = (tx["full_name"] or "").strip()
        nickname = (tx["nickname"] or "").strip()
        if public_name:
            name = f"@{public_name}"
        elif nickname:
            name = f"@{nickname}"
        elif full_name:
            name = full_name.split()[0]
        else:
            name = f"Usuário #{tx['user_id']}"

        if tx["kind"] in {"bid_spent", "payment"}:
            amount = abs(raw)
        elif tx["kind"] in {"withdrawal_reversal"}:
            amount = abs(raw)
        elif tx["kind"] in {"product_outgoing", "refund", "withdrawal_reserved"}:
            amount = -abs(raw)
        else:
            amount = raw

        tmp.append({
            "created_at": tx["created_at"],
            "type": type_labels.get(tx["kind"], tx["kind"]),
            "description": f"{name} • CPF {(tx['cpf'] or '—')} • {tx['note'] or 'Movimentação'}",
            "amount": BR(amount),
            "balance_after": None,
            "status": "registrado",
        })

    tmp.sort(key=lambda r: r.get("created_at") or datetime.min)
    running = 0.0
    for r in tmp:
        running = BR(running + BR(r.get("amount") or 0.0))
        r["balance_after"] = running
    tmp.sort(key=lambda r: r.get("created_at") or datetime.min, reverse=True)
    return tmp

def build_auction_results(db: Session) -> list[dict]:
    """Resultado resumido por leilão com poucas consultas.

    Mantém a visão produto a produto, mas evita hidratar objetos ORM e reduz o
    volume inicial para o que o Admin enxerga primeiro.
    """
    rows: list[dict] = []
    items = db.execute(text("""
        SELECT id, title, source_price, total_bid_spent, total_bid_fees, current_price, status, created_at
        FROM auction_items
        ORDER BY created_at DESC
        LIMIT 100
    """)).mappings().all()
    if not items:
        return rows

    item_ids = [int(i["id"]) for i in items]
    orders_by_auction: dict[int, dict] = {}
    order_rows = (
        db.query(
            WinnerOrder.id,
            WinnerOrder.auction_id,
            WinnerOrder.final_price,
            WinnerOrder.status,
            WinnerOrder.created_at,
        )
        .filter(WinnerOrder.auction_id.in_(item_ids))
        .order_by(WinnerOrder.auction_id, desc(WinnerOrder.created_at))
        .all()
    )

    for o in order_rows:
        orders_by_auction.setdefault(int(o.auction_id), {
            "id": o.id,
            "auction_id": o.auction_id,
            "final_price": o.final_price,
            "status": o.status,
            "created_at": o.created_at,
        })

    outgoing_by_order: dict[int, float] = {}
    order_ids = [int(o["id"]) for o in orders_by_auction.values() if o]
    if order_ids:
        # Mantém compatibilidade com o formato atual do note sem varrer histórico enorme.
        tx_rows = db.query(WalletTransaction.note, WalletTransaction.amount).filter(WalletTransaction.kind == "product_outgoing").order_by(desc(WalletTransaction.created_at)).limit(30).all()
        wanted_order_ids = set(order_ids)
        for note, amount in tx_rows:
            m = re.search(r"Pedido #(\d+)", note or "")
            if m:
                order_id = int(m.group(1))
                if order_id in wanted_order_ids:
                    outgoing_by_order[order_id] = BR(outgoing_by_order.get(order_id, 0.0) + abs(amount or 0.0))

    paid_statuses = {"paid", "processing", "purchased", "sent", "delivered"}
    for item in items:
        order = orders_by_auction.get(int(item["id"]))
        source_price = BR(item["source_price"] or 0.0)
        gross_bids = BR(item["total_bid_spent"] or 0.0)
        fees_total = BR(item["total_bid_fees"] or 0.0)
        product_cash = BR(item["current_price"] or 0.0)
        final_price = BR(order["final_price"] if order and order.get("status") in paid_statuses else 0.0)
        outgoing = BR(outgoing_by_order.get(int(order["id"]), 0.0) if order else 0.0)
        cash_total = BR(product_cash + final_price)
        result = BR((fees_total + product_cash + final_price) - outgoing)
        rows.append({
            "title": item["title"],
            "source_price": source_price,
            "final_price": final_price,
            "gross_bids": gross_bids,
            "fees_total": fees_total,
            "product_cash": product_cash,
            "site_complement": BR(max(0.0, source_price - cash_total)),
            "outgoing": outgoing,
            "result": result,
            "status_label": item["status"],
        })
    return rows



def cached_returned_items(db: Session, ttl_seconds: int = 600) -> list[dict]:
    cached = nav_cache_get("admin:returned-items")
    if cached is not None:
        return cached
    return nav_cache_set("admin:returned-items", build_returned_items(db), ttl_seconds)

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


ORDER_STATUS_LABELS = {
    "pending_payment": "Aguardando pagamento",
    "pending_gateway": "Aguardando confirmação do gateway",
    "paid": "Pagamento aprovado",
    "aguardando_escolha": "Aguardando escolha",
    "aguardando_compra": "Aguardando compra",
    "aguardando_link": "Aguardando link de pagamento",
    "link_recebido": "Link recebido",
    "link_rejeitado": "Link rejeitado",
    "aguardando_aprovacao": "Aguardando aprovação",
    "aprovado_para_pagamento": "Aprovado para pagamento",
    "pagamento_pedido_realizado": "Pagamento do pedido realizado",
    "processing": "Preparando compra",
    "purchased": "Compra realizada",
    "sent": "Produto enviado",
    "delivered": "Produto entregue",
    "finalized": "Pedido concluído",
    "completed": "Pedido concluído",
    "expired": "Prazo expirado",
    "dispute": "Problema informado",
    "resolved": "Resolvido",
}

ORDER_STATUS_DESCRIPTIONS = {
    "pending_payment": "Finalize o pagamento para liberar as opções de recebimento do produto.",
    "pending_gateway": "Pagamento iniciado por Pix/cartão. Aguardando confirmação oficial antes de liberar o pedido.",
    "paid": "Pagamento aprovado. Escolha como deseja seguir com o recebimento do produto.",
    "aguardando_escolha": "Escolha se prefere que o LanceioCerto faça a compra ou se você mesmo fará o pedido no site original.",
    "aguardando_compra": "Forma de recebimento escolhida. Aguardando o administrador registrar a compra do produto.",
    "aguardando_link": "Faça o pedido no site original do produto, escolha Pix e envie o link ou código de pagamento para validação.",
    "link_recebido": "Seu link foi recebido e está sendo validado.",
    "link_rejeitado": "O link enviado não passou na validação. Confira o motivo e envie novamente.",
    "aguardando_aprovacao": "O link é compatível com o produto cadastrado. Aguarde a aprovação do administrador.",
    "aprovado_para_pagamento": "O link foi aprovado. A equipe fará o pagamento do pedido.",
    "pagamento_pedido_realizado": "O pagamento do pedido foi registrado pela equipe.",
    "processing": "Pedido em preparação.",
    "purchased": "Compra realizada. O próximo passo é o envio.",
    "sent": "Produto enviado. Acompanhe o rastreio informado nesta página.",
    "delivered": "Produto marcado como entregue. Confirme o recebimento ou abra uma disputa se houver problema.",
    "finalized": "Pedido concluído com sucesso.",
    "completed": "Pedido concluído com sucesso.",
    "expired": "O prazo para pagamento expirou.",
    "dispute": "Problema informado. A equipe acompanhará o chamado.",
    "resolved": "Pedido resolvido após análise da equipe.",
}

def order_status_label(status: str) -> str:
    return ORDER_STATUS_LABELS.get((status or "").strip().lower(), status or "—")


def order_status_description(status: str) -> str:
    return ORDER_STATUS_DESCRIPTIONS.get((status or "").strip().lower(), "Acompanhe as atualizações deste pedido por aqui.")


templates.env.globals["order_status_label"] = order_status_label
templates.env.globals["order_status_description"] = order_status_description

def normalize_domain_from_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    try:
        host = (urlparse(raw).netloc or "").lower()
    except Exception:
        return ""
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def compatible_store_domains(source_store: str, source_url: str) -> set[str]:
    store = (source_store or "").strip().lower()
    base_domain = normalize_domain_from_url(source_url)
    domains = {base_domain} if base_domain else set()
    if "mercado" in store or "mercadolivre" in base_domain:
        domains.update({"mercadolivre.com.br", "mercadolivre.com", "mercadopago.com.br", "mercadopago.com"})
    if "shopee" in store or "shopee" in base_domain:
        domains.update({"shopee.com.br", "shopee.com"})
    if "amazon" in store or "amazon" in base_domain:
        domains.update({"amazon.com.br", "amazon.com"})
    return {d for d in domains if d}


def extract_marketplace_product_token(url: str) -> str:
    raw = (url or "").strip()
    try:
        parsed = urlparse(raw if re.match(r"^https?://", raw, re.I) else "https://" + raw)
    except Exception:
        return ""
    candidate = (parsed.path or "") + "?" + (parsed.query or "")
    patterns = [
        r"(MLB-?\d+)",
        r"(/[^/?#]+-)?(MLB\d+)",
        r"i\.(\d+)\.(\d+)",
        r"/dp/([A-Z0-9]{8,})",
        r"/gp/product/([A-Z0-9]{8,})",
    ]
    for pat in patterns:
        m = re.search(pat, candidate, re.I)
        if m:
            return "-".join([g for g in m.groups() if g]).upper()
    # fallback: primeira parte relevante do caminho para evitar aprovar domínios iguais com produto totalmente diferente
    parts = [p for p in (parsed.path or "").split("/") if p and p not in {"p", "product", "produto"}]
    return (parts[-1][:80].lower() if parts else "")


def validate_customer_purchase_link(order: WinnerOrder) -> tuple[bool, str, str]:
    item = order.auction
    submitted = (getattr(order, "submitted_purchase_link", "") or "").strip()
    if not submitted:
        return False, "", "Envie o link ou código de pagamento do pedido."
    submitted_domain = normalize_domain_from_url(submitted)
    if not submitted_domain:
        return False, "", "Link inválido. Envie um endereço completo do site original."
    blocked_domains = {"bit.ly", "tinyurl.com", "cutt.ly", "goo.gl", "t.co", "is.gd", "encurtador.com.br"}
    if submitted_domain in blocked_domains:
        return False, submitted_domain, "Links encurtados não são aceitos por segurança."

    source_url = getattr(item, "source_url", "") if item else ""
    source_store = getattr(item, "source_store", "") if item else ""
    allowed_domains = compatible_store_domains(source_store, source_url)
    if allowed_domains and not any(submitted_domain == d or submitted_domain.endswith("." + d) for d in allowed_domains):
        allowed_text = ", ".join(sorted(allowed_domains))
        return False, submitted_domain, f"Link incompatível. Este produto deve usar um domínio oficial compatível com: {allowed_text}."

    original_token = extract_marketplace_product_token(source_url)
    submitted_token = extract_marketplace_product_token(submitted)
    payment_domains = {"mercadopago.com.br", "mercadopago.com"}
    is_payment_domain = any(submitted_domain == d or submitted_domain.endswith("." + d) for d in payment_domains)
    # Links de pagamento do Mercado Pago nem sempre carregam o token do produto. Nesse caso validamos domínio,
    # mas deixamos o admin conferir manualmente antes de autorizar.
    if original_token and submitted_token and original_token != submitted_token and not is_payment_domain:
        return False, submitted_domain, "O domínio é compatível, mas o produto do link parece ser diferente do produto vencido."

    if is_payment_domain:
        return True, submitted_domain, "Domínio de pagamento compatível. Conferência manual do admin necessária antes de pagar."
    return True, submitted_domain, "Link compatível com o produto cadastrado. Aguardando aprovação do admin."



def build_order_timeline(order: WinnerOrder) -> list[dict]:
    status = (order.status or "").strip().lower()
    finalized = status in {"finalized", "completed"}
    paid_done = bool(order.paid_at) or status in {
        "paid", "aguardando_escolha", "aguardando_compra", "aguardando_link", "link_recebido", "link_rejeitado",
        "aguardando_aprovacao", "aprovado_para_pagamento", "pagamento_pedido_realizado",
        "processing", "purchased", "sent", "delivered", "finalized", "completed"
    }

    customer_flow = (getattr(order, "fulfillment_mode", "") == "customer_purchase") or status in {
        "aguardando_link", "link_recebido", "link_rejeitado", "aguardando_aprovacao",
        "aprovado_para_pagamento", "pagamento_pedido_realizado"
    }

    if customer_flow:
        link_done = bool(getattr(order, "submitted_purchase_link", "")) or status in {
            "link_recebido", "link_rejeitado", "aguardando_aprovacao", "aprovado_para_pagamento",
            "pagamento_pedido_realizado", "finalized", "completed"
        }
        approved_done = status in {"aprovado_para_pagamento", "pagamento_pedido_realizado", "finalized", "completed"}
        paid_order_done = status in {"pagamento_pedido_realizado", "finalized", "completed"}
        steps = [
            ("Leilão vencido", order.created_at, True, "Produto arrematado na sua conta."),
            ("Pagamento aprovado", order.paid_at, paid_done, "Aguardando confirmação do pagamento."),
            ("Forma escolhida", getattr(order, "order_choice_at", None), bool(getattr(order, "order_choice_at", None)), "Escolha como quer seguir com o pedido."),
            ("Link enviado", getattr(order, "submitted_link_checked_at", None), link_done, "Envie o link/código Pix do site original."),
            ("Aprovação admin", getattr(order, "approved_at", None), approved_done, "Aguardando conferência da equipe."),
            ("Pedido pago", order.purchased_at, paid_order_done, "Aguardando registro do pagamento do pedido."),
            ("Concluído", None, finalized, "Operação finalizada."),
        ]
    else:
        purchased_done = bool(order.purchased_at) or status in {"purchased", "sent", "delivered", "finalized", "completed"}
        sent_done = bool(order.sent_at) or status in {"sent", "delivered", "finalized", "completed"}
        delivered_done = bool(order.delivered_at) or status in {"delivered", "finalized", "completed"}
        steps = [
            ("Leilão vencido", order.created_at, True, "Produto arrematado na sua conta."),
            ("Pagamento aprovado", order.paid_at, paid_done, "Aguardando confirmação do pagamento."),
            ("Forma escolhida", getattr(order, "order_choice_at", None), bool(getattr(order, "order_choice_at", None)), "Escolha como quer receber."),
            ("Compra realizada", order.purchased_at, purchased_done, "Aguardando compra do produto."),
            ("Produto enviado", order.sent_at, sent_done, "Aguardando envio e rastreio."),
            ("Produto entregue", order.delivered_at, delivered_done, "Aguardando entrega."),
            ("Pedido concluído", None, finalized, "Finalize após receber o produto."),
        ]

    active_set = False
    timeline = []
    for label, dt, done, helper in steps:
        if done:
            state = "done"
        elif not active_set and status != "expired":
            state = "active"
            active_set = True
        else:
            state = "pending"
        timeline.append({
            "label": label,
            "date_label": fmt_br_datetime(dt) if dt else "",
            "state": state,
            "helper": helper,
        })
    if status == "expired":
        timeline.append({"label": "Prazo expirado", "date_label": fmt_br_datetime(order.expired_at), "state": "danger", "helper": "O prazo de pagamento expirou."})
    if status == "link_rejeitado":
        timeline.append({"label": "Link recusado", "date_label": fmt_br_datetime(getattr(order, "submitted_link_checked_at", None)), "state": "danger", "helper": getattr(order, "submitted_link_validation_note", "") or "Link incompatível."})
    return timeline

def build_order_history(order: WinnerOrder) -> list[dict]:
    events = [
        (order.created_at, "Leilão vencido", "Você foi o vencedor deste leilão."),
        (order.paid_at, "Pagamento aprovado", "Pagamento confirmado na plataforma."),
        (getattr(order, "order_choice_at", None), "Forma de recebimento escolhida", "O modo de atendimento do pedido foi definido."),
        (getattr(order, "submitted_link_checked_at", None), "Link de pagamento enviado", getattr(order, "submitted_link_validation_note", "") or "Link recebido para validação."),
        (getattr(order, "approved_at", None), "Link aprovado pelo admin", "A equipe aprovou o link para pagamento."),
        (order.purchased_at, "Compra realizada", "A equipe registrou a compra/pagamento do pedido."),
        (order.sent_at, "Produto enviado", f"Envio registrado. Rastreio: {order.tracking_code or 'aguardando código'}."),
        (order.delivered_at, "Produto entregue", "Entrega marcada como concluída."),
        (order.expired_at, "Prazo expirado", "O prazo de pagamento do pedido expirou."),
    ]
    history = []
    for dt, title, description in events:
        if dt:
            history.append({"date_label": fmt_br_datetime(dt), "title": title, "description": description})
    return history

def build_order_card(order: WinnerOrder) -> dict:
    item = order.auction
    source_price = BR(getattr(item, "source_price", 0.0) or 0.0)
    final_price = BR(order.final_price)
    savings = BR(max(0.0, source_price - final_price)) if source_price else 0.0
    return {
        "id": order.id,
        "auction_id": order.auction_id,
        "status": order.status,
        "status_label": order_status_label(order.status),
        "status_description": order_status_description(order.status),
        "auction_title": item.title if item else "Produto",
        "image_url": safe_image_url(item.image_url if item else ""),
        "final_price": final_price,
        "source_price": source_price,
        "savings": savings,
        "deadline_label": fmt_deadline(order.payment_deadline),
        "remaining_label": remaining_label(order.payment_deadline),
        "payment_link": order.payment_link,
        "tracking_code": order.tracking_code,
        "purchase_status": order.purchase_status,
        "purchase_link": order.purchase_link,
        "fulfillment_mode": getattr(order, "fulfillment_mode", "") or "",
        "submitted_purchase_link": getattr(order, "submitted_purchase_link", "") or "",
        "submitted_link_domain": getattr(order, "submitted_link_domain", "") or "",
        "submitted_link_valid": bool(getattr(order, "submitted_link_valid", False)),
        "submitted_link_validation_note": getattr(order, "submitted_link_validation_note", "") or "",
        "submitted_link_checked_at": fmt_br_datetime(getattr(order, "submitted_link_checked_at", None)),
        "approved_at": fmt_br_datetime(getattr(order, "approved_at", None)),
        "admin_note": order.admin_note,
        "source_store": item.source_store if item else "",
        "source_url": item.source_url if item else "",
        "created_at": fmt_br_datetime(order.created_at),
        "paid_at": fmt_br_datetime(order.paid_at),
        "purchased_at": fmt_br_datetime(order.purchased_at),
        "sent_at": fmt_br_datetime(order.sent_at),
        "delivered_at": fmt_br_datetime(order.delivered_at),
        "timeline": build_order_timeline(order),
        "history": build_order_history(order),
        "delivery_name": order.delivery_name,
        "delivery_cep": order.delivery_cep,
        "delivery_street": order.delivery_street,
        "delivery_number": order.delivery_number,
        "delivery_district": order.delivery_district,
        "delivery_city": order.delivery_city,
        "delivery_state": order.delivery_state,
    }



def cashback_payload(item: AuctionItem, db: Session, user: Optional[User] = None) -> dict:
    # Cashback/sorteio removido. Retorno fixo evita renderizar o card antigo.
    return {"available": False}
    if not getattr(item, "cashback_enabled", False):
        return {"available": False}
    event = db.query(CashbackEvent).filter(CashbackEvent.auction_id == item.id).first()
    if not event:
        return {"available": False}
    joined = False
    user_spent = 0.0
    if user:
        joined = db.query(CashbackEntry.id).filter(CashbackEntry.event_id == event.id, CashbackEntry.user_id == user.id).first() is not None
        user_spent = float(db.query(func.coalesce(func.sum(Bid.bid_value), 0.0)).filter(Bid.auction_id == item.id, Bid.user_id == user.id).scalar() or 0.0)
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
                "accepted_legal_at": "TIMESTAMP NULL",
                "accepted_legal_ip": "VARCHAR(80) DEFAULT ''",
                "accepted_legal_user_agent": "VARCHAR(600) DEFAULT ''",
                "accepted_terms_version": "VARCHAR(40) DEFAULT ''",
                "accepted_rules_version": "VARCHAR(40) DEFAULT ''",
                "accepted_privacy_version": "VARCHAR(40) DEFAULT ''",
                "email_verified": "BOOLEAN DEFAULT FALSE",
                "email_verified_at": "TIMESTAMP NULL",
                "email_verification_token": "VARCHAR(120) DEFAULT ''",
                "email_verification_code": "VARCHAR(12) DEFAULT ''",
                "email_verification_expires_at": "TIMESTAMP NULL",
                "password_reset_token": "VARCHAR(120) DEFAULT ''",
                "password_reset_expires_at": "TIMESTAMP NULL",
                "account_deleted": "BOOLEAN DEFAULT FALSE",
                "account_deleted_at": "TIMESTAMP NULL",
                "account_delete_reason": "TEXT DEFAULT ''",
                "account_delete_details": "TEXT DEFAULT ''",
                "account_delete_ip": "VARCHAR(80) DEFAULT ''",
                "ban_count": "INTEGER DEFAULT 0",
                "banned_until": "TIMESTAMP NULL",
                "ban_reason": "TEXT DEFAULT ''",
                "referral_code": "VARCHAR(40) DEFAULT ''",
                "referral_code_customized_at": "TIMESTAMP NULL",
                "referred_by_user_id": "INTEGER NULL",
                "first_credit_purchase_at": "TIMESTAMP NULL",
                "referral_bonus_released_at": "TIMESTAMP NULL",
                "signup_ip": "VARCHAR(80) DEFAULT ''",
                "signup_device_hash": "VARCHAR(120) DEFAULT ''",
                "fraud_risk_score": "INTEGER DEFAULT 0",
            }.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))
            # Garante que bancos antigos com nickname obrigatório não quebrem o cadastro.
            if "nickname" in cols:
                conn.execute(text("UPDATE users SET nickname = COALESCE(NULLIF(nickname, ''), public_name, email, 'usuario') WHERE nickname IS NULL OR nickname = ''"))

        if inspector.has_table("auction_items"):
            cols = {c["name"] for c in inspector.get_columns("auction_items")}
            # Produto usa data URL quando a imagem é enviada pelo admin.
            # No PostgreSQL, bancos antigos tinham image_url como VARCHAR(500),
            # o que estourava ao salvar base64. Convertendo para TEXT,
            # a imagem passa a acompanhar o produto no próprio banco.
            if engine.dialect.name == "postgresql" and "image_url" in cols:
                conn.execute(text("ALTER TABLE auction_items ALTER COLUMN image_url TYPE TEXT"))
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
                "fulfillment_mode": "VARCHAR(40) DEFAULT ''",
                "submitted_purchase_link": "VARCHAR(900) DEFAULT ''",
                "submitted_link_domain": "VARCHAR(160) DEFAULT ''",
                "submitted_link_valid": "BOOLEAN DEFAULT FALSE",
                "submitted_link_validation_note": "TEXT DEFAULT ''",
                "submitted_link_checked_at": "TIMESTAMP NULL",
                "approved_by_admin": "INTEGER NULL",
                "approved_at": "TIMESTAMP NULL",
                "order_choice_at": "TIMESTAMP NULL",
                "purchased_at": "TIMESTAMP NULL",
                "sent_at": "TIMESTAMP NULL",
                "delivered_at": "TIMESTAMP NULL",
            }.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE winner_orders ADD COLUMN {name} {ddl}"))

        if inspector.has_table("withdrawal_requests"):
            cols = {c["name"] for c in inspector.get_columns("withdrawal_requests")}
            for name, ddl in {
                "fee_amount": "FLOAT DEFAULT 0",
                "net_amount": "FLOAT DEFAULT 0",
            }.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE withdrawal_requests ADD COLUMN {name} {ddl}"))
            # Correção compatível com SQLite e PostgreSQL/Railway:
            # não usamos ROUND(double precision, integer), porque no PostgreSQL essa assinatura não existe.
            # O arredondamento visual continua sendo feito na tela com 2 casas decimais; aqui gravamos os valores base.
            conn.execute(text("UPDATE withdrawal_requests SET fee_amount = COALESCE(amount,0) * 0.01 WHERE COALESCE(fee_amount, 0) = 0 AND COALESCE(amount,0) > 0"))
            conn.execute(text("UPDATE withdrawal_requests SET net_amount = COALESCE(amount,0) - COALESCE(fee_amount,0) WHERE COALESCE(net_amount, 0) = 0 AND COALESCE(amount,0) > 0"))


        if inspector.has_table("support_tickets"):
            cols = {c["name"] for c in inspector.get_columns("support_tickets")}
            for name, ddl in {
                "category": "VARCHAR(60) DEFAULT 'duvida_geral'",
                "priority": "VARCHAR(20) DEFAULT 'media'",
                "result": "VARCHAR(30) DEFAULT ''",
                "assigned_admin_id": "INTEGER NULL",
                "customer_last_seen_at": "TIMESTAMP NULL",
                "last_customer_message_at": "TIMESTAMP NULL",
                "last_admin_response_at": "TIMESTAMP NULL",
                "sla_due_at": "TIMESTAMP NULL",
                "closed_at": "TIMESTAMP NULL",
            }.items():
                if name not in cols:
                    conn.execute(text(f"ALTER TABLE support_tickets ADD COLUMN {name} {ddl}"))
            conn.execute(text("UPDATE support_tickets SET category = COALESCE(NULLIF(category, ''), 'duvida_geral') WHERE category IS NULL OR category = ''"))
            conn.execute(text("UPDATE support_tickets SET priority = COALESCE(NULLIF(priority, ''), 'media') WHERE priority IS NULL OR priority = ''"))
            conn.execute(text("UPDATE support_tickets SET last_customer_message_at = created_at WHERE last_customer_message_at IS NULL"))


        # Índices leves para as consultas mais repetidas da home, conta, admin e leilão.
        # CREATE INDEX IF NOT EXISTS funciona em SQLite e PostgreSQL.
        for ddl in [
            "CREATE INDEX IF NOT EXISTS ix_auction_items_status_created ON auction_items (status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_auction_items_status_start ON auction_items (status, scheduled_start)",
            "CREATE INDEX IF NOT EXISTS ix_bids_auction_created ON bids (auction_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_bids_auction_id_desc ON bids (auction_id, id DESC)",
            "CREATE INDEX IF NOT EXISTS ix_bids_auction_user_id_desc ON bids (auction_id, user_id, id DESC)",
            "CREATE INDEX IF NOT EXISTS ix_bids_user ON bids (user_id)",
            "CREATE INDEX IF NOT EXISTS ix_bids_auction_user_created ON bids (auction_id, user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_bids_auction_user_value_created ON bids (auction_id, user_id, bid_value, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_bids_client_bid_id ON bids (client_bid_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bids_auction_user_client_bid ON bids (auction_id, user_id, client_bid_id) WHERE client_bid_id <> ''",
            "CREATE INDEX IF NOT EXISTS ix_winner_orders_user_status_created ON winner_orders (user_id, status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_winner_orders_status_created ON winner_orders (status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_winner_orders_auction_created ON winner_orders (auction_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_wallet_transactions_user_created ON wallet_transactions (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_wallet_transactions_kind_created ON wallet_transactions (kind, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_wallet_transactions_created ON wallet_transactions (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_mp_payments_mp_id ON mercadopago_payments (mp_payment_id)",
            "CREATE INDEX IF NOT EXISTS ix_mp_payments_user_created ON mercadopago_payments (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_mp_payments_order_created ON mercadopago_payments (order_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_withdrawals_status_created ON withdrawal_requests (status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_users_identity_created ON users (identity_status, created_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_lower ON users (LOWER(email)) WHERE email <> ''",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_cpf_nonempty ON users (cpf) WHERE cpf <> ''",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_phone_nonempty ON users (phone) WHERE phone <> ''",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_public_name_lower ON users (LOWER(public_name)) WHERE public_name <> ''",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_referral_code_lower ON users (LOWER(referral_code)) WHERE referral_code <> ''",
            "CREATE INDEX IF NOT EXISTS ix_users_referred_by ON users (referred_by_user_id)",
            "CREATE INDEX IF NOT EXISTS ix_referral_rewards_status_created ON referral_rewards (status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_withdrawals_user_created ON withdrawal_requests (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_support_tickets_user_created ON support_tickets (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_support_tickets_category_status ON support_tickets (category, status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_support_tickets_assigned_status ON support_tickets (assigned_admin_id, status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_support_ticket_messages_ticket_created ON support_ticket_messages (ticket_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_audit_logs_created ON audit_logs (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_suggestion_votes_key ON product_suggestion_votes (product_key)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_suggestion_vote_user_day ON product_suggestion_votes (user_id, date(created_at))",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_suggestion_nomination_week_user ON product_suggestion_nominations (week_start, user_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_suggestion_nomination_week_product ON product_suggestion_nominations (week_start, product_key)",
            "CREATE INDEX IF NOT EXISTS ix_suggestion_nomination_week ON product_suggestion_nominations (week_start)",
            "CREATE INDEX IF NOT EXISTS ix_cashback_events_auction ON cashback_events (auction_id)",
            "CREATE INDEX IF NOT EXISTS ix_auction_items_status_ends ON auction_items (status, ends_at)",
            "CREATE INDEX IF NOT EXISTS ix_winner_orders_status_deadline ON winner_orders (status, payment_deadline)",
            "CREATE INDEX IF NOT EXISTS ix_support_tickets_status_created ON support_tickets (status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_admin_direct_messages_order_created ON admin_direct_messages (order_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_chat_messages_created ON chat_messages (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_chat_messages_auction_created ON chat_messages (auction_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_users_created ON users (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_users_admin_banned ON users (is_admin, is_banned)",
            "CREATE INDEX IF NOT EXISTS ix_winner_orders_status_expired_created ON winner_orders (status, expired_at, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_auction_items_created ON auction_items (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_cashback_events_status_deadline ON cashback_events (status, join_deadline)",
            "CREATE INDEX IF NOT EXISTS ix_winner_orders_status_expired_created_desc ON winner_orders (status, expired_at DESC, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_auction_items_status_created_desc ON auction_items (status, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_users_created_desc ON users (created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_audit_logs_created_desc ON audit_logs (created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_mp_payments_status_created ON mercadopago_payments (status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_mp_payments_purpose_status_created ON mercadopago_payments (purpose, status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)",
            "CREATE INDEX IF NOT EXISTS ix_users_cpf ON users (cpf)",
            "CREATE INDEX IF NOT EXISTS ix_users_phone ON users (phone)",
            "CREATE INDEX IF NOT EXISTS ix_users_referral_code ON users (referral_code)",
            "CREATE INDEX IF NOT EXISTS ix_referral_rewards_referred_status ON referral_rewards (referred_user_id, status)",
            "CREATE INDEX IF NOT EXISTS ix_referral_rewards_referrer_status ON referral_rewards (referrer_user_id, status)",
        ]:
            try:
                conn.execute(text(ddl))
            except Exception:
                pass



def _db_ping() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        print(f"[DB-HEALTH] erro={type(exc).__name__}: {exc}")
        return False


@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "app": APP_NAME,
        "env": APP_ENV,
        "database": engine.dialect.name,
        "production": IS_PRODUCTION,
    }


@app.get("/readyz")
def readyz():
    ready = _db_ping()
    return JSONResponse(
        {
            "ok": ready,
            "database": engine.dialect.name,
            "database_ready": ready,
            "production": IS_PRODUCTION,
        },
        status_code=200 if ready else 503,
    )



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


def save_product_image_data_url(file: Optional[UploadFile]) -> str:
    """Salva imagem do produto como data URL otimizada.

    Mantém a lógica de persistir a imagem junto do cadastro do produto, mas
    reduz o peso antes de gravar no banco. Isso evita HTML enorme na Home/Admin
    quando vários cards usam imagens em base64.
    """
    if not file or not file.filename:
        return ""

    original_name = Path(file.filename).name
    ext = Path(original_name).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        raise HTTPException(status_code=400, detail="Envie uma imagem JPG, PNG, WEBP ou GIF para o produto.")

    declared_type = (file.content_type or mimetypes.guess_type(original_name)[0] or "").lower()
    if declared_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        raise HTTPException(status_code=400, detail="Formato de imagem inválido para o produto.")

    try:
        file.file.seek(0)
    except Exception:
        pass

    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Imagem vazia ou inválida.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Imagem muito grande. Envie uma imagem menor.")

    # GIF animado é preservado. Para JPG/PNG/WEBP, convertemos para WEBP e
    # limitamos a largura para acelerar Home/Admin sem mudar a regra de negócio.
    if declared_type != "image/gif":
        try:
            from PIL import Image, ImageOps

            img = Image.open(io.BytesIO(data))
            img = ImageOps.exif_transpose(img)
            if img.mode not in {"RGB", "RGBA"}:
                img = img.convert("RGB")
            if img.width > PRODUCT_IMAGE_MAX_WIDTH:
                ratio = PRODUCT_IMAGE_MAX_WIDTH / float(img.width)
                new_size = (PRODUCT_IMAGE_MAX_WIDTH, max(1, int(img.height * ratio)))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            out = io.BytesIO()
            save_kwargs = {"format": "WEBP", "quality": PRODUCT_IMAGE_WEBP_QUALITY, "method": 6}
            if img.mode == "RGBA":
                save_kwargs["lossless"] = False
            img.save(out, **save_kwargs)
            optimized = out.getvalue()
            # Usa a versão otimizada só quando ela realmente reduzir o payload.
            if optimized and len(optimized) < len(data):
                data = optimized
                declared_type = "image/webp"
        except Exception:
            # Em caso de imagem exótica/corrompida que o Pillow não processe,
            # mantém a validação de tamanho e salva o original para não quebrar o cadastro.
            pass

    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{declared_type};base64,{encoded}"


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
        elif DATABASE_URL.startswith("sqlite") and os.getenv("ENV", "development").lower() != "production":
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


def _auction_watcher_sync() -> list[tuple[int, dict]]:
    """Executa consultas do watcher em thread separada e devolve payloads prontos."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        changed_ids: set[int] = set()
        cashback_changed = False

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
            previous_status = cashback.status
            previous_winner_id = cashback.winner_user_id
            previous_amount = cashback.cashback_amount
            draw_cashback_if_due(cashback, db, now)
            if (
                cashback.status != previous_status
                or cashback.winner_user_id != previous_winner_id
                or cashback.cashback_amount != previous_amount
            ):
                cashback_changed = True
                changed_ids.add(cashback.auction_id)

        expired_orders = (
            db.query(WinnerOrder)
            .filter(WinnerOrder.status.in_(["pending_payment", "pending_gateway"]))
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

        if not changed_ids and not cashback_changed:
            db.rollback()
            return []

        db.commit()
        payloads: list[tuple[int, dict]] = []
        for auction_id in changed_ids:
            fresh = db.get(AuctionItem, auction_id)
            if fresh:
                payloads.append((auction_id, public_auction_live_payload(fresh, db)))
        return payloads
    finally:
        db.close()


async def auction_watcher():
    """Atualiza transições sem bloquear o event loop/WebSocket."""
    while True:
        await asyncio.sleep(1)
        try:
            payloads = await asyncio.to_thread(_auction_watcher_sync)
            for auction_id, payload in payloads:
                await manager.broadcast(auction_id, {"type": "auction_update", "auction": payload})
        except Exception as exc:
            print(f"[WATCHER-ERROR] {exc}")


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



def cached_home_user_suggestion_context(db: Session, user: Optional[User], ttl_seconds: int = 12) -> dict:
    """Pequeno cache por usuário para evitar 3 consultas repetidas na Home."""
    if not user:
        return {"user_week_nomination": None, "today_suggestion_vote": None, "today_suggestion_nomination": None}
    key = f"home:user-suggestion:{user.id}"
    cached = nav_cache_get(key)
    if cached is not None:
        return cached
    return nav_cache_set(key, {
        "user_week_nomination": user_week_nomination(db, user),
        "today_suggestion_vote": user_today_suggestion_vote(db, user),
        "today_suggestion_nomination": user_today_nomination(db, user),
    }, ttl_seconds)

def should_sync_home_states() -> bool:
    global HOME_SYNC_LAST_AT
    now = datetime.utcnow()
    if HOME_SYNC_LAST_AT and (now - HOME_SYNC_LAST_AT).total_seconds() < HOME_SYNC_INTERVAL_SECONDS:
        return False
    HOME_SYNC_LAST_AT = now
    return True


def cached_home_public_context(db: Session, ttl_seconds: int = 2) -> dict:
    """Dados públicos da Home com cache ultracurto e sincronização forte.

    A vitrine não pode mostrar relógio/status velho. Antes o cache de 90s podia
    deixar a Home em "Próximo" enquanto a página do leilão já estava ao vivo.
    Agora sincronizamos estados vencidos antes de consultar o cache e mantemos
    o cache em apenas 2s, suficiente para aliviar cliques repetidos sem quebrar
    a sensação de tempo real.
    """
    if should_sync_home_states() and sync_due_auction_states(db, limit=50):
        db.commit()
        nav_cache_clear("home:")

    cached = nav_cache_get("home:public")
    if cached is not None:
        return cached


    live_items = (
        db.query(AuctionItem)
        .filter(AuctionItem.status == "live")
        .order_by(AuctionItem.created_at.desc())
        .limit(8)
        .all()
    )
    upcoming_items = (
        db.query(AuctionItem)
        .filter(AuctionItem.status.in_(["scheduled", "relisted"]))
        .order_by(AuctionItem.scheduled_start.asc())
        .limit(8)
        .all()
    )
    ended_items = (
        db.query(AuctionItem)
        .options(selectinload(AuctionItem.winner))
        .filter(AuctionItem.status.in_(["pending_payment", "ended"]))
        .order_by(desc(AuctionItem.created_at))
        .limit(8)
        .all()
    )
    week_start = current_week_start_utc()
    suggestion_week_count = int(
        db.query(func.count(ProductSuggestionNomination.id))
        .filter(ProductSuggestionNomination.week_start == week_start)
        .scalar() or 0
    )

    return nav_cache_set("home:public", {
        "live_items": [public_auction_card_payload(x) for x in live_items],
        "upcoming_items": [public_auction_card_payload(x) for x in upcoming_items],
        "ended_items": [public_auction_card_payload(x) for x in ended_items],
        "suggestion_products": cached_suggestion_vote_stats(db),
        "suggestion_week_count": suggestion_week_count,
    }, ttl_seconds)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    db = SessionLocal()
    try:
        user = current_user(request, db)

        public_home = cached_home_public_context(db)
        user_suggestion = cached_home_user_suggestion_context(db, user)

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
                "user_week_nomination": user_suggestion["user_week_nomination"],
                "today_suggestion_vote": user_suggestion["today_suggestion_vote"],
                "today_suggestion_nomination": user_suggestion["today_suggestion_nomination"],
                "fee_percent": "1%",
                **server_time_payload(),
            },
        )
    finally:
        db.close()


@app.get("/api/home/state")
def home_state(request: Request):
    """Estado leve da Home, mas suficiente para atualizar cards sem reload.

    A Home não deve recarregar a página inteira quando um leilão sai de
    PRÓXIMO para AO VIVO ou de AO VIVO para ENCERRADO. Esta rota sincroniza
    apenas os leilões vencidos/iniciados e devolve os dados mínimos dos cards
    já visíveis, permitindo que o app.js atualize o DOM suavemente.
    """
    db = SessionLocal()
    try:
        # Sincronização sob demanda para evitar a Home ficar vários segundos em
        # "Conferindo/Iniciando" esperando o watcher. A consulta é limitada e
        # só pega itens realmente vencidos/iniciados.
        if sync_due_auction_states(db, limit=30):
            db.commit()
            nav_cache_clear("home:")

        live_items = (
            db.query(AuctionItem)
            .filter(AuctionItem.status == "live")
            .order_by(AuctionItem.created_at.desc())
            .limit(8)
            .all()
        )
        upcoming_items = (
            db.query(AuctionItem)
            .filter(AuctionItem.status.in_(["scheduled", "relisted"]))
            .order_by(AuctionItem.scheduled_start.asc())
            .limit(8)
            .all()
        )
        ended_items = (
            db.query(AuctionItem)
            .options(selectinload(AuctionItem.winner))
            .filter(AuctionItem.status.in_(["pending_payment", "ended"]))
            .order_by(desc(AuctionItem.created_at))
            .limit(8)
            .all()
        )

        live_payloads = [public_auction_card_payload(x) for x in live_items]
        upcoming_payloads = [public_auction_card_payload(x) for x in upcoming_items]
        ended_payloads = [public_auction_card_payload(x) for x in ended_items]

        return JSONResponse({
            "ok": True,
            "live_count": len(live_payloads),
            "upcoming_count": len(upcoming_payloads),
            "ended_count": len(ended_payloads),
            "live_ids": [x["id"] for x in live_payloads],
            "upcoming_ids": [x["id"] for x in upcoming_payloads],
            "ended_ids": [x["id"] for x in ended_payloads],
            "live_items": live_payloads,
            "upcoming_items": upcoming_payloads,
            "ended_items": ended_payloads,
            **server_time_payload(),
        })
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
@app.get("/contrato-de-servico", response_class=HTMLResponse)
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
@app.get("/regras-de-participacao", response_class=HTMLResponse)
def auction_rules_page(request: Request):
    db = SessionLocal()
    try:
        return templates.TemplateResponse("legal_rules.html", {"request": request, "user": current_user(request, db)})
    finally:
        db.close()

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": None, "form_data": {}})


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
    referral_code: str = Form(""),
    accept_legal: str = Form(""),
):
    db = SessionLocal()
    try:
        raw_public_name = (public_name or "").strip()
        clean_public_name = normalize_public_name(raw_public_name)
        clean_public_name_key = public_name_key(clean_public_name)
        clean_referral_code = normalize_referral_code(referral_code)
        clean_email = normalize_email(email)
        clean_cpf = only_digits(cpf)
        clean_phone = only_digits(phone)

        def current_register_form_data() -> dict:
            # Reenvia os campos não sensíveis para o template quando houver erro de validação.
            # A senha nunca volta preenchida por segurança, mas o usuário recebe o aviso exato no campo.
            return {
                "full_name": (full_name or "").strip(),
                "public_name": (public_name or "").strip(),
                "email": (email or "").strip(),
                "cpf": (cpf or "").strip(),
                "phone": (phone or "").strip(),
                "gender": (gender or "").strip(),
                "birth_date": (birth_date or "").strip(),
                "cep": (cep or "").strip(),
                "street": (street or "").strip(),
                "number": (number or "").strip(),
                "complement": (complement or "").strip(),
                "district": (district or "").strip(),
                "city": (city or "").strip(),
                "state": (state or "").strip().upper()[:2],
                "referral_code": (referral_code or "").strip(),
                "accept_legal": accept_legal == "on",
            }

        def fail(message: str, focus_field: str = ""):
            context = {
                "request": request,
                "error": message,
                "form_data": current_register_form_data(),
                "focus_field": focus_field,
            }
            if focus_field in {"password", "password_confirm"}:
                context["password_error"] = message
            return templates.TemplateResponse("register.html", context, status_code=400)

        if accept_legal != "on":
            return fail("Para criar a conta, aceite o Contrato de Serviço, as Regras de Participação e a Política de Privacidade.", "accept_legal")
        if len((full_name or "").strip().split()) < 2:
            return fail("Informe seu nome completo.", "full_name")
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,24}", raw_public_name or ""):
            return fail("Escolha um apelido público com 3 a 24 caracteres, sem espaço, sem acento e sem símbolos fora de ponto, underline ou hífen.", "public_name")
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", clean_email):
            return fail("Informe um e-mail válido.", "email")
        if not validate_cpf_digits(clean_cpf):
            return fail("Informe um CPF válido.", "cpf")
        if not validate_phone_digits(clean_phone):
            return fail("Informe um telefone válido com DDD.", "phone")
        clean_gender = (gender or "").strip()[:30]
        clean_birth_date = (birth_date or "").strip()[:20]
        if not clean_gender:
            return fail("Informe o gênero.", "gender")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", clean_birth_date):
            return fail("Informe a data de nascimento.", "birth_date")
        if len(password or "") < 8:
            return fail("A senha precisa ter pelo menos 8 caracteres.", "password")
        if password != password_confirm:
            return fail("A confirmação de senha não confere.", "password_confirm")

        if db.query(User).filter(func.lower(User.email) == clean_email.lower()).first():
            return fail("Este e-mail já está cadastrado.", "email")
        if db.query(User).filter(User.cpf == clean_cpf).first():
            return fail("Este CPF já está cadastrado.", "cpf")
        if db.query(User).filter(User.phone == clean_phone).first():
            return fail("Este telefone já está cadastrado.", "phone")
        if db.query(User).filter(func.lower(User.public_name) == clean_public_name_key).first():
            return fail("Este apelido público já está em uso.", "public_name")

        referrer = None
        if clean_referral_code:
            referrer = db.query(User).filter(func.lower(User.referral_code) == clean_referral_code.lower()).first()
            if not referrer:
                return fail("Código de indicação inválido.", "referral_code")

        verification_code = make_email_verification_code()
        user = User(
            full_name=full_name.strip(),
            public_name=clean_public_name,
            nickname=clean_public_name,
            email=clean_email,
            email_verified=False,
            email_verification_token="",
            email_verification_code=verification_code,
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
            accepted_legal_at=datetime.utcnow(),
            accepted_legal_ip=client_ip(request),
            accepted_legal_user_agent=(request.headers.get("user-agent") or "")[:600],
            accepted_terms_version=LEGAL_TERMS_VERSION,
            accepted_rules_version=LEGAL_RULES_VERSION,
            accepted_privacy_version=LEGAL_PRIVACY_VERSION,
            wallet_balance=0.0,
            referred_by_user_id=referrer.id if referrer else None,
            signup_ip=client_ip(request),
            signup_device_hash=request_device_hash(request),
            fraud_risk_score=0,
        )
        db.add(user)
        db.flush()
        user.referral_code = make_unique_referral_code(db, user, clean_public_name)
        db.add(user)
        if referrer:
            db.add(ReferralReward(referrer_user_id=referrer.id, referred_user_id=user.id, amount_credits=REFERRAL_BONUS_CREDITS, status="pending", reason="Aguardando primeira compra válida de Créditos LC e validação antifraude."))
        sent = send_verification_code_email(user, request)
        referral_detail = f" | Indicação: {referrer.id}" if referrer else ""
        audit_event(db, request, "user.register", user, "user", user.id, f"Cadastro criado. Aceite legal registrado ({LEGAL_TERMS_VERSION}/{LEGAL_RULES_VERSION}/{LEGAL_PRIVACY_VERSION}). Código de confirmação de e-mail enviado e KYC pendente.{referral_detail}")
        db.commit()
        suffix = "&email_sent=1" if sent else "&email_dev=1"
        return RedirectResponse(f"/cadastro/confirmar-email?email={clean_email}{suffix}", status_code=303)
    finally:
        db.close()


@app.get("/cadastro/confirmar-email", response_class=HTMLResponse)
def register_confirm_email_page(request: Request, email: str = "", email_sent: int = 0, email_dev: int = 0):
    """Página intermediária após cadastro.

    A confirmação é feita por código de 6 dígitos enviado por e-mail.
    Depois de validar o código, o usuário segue para o envio de documentos.
    """
    return templates.TemplateResponse(
        "register_email_confirm.html",
        {
            "request": request,
            "email": normalize_email(email),
            "error": None,
            "email_sent": email_sent,
            "email_dev": email_dev,
        },
    )


@app.post("/cadastro/confirmar-email")
def register_confirm_email_submit(request: Request, email: str = Form(...), code: str = Form(...)):
    db = SessionLocal()
    try:
        clean_email = normalize_email(email)
        clean_code = re.sub(r"\D", "", code or "")[:6]
        user = db.query(User).filter(User.email == clean_email).first()

        def render_error(message: str, status_code: int = 400):
            return templates.TemplateResponse(
                "register_email_confirm.html",
                {
                    "request": request,
                    "email": clean_email,
                    "error": message,
                    "email_sent": 0,
                    "email_dev": 0,
                },
                status_code=status_code,
            )

        if not user:
            return render_error("Não foi possível localizar este cadastro. Verifique o e-mail informado.")
        if user.email_verified:
            response = RedirectResponse("/cadastro/documentos", status_code=303)
            return _create_session_response(response, user.id)
        if not clean_code or len(clean_code) != 6:
            return render_error("Digite o código de 6 dígitos enviado para seu e-mail.")
        if user.email_verification_expires_at and user.email_verification_expires_at < datetime.utcnow():
            user.email_verification_code = make_email_verification_code()
            user.email_verification_token = ""
            user.email_verification_expires_at = datetime.utcnow() + timedelta(minutes=15)
            sent = send_verification_code_email(user, request)
            audit_event(db, request, "user.email_code_expired", user, "user", user.id, "Código expirado. Novo código enviado.")
            db.commit()
            suffix = "&email_sent=1" if sent else "&email_dev=1"
            return RedirectResponse(f"/cadastro/confirmar-email?email={clean_email}{suffix}", status_code=303)
        if not hmac.compare_digest((user.email_verification_code or "").strip(), clean_code):
            audit_event(db, request, "user.email_code_invalid", user, "user", user.id, "Tentativa de confirmação com código inválido.")
            db.commit()
            return render_error("Código inválido. Confira os números enviados por e-mail ou solicite um novo código.")

        user.email_verified = True
        user.email_verified_at = datetime.utcnow()
        user.email_verification_token = ""
        user.email_verification_code = ""
        user.email_verification_expires_at = None
        audit_event(db, request, "user.email_verified", user, "user", user.id, "E-mail confirmado por código de 6 dígitos.")
        db.commit()
        response = RedirectResponse("/cadastro/documentos", status_code=303)
        return _create_session_response(response, user.id)
    finally:
        db.close()


@app.post("/cadastro/reenviar-codigo")
def register_resend_code(request: Request, email: str = Form(...)):
    return register_resend_confirmation_code(request, email)


@app.post("/cadastro/reenviar-confirmacao")
def register_resend_confirmation_code(request: Request, email: str = Form(...)):
    db = SessionLocal()
    try:
        clean_email = normalize_email(email)
        user = db.query(User).filter(User.email == clean_email).first()
        email_dev = False
        if user and not user.email_verified:
            user.email_verification_token = ""
            user.email_verification_code = make_email_verification_code()
            user.email_verification_expires_at = datetime.utcnow() + timedelta(minutes=15)
            email_dev = not send_verification_code_email(user, request)
            audit_event(db, request, "user.email_confirmation_resent", user, "user", user.id, "Código de confirmação reenviado após cadastro.")
            db.commit()
        suffix = "&email_dev=1" if email_dev else "&email_sent=1"
        return RedirectResponse(f"/cadastro/confirmar-email?email={clean_email}{suffix}", status_code=303)
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
        try:
            user.document_file_url = save_uploaded_image(document_front_file)
            user.document_back_file_url = save_uploaded_image(document_back_file)
            user.selfie_file_url = save_uploaded_image(selfie_file)
            if residence_proof_file and residence_proof_file.filename:
                user.residence_proof_file_url = save_uploaded_image(residence_proof_file)
        except HTTPException as exc:
            return templates.TemplateResponse("register_documents.html", {"request": request, "user": user, "error": str(exc.detail), "success": None}, status_code=exc.status_code)
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
    """Compatibilidade com links antigos.

    O fluxo atual usa código de 6 dígitos. Se alguém abrir um link antigo ainda válido,
    confirmamos e seguimos para documentos; caso contrário, mostramos a tela de código.
    """
    db = SessionLocal()
    try:
        token = (token or "").strip()
        user = db.query(User).filter(User.email_verification_token == token).first() if token else None
        if not user:
            return templates.TemplateResponse("login.html", {"request": request, "error": "Confirmação inválida ou expirada. Solicite um novo código de confirmação.", "created": 0, "email_pending": 0, "email_verified": 0}, status_code=400)
        if user.email_verification_expires_at and user.email_verification_expires_at < datetime.utcnow():
            user.email_verification_token = ""
            user.email_verification_code = make_email_verification_code()
            user.email_verification_expires_at = datetime.utcnow() + timedelta(minutes=15)
            send_verification_code_email(user, request)
            db.commit()
            return RedirectResponse(f"/cadastro/confirmar-email?email={user.email}&email_sent=1", status_code=303)
        user.email_verified = True
        user.email_verified_at = datetime.utcnow()
        user.email_verification_token = ""
        user.email_verification_code = ""
        user.email_verification_expires_at = None
        audit_event(db, request, "user.email_verified", user, "user", user.id, "E-mail confirmado por link antigo ainda válido.")
        db.commit()
        response = RedirectResponse("/cadastro/documentos", status_code=303)
        return _create_session_response(response, user.id)
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
            user.email_verification_token = ""
            user.email_verification_code = make_email_verification_code()
            user.email_verification_expires_at = datetime.utcnow() + timedelta(minutes=15)
            send_verification_code_email(user, request)
            audit_event(db, request, "user.email_confirmation_resent", user, "user", user.id, "Reenvio de código de confirmação solicitado.")
            db.commit()
        return templates.TemplateResponse("login.html", {"request": request, "error": "Se a conta existir e ainda estiver pendente, um novo código de confirmação será enviado.", "created": 0, "email_pending": 1, "email_verified": 0})
    finally:
        db.close()

@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    db = SessionLocal()
    try:
        return templates.TemplateResponse("forgot_password.html", {"request": request, "user": current_user(request, db)})
    finally:
        db.close()


@app.post("/forgot-password", response_class=HTMLResponse)
def forgot_password_submit(request: Request, email: str = Form(...)):
    db = SessionLocal()
    generic_message = "Se existir uma conta com esse e-mail, enviamos um link de recuperação."
    try:
        email_clean = normalize_email(email)
        user = db.query(User).filter(func.lower(User.email) == email_clean).first() if email_clean else None
        email_dev = False
        if user:
            token = secrets.token_urlsafe(40)
            user.password_reset_token = token
            user.password_reset_expires_at = datetime.utcnow() + timedelta(hours=1)
            db.commit()
            email_dev = not send_password_reset_email(user, token, request)
            audit_event(db, request, "user.password_reset_requested", user, "user", user.id, "Link de recuperação de senha solicitado.")
            db.commit()
        return templates.TemplateResponse(
            "forgot_password.html",
            {"request": request, "user": current_user(request, db), "success": generic_message, "email_dev": email_dev},
        )
    finally:
        db.close()


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str = ""):
    db = SessionLocal()
    try:
        token_clean = (token or "").strip()
        user = db.query(User).filter(User.password_reset_token == token_clean).first() if token_clean else None
        if not user or not user.password_reset_expires_at or user.password_reset_expires_at < datetime.utcnow():
            return templates.TemplateResponse(
                "reset_password.html",
                {"request": request, "user": current_user(request, db), "error": "Link inválido ou expirado. Solicite um novo link de recuperação."},
            )
        return templates.TemplateResponse("reset_password.html", {"request": request, "user": current_user(request, db), "token": token_clean})
    finally:
        db.close()


@app.post("/reset-password", response_class=HTMLResponse)
def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    db = SessionLocal()
    try:
        token_clean = (token or "").strip()
        user = db.query(User).filter(User.password_reset_token == token_clean).first() if token_clean else None
        if not user or not user.password_reset_expires_at or user.password_reset_expires_at < datetime.utcnow():
            return templates.TemplateResponse(
                "reset_password.html",
                {"request": request, "user": current_user(request, db), "error": "Link inválido ou expirado. Solicite um novo link de recuperação."},
            )
        if password != password_confirm:
            return templates.TemplateResponse(
                "reset_password.html",
                {"request": request, "user": current_user(request, db), "token": token_clean, "error": "As senhas não coincidem."},
            )
        if len(password or "") < 8:
            return templates.TemplateResponse(
                "reset_password.html",
                {"request": request, "user": current_user(request, db), "token": token_clean, "error": "A senha deve ter pelo menos 8 caracteres."},
            )
        user.password = hash_password(password)
        user.password_reset_token = ""
        user.password_reset_expires_at = None
        audit_event(db, request, "user.password_reset_completed", user, "user", user.id, "Senha redefinida por link seguro.")
        db.commit()
        return RedirectResponse("/?login=1&password_reset=1", status_code=303)
    finally:
        db.close()


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, created: int = 0, email_pending: int = 0, email_verified: int = 0, email_sent: int = 0, email_dev: int = 0, password_reset: int = 0):
    # O padrão visual do site é sempre abrir o login em modal sobre a tela atual.
    # Mantemos a rota /login apenas como compatibilidade para links antigos,
    # redirecionando para a home com o modal aberto automaticamente.
    params = []
    if email_verified:
        params.append("email_verified=1")
    if email_dev:
        params.append("email_dev=1")
    if password_reset:
        params.append("password_reset=1")
    params.insert(0, "login=1")
    return RedirectResponse("/?" + "&".join(params), status_code=303)


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

        if getattr(user, "account_deleted", False):
            if wants_json:
                return JSONResponse({"ok": False, "detail": "Esta conta foi excluída e não pode mais acessar a plataforma."}, status_code=403)
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Esta conta foi excluída e não pode mais acessar a plataforma.",
                "login_identifier": login_identifier,
            }, status_code=403)

        # A confirmação de e-mail pertence apenas ao fluxo de cadastro.
        # Depois que a conta foi criada, o login não deve ficar bloqueado nem reenviar link automaticamente.
        # Isso evita o aviso indevido no modal de entrada e mantém o padrão decidido para a plataforma.

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

        return _create_session_response(response, user.id)
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
            .options(selectinload(ChatMessage.user))
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
                "item": public_auction_payload(item, db, user),
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
            if IS_SQLITE:
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

            # Idempotência otimizada:
            # não fazemos SELECT prévio em todo clique. O índice único
            # uq_bids_auction_user_client_bid protege duplicidade; se o mesmo
            # client_bid_id repetir, o IntegrityError abaixo faz rollback e
            # devolve o estado oficial. Isso reduz uma consulta no caminho quente
            # de todo lance aceito.

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
            if not IS_SQLITE:
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
                    detail="Créditos LC insuficientes para dar este lance.",
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
                            **server_time_payload(),
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
                last_bidder="Você",
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
        # Uma fila assíncrona por leilão evita ocupar várias threads com lances
        # parados esperando o mesmo lock. O processamento continua serializado,
        # mas o servidor fica mais estável em pico.
        async with AUCTION_BID_ASYNC_LOCKS[auction_id]:
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
        # Agora o broadcast só enfileira a atualização por conexão; não espera rede.
        # Isso faz outro computador receber o lance praticamente junto da resposta do POST.
        await manager.broadcast(auction_id, {"type": "auction_update", "auction": public_payload})
    return JSONResponse({"ok": True, "auction": private_payload, "button_cooldown": button_cooldown, "cooldown_scope": "button"})

def _auction_state_sync(request: Request, auction_id: int) -> tuple[dict, Optional[dict], bool]:
    """Monta /state fora do event loop para não travar WebSocket.

    SQLAlchemy usado aqui é síncrono. Em uma rota async direta ele bloqueava o
    mesmo loop que mantém WebSocket vivo. A rota abaixo chama esta função via
    asyncio.to_thread().
    """
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
            db.refresh(item)

        private_payload = public_auction_live_payload(item, db, user=user)
        public_payload = None
        if changed:
            public_payload = public_auction_live_payload(item, db, user_turbo_eligible_override=None)
        return {"ok": True, "auction": private_payload}, public_payload, bool(finished_now)
    finally:
        db.close()


@app.get("/api/auction/{auction_id}/state")
async def auction_state(request: Request, auction_id: int):
    body, public_payload, finished_now = await asyncio.to_thread(_auction_state_sync, request, auction_id)
    if public_payload:
        await manager.broadcast(auction_id, {"type": "auction_update", "auction": public_payload})
    if finished_now:
        asyncio.create_task(asyncio.to_thread(ensure_finished_auction_side_effects, auction_id))
    return JSONResponse(body)


@app.post("/api/auction/{auction_id}/cashback/join")
def join_cashback(request: Request, auction_id: int):
    raise HTTPException(status_code=410, detail="Cashback/sorteio foi removido. Créditos promocionais serão tratados como Bônus LC em regras próprias da plataforma.")
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
        spent = float(db.query(func.coalesce(func.sum(Bid.bid_value), 0.0)).filter(Bid.auction_id == auction_id, Bid.user_id == user.id).scalar() or 0.0)
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

def _send_chat_sync(request: Request, auction_id: int, message: str) -> dict:
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
        return {
            "type": "chat_message",
            "message": {"author": public_user_name(user), "text": msg.message, "created_at": msg.created_at.strftime("%H:%M:%S")},
        }
    finally:
        db.close()


@app.post("/api/auction/{auction_id}/chat")
async def send_chat(request: Request, auction_id: int, message: str = Form(...)):
    payload = await asyncio.to_thread(_send_chat_sync, request, auction_id, message)
    await manager.broadcast(auction_id, payload)
    return JSONResponse({"ok": True})


def cached_account_dashboard_context(db: Session, user: User, ttl_seconds: int = 60) -> dict:
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
        .filter(WinnerOrder.user_id == user.id, WinnerOrder.status.in_(["pending_payment", "pending_gateway"]))
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
    open_tickets = (
        db.query(SupportTicket)
        .filter(SupportTicket.user_id == user.id, SupportTicket.status.in_(["open", "in_review", "dispute"]))
        .order_by(desc(SupportTicket.created_at))
        .limit(3)
        .all()
    )
    return nav_cache_set(key, {
        "stats": stats,
        "pending_orders": [build_order_card(x) for x in pending_orders[:3]],
        "latest_orders": [build_order_card(x) for x in won_orders[:5]],
        "open_tickets": open_tickets,
    }, ttl_seconds)


@app.get("/minha-conta", response_class=HTMLResponse)
def my_account(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        ensure_user_referral_code(db, user)
        db.commit()
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
                "open_tickets": account_summary.get("open_tickets", []),
                "wallet_transactions": [],
                "withdrawals": [],
                "tickets": [],
                "orders_raw": [],
                "account_status_label": account_status_label(user),
            },
        )
    finally:
        db.close()


@app.get("/minha-conta/saldo", response_class=HTMLResponse)
def my_wallet(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        ensure_user_referral_code(db, user)
        db.commit()
        return templates.TemplateResponse("account_pages.html", wallet_context(db, request, user))
    finally:
        db.close()


@app.post("/minha-conta/convite/gerar")
def account_generate_invite_code(request: Request, code_prefix: str = Form(...)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        ensure_user_referral_code(db, user)
        if getattr(user, "referral_code_customized_at", None):
            return templates.TemplateResponse(
                "account_pages.html",
                wallet_context(db, request, user, error="Seu código de convite já foi definido e é fixo da sua conta."),
                status_code=400,
            )
        if db.query(ReferralReward.id).filter(ReferralReward.referrer_user_id == user.id).first():
            return templates.TemplateResponse(
                "account_pages.html",
                wallet_context(db, request, user, error="Seu código já foi usado em indicações e não pode mais ser alterado."),
                status_code=400,
            )
        try:
            new_code = make_invite_code_from_prefix(db, code_prefix)
        except ValueError as exc:
            return templates.TemplateResponse(
                "account_pages.html",
                wallet_context(db, request, user, error=str(exc)),
                status_code=400,
            )
        old_code = user.referral_code or ""
        user.referral_code = new_code
        user.referral_code_customized_at = datetime.utcnow()
        db.add(user)
        audit_event(db, request, "referral.code_customized", user, "user", user.id, f"Código anterior: {old_code or '—'} | Novo código: {new_code}")
        db.commit()
        db.refresh(user)
        return templates.TemplateResponse(
            "account_pages.html",
            wallet_context(db, request, user, success=f"Código de convite criado: {new_code}. Ele agora é o código fixo da sua conta."),
        )
    finally:
        db.close()


@app.post("/minha-conta/convite/aplicar")
def account_apply_invite_code(request: Request, invite_code: str = Form(...)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        ok, msg = apply_invite_code_for_user(db, request, user, invite_code, require_code=True)
        if not ok:
            return templates.TemplateResponse(
                "account_pages.html",
                wallet_context(db, request, user, error=msg),
                status_code=400,
            )
        db.commit()
        db.refresh(user)
        return templates.TemplateResponse("account_pages.html", wallet_context(db, request, user, success=msg))
    finally:
        db.close()


@app.post("/minha-conta/saldo")
@app.post("/minha-conta/saldo/pix")
def account_add_balance_pix(request: Request, amount: float = Form(...), invite_code: str = Form("")):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        amount = BR(amount)
        if amount < LC_MIN_CREDIT_PURCHASE_AMOUNT:
            return templates.TemplateResponse(
                "account_pages.html",
                wallet_context(db, request, user, error=f"A compra mínima é de R$ {fmt_money(LC_MIN_CREDIT_PURCHASE_AMOUNT)} em Créditos LC."),
                status_code=400,
            )

        invite_notice = ""
        if (invite_code or "").strip():
            ok, invite_notice = apply_invite_code_for_user(db, request, user, invite_code, require_code=True)
            if not ok:
                return templates.TemplateResponse(
                    "account_pages.html",
                    wallet_context(db, request, user, error=invite_notice),
                    status_code=400,
                )

        try:
            mp = create_mp_pix_payment(
                amount=amount,
                description=f"Compra de Créditos LC - LanceioCerto - usuário #{user.id}",
                payer_email=user.email,
            )
        except Exception as exc:
            return templates.TemplateResponse(
                "account_pages.html",
                wallet_context(db, request, user, error=str(exc)),
                status_code=400,
            )

        payment = MercadoPagoPayment(
            user_id=user.id,
            order_id=None,
            purpose="deposit",
            mp_payment_id=mp["payment_id"],
            amount=amount,
            status=mp["status"],
            qr_code=mp["qr_code"],
            qr_code_base64=mp["qr_code_base64"],
            description=f"Compra de Créditos LC - usuário #{user.id}",
        )
        db.add(payment)
        db.add(WalletTransaction(
            user_id=user.id,
            amount=0.0,
            kind="deposit_pix_pending",
            note=f"Pix de compra de Créditos LC gerado: R$ {fmt_money(amount)} | MP #{mp['payment_id']}",
        ))
        audit_event(db, request, "wallet.credit_purchase_pix_created", user, "mercadopago_payment", mp["payment_id"], f"Valor R$ {fmt_money(amount)}")
        db.commit()
        db.refresh(payment)

        return templates.TemplateResponse(
            "account_pages.html",
            wallet_context(db, request, user, pix_payment=build_pix_payment_view(payment), success=invite_notice or None),
        )
    finally:
        db.close()


@app.post("/minha-conta/saldo/cartao")
def account_add_balance_card(request: Request, amount: float = Form(...), invite_code: str = Form("")):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        amount = BR(amount)
        if amount < LC_MIN_CREDIT_PURCHASE_AMOUNT:
            return templates.TemplateResponse(
                "account_pages.html",
                wallet_context(db, request, user, error=f"A compra mínima é de R$ {fmt_money(LC_MIN_CREDIT_PURCHASE_AMOUNT)} em Créditos LC."),
                status_code=400,
            )
        if (invite_code or "").strip():
            ok, invite_notice = apply_invite_code_for_user(db, request, user, invite_code, require_code=True)
            if not ok:
                return templates.TemplateResponse(
                    "account_pages.html",
                    wallet_context(db, request, user, error=invite_notice),
                    status_code=400,
                )

        try:
            checkout = create_mp_card_checkout(
                request=request,
                amount=amount,
                description=f"Compra de Créditos LC - LanceioCerto - usuário #{user.id}",
                payer_email=user.email,
                purpose="deposit",
                user_id=user.id,
            )
        except Exception as exc:
            return templates.TemplateResponse(
                "account_pages.html",
                wallet_context(db, request, user, error=str(exc)),
                status_code=400,
            )

        payment = MercadoPagoPayment(
            user_id=user.id,
            order_id=None,
            purpose="deposit",
            mp_payment_id=checkout["external_reference"],
            amount=amount,
            status="pending",
            qr_code="",
            qr_code_base64="",
            description=f"Cartão | preferência {checkout['preference_id']}",
        )
        db.add(payment)
        db.add(WalletTransaction(
            user_id=user.id,
            amount=0.0,
            kind="deposit_card_pending",
            note=f"Checkout de cartão para Créditos LC gerado: R$ {fmt_money(amount)} | Preferência MP {checkout['preference_id']}",
        ))
        audit_event(db, request, "wallet.credit_purchase_card_created", user, "mercadopago_payment", checkout["external_reference"], f"Valor R$ {fmt_money(amount)}")
        db.commit()
        return RedirectResponse(checkout["init_point"], status_code=303)
    finally:
        db.close()


@app.get("/minha-conta/saldo/pix/status")
def account_pix_deposit_status(request: Request, payment_id: str):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        row = db.query(MercadoPagoPayment).filter(
            MercadoPagoPayment.user_id == user.id,
            MercadoPagoPayment.mp_payment_id == str(payment_id),
            MercadoPagoPayment.purpose == "deposit",
        ).first()
        if not row:
            return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
        row = refresh_mp_payment(db, request, row.mp_payment_id)
        db.refresh(user)
        return {
            "ok": True,
            "approved": row.status == "approved",
            "status": row.status,
            "wallet_balance": BR(user.wallet_balance or 0),
            "lc_credits": BR(user.wallet_balance or 0),
        }
    finally:
        db.close()



@app.get("/minha-conta/auditoria", response_class=HTMLResponse)
def my_audit_redirect(request: Request):
    return RedirectResponse("/minha-conta/suporte", status_code=303)


@app.get("/minha-conta/comprovantes", response_class=HTMLResponse)
def my_receipts(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        cache_key = f"account:receipts:{user.id}"
        data = nav_cache_get(cache_key)
        if data is None:
            transactions = db.query(WalletTransaction).filter(WalletTransaction.user_id == user.id).order_by(desc(WalletTransaction.created_at)).limit(25).all()
            orders = db.query(WinnerOrder).options(selectinload(WinnerOrder.auction)).filter(WinnerOrder.user_id == user.id).order_by(desc(WinnerOrder.created_at)).limit(25).all()
            audits = db.query(AuditLog).filter(AuditLog.user_id == user.id).order_by(desc(AuditLog.created_at)).limit(25).all()
            data = {"wallet_transactions": transactions, "orders_raw": orders, "audit_logs": audits}
            nav_cache_set(cache_key, data, 60)
        return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, "section": "receipts", **data})
    finally:
        db.close()


@app.get("/minha-conta/suporte", response_class=HTMLResponse)
def my_support(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        ticket_raw = (request.query_params.get("ticket") or "").strip()
        ticket_id = int(ticket_raw) if ticket_raw.isdigit() else None
        ctx = support_context_for_user(db, user, ticket_id=ticket_id)
        db.commit()
        return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, **ctx})
    finally:
        db.close()


@app.post("/minha-conta/suporte/novo")
async def my_support_create(request: Request, category: str = Form("duvida_geral"), subject: str = Form(""), message: str = Form(""), order_id: int = Form(0), proof_file: UploadFile | None = File(None)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        category = category if category in SUPPORT_CATEGORIES else "duvida_geral"
        subject = (subject or "").strip()[:160]
        message = (message or "").strip()
        if len(message) < 10:
            ctx = support_context_for_user(db, user, error="Descreva o problema com pelo menos 10 caracteres.")
            return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, **ctx}, status_code=400)
        proof_url = save_uploaded_image(proof_file) if proof_file and proof_file.filename else ""
        valid_order_id = None
        if order_id:
            order = db.query(WinnerOrder).filter(WinnerOrder.id == order_id, WinnerOrder.user_id == user.id).first()
            if order:
                valid_order_id = order.id
        priority = support_category_priority(category)
        ticket = SupportTicket(user_id=user.id, order_id=valid_order_id, category=category, priority=priority, subject=subject or support_category_label(category), message=message, proof_url=proof_url, status="open", last_customer_message_at=datetime.utcnow(), sla_due_at=support_sla_due(category, priority))
        db.add(ticket)
        db.flush()
        support_add_message(db, ticket, message, user_id=user.id, message_type="customer", proof_url=proof_url)
        audit_event(db, request, "support.ticket_created", user, "support_ticket", ticket.id, f"Categoria: {category} | Prioridade: {priority}")
        nav_cache_clear()
        db.commit()
        return RedirectResponse(f"/minha-conta/suporte?ticket={ticket.id}", status_code=303)
    finally:
        db.close()


@app.post("/minha-conta/suporte/{ticket_id}/responder")
async def my_support_reply(request: Request, ticket_id: int, message: str = Form(""), proof_file: UploadFile | None = File(None)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id, SupportTicket.user_id == user.id).first()
        if not ticket:
            raise HTTPException(status_code=404, detail="Chamado não encontrado.")
        body = (message or "").strip()
        if len(body) < 2 and not (proof_file and proof_file.filename):
            ctx = support_context_for_user(db, user, ticket_id=ticket.id, error="Digite uma resposta ou envie um anexo.")
            return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, **ctx}, status_code=400)
        proof_url = save_uploaded_image(proof_file) if proof_file and proof_file.filename else ""
        support_add_message(db, ticket, body or "Anexo enviado pelo usuário.", user_id=user.id, message_type="customer", proof_url=proof_url)
        audit_event(db, request, "support.customer_replied", user, "support_ticket", ticket.id, "Usuário respondeu ao chamado.")
        nav_cache_clear()
        db.commit()
        return RedirectResponse(f"/minha-conta/suporte?ticket={ticket.id}", status_code=303)
    finally:
        db.close()


@app.get("/minha-conta/cadastro", response_class=HTMLResponse)
def my_profile(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        ensure_user_referral_code(db, user)
        db.commit()
        return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, "section": "profile"})
    finally:
        db.close()


@app.get("/minha-conta/deletar", response_class=HTMLResponse)
def account_delete_page(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, "section": "delete_account", "error": None})
    finally:
        db.close()


@app.post("/minha-conta/deletar")
def account_delete_submit(
    request: Request,
    delete_reason: str = Form(...),
    delete_details: str = Form(""),
    password: str = Form(...),
    confirm_delete: str = Form(""),
):
    db = SessionLocal()
    try:
        user = require_user(request, db)

        if getattr(user, "is_admin", False) or getattr(user, "is_superadmin", False):
            return templates.TemplateResponse("account_pages.html", {
                "request": request,
                "user": user,
                "section": "delete_account",
                "error": "Contas administrativas não podem ser excluídas por esta tela. Use uma conta comum para testes.",
            }, status_code=400)

        if not verify_password((password or "").strip(), user.password):
            return templates.TemplateResponse("account_pages.html", {
                "request": request,
                "user": user,
                "section": "delete_account",
                "error": "Senha incorreta. A conta não foi excluída.",
            }, status_code=400)

        if confirm_delete != "1":
            return templates.TemplateResponse("account_pages.html", {
                "request": request,
                "user": user,
                "section": "delete_account",
                "error": "Confirme que entende que a exclusão não libera o histórico financeiro e operacional já registrado.",
            }, status_code=400)

        reason_labels = {
            "nao_uso": "Não uso mais a plataforma",
            "privacidade": "Privacidade/dados pessoais",
            "dificuldade": "Dificuldade para usar o site",
            "problema_pagamento": "Problema com pagamento ou Créditos LC",
            "problema_leilao": "Problema com leilão ou pedido",
            "teste": "Conta criada apenas para teste",
            "outro": "Outro motivo",
        }
        reason_key = (delete_reason or "").strip()
        reason_label = reason_labels.get(reason_key, "Outro motivo")
        details = (delete_details or "").strip()[:1000]
        now = datetime.utcnow()

        original = {
            "id": user.id,
            "full_name": user.full_name or "",
            "public_name": user.public_name or user.nickname or "",
            "email": user.email or "",
            "cpf": user.cpf or "",
            "phone": user.phone or "",
            "wallet_balance": BR(user.wallet_balance or 0.0),
            "identity_status": user.identity_status or "",
        }

        audit_event(
            db,
            request,
            "user.account_deleted",
            user,
            "user",
            user.id,
            (
                f"Conta excluída pelo próprio usuário. "
                f"Nome: {original['full_name']} | E-mail: {original['email']} | CPF: {original['cpf']} | "
                f"Créditos LC no momento: {fmt_money(original['wallet_balance'])} LC | "
                f"Motivo: {reason_label} | Detalhes: {details or 'sem detalhes'}"
            ),
        )

        deletion_stamp = f"{user.id}-{int(now.timestamp())}-{secrets.token_hex(4)}"
        user.account_deleted = True
        user.account_deleted_at = now
        user.account_delete_reason = reason_label[:500]
        user.account_delete_details = details
        user.account_delete_ip = client_ip(request)
        user.is_banned = True
        user.chat_muted = True
        user.ban_reason = "Conta excluída pelo próprio usuário."
        user.banned_until = None
        user.email_verified = False
        user.email_verification_token = ""
        user.email_verification_code = ""
        user.email_verification_expires_at = None
        user.password_reset_token = ""
        user.password_reset_expires_at = None
        user.password = hash_password(secrets.token_urlsafe(32))

        # Anonimização: libera e-mail/CPF/apelido para novo cadastro e mantém o histórico financeiro/auditoria por ID.
        user.full_name = "Conta excluída"
        user.public_name = f"excluido{user.id}"[:24]
        user.nickname = user.public_name
        user.email = f"deleted-{deletion_stamp}@deleted.lanceiocerto.local"[:160]
        user.cpf = f"deleted-{user.id}"[:20]
        user.phone = ""
        user.gender = ""
        user.birth_date = ""
        user.cep = ""
        user.street = ""
        user.number = ""
        user.complement = ""
        user.district = ""
        user.city = ""
        user.state = ""
        user.identity_status = "deleted"
        user.identity_note = "Conta excluída pelo próprio usuário."
        user.document_number = ""
        user.document_file_url = ""
        user.document_back_file_url = ""
        user.selfie_file_url = ""
        user.residence_proof_file_url = ""

        nav_cache_clear()
        db.commit()

        token = request.cookies.get("session_token")
        if token and token in SESSIONS:
            del SESSIONS[token]
        response = RedirectResponse("/?account_deleted=1", status_code=303)
        response.delete_cookie("session_token")
        return response
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
        cache_key = f"account:participations:{user.id}"
        data = nav_cache_get(cache_key)
        if data is None:
            rows = (
                db.query(
                    Bid.auction_id.label("auction_id"),
                    func.count(Bid.id).label("total_bids"),
                    func.coalesce(func.sum(Bid.bid_value), 0.0).label("total_spent"),
                    func.max(Bid.created_at).label("last_activity"),
                    AuctionItem.title.label("title"),
                    AuctionItem.image_url.label("image_url"),
                    AuctionItem.status.label("status"),
                    AuctionItem.winner_user_id.label("winner_user_id"),
                )
                .join(AuctionItem, AuctionItem.id == Bid.auction_id)
                .filter(Bid.user_id == user.id)
                .group_by(Bid.auction_id, AuctionItem.title, AuctionItem.image_url, AuctionItem.status, AuctionItem.winner_user_id)
                .order_by(desc(func.max(Bid.created_at)))
                .limit(40)
                .all()
            )
            data = []
            for row in rows:
                data.append({
                    "auction_id": row.auction_id,
                    "title": row.title,
                    "image_url": safe_image_url(row.image_url),
                    "status": public_display_status(row.status),
                    "total_bids": int(row.total_bids or 0),
                    "total_spent": BR(row.total_spent or 0.0),
                    "won": row.winner_user_id == user.id,
                    "last_activity": row.last_activity.strftime("%d/%m/%Y %H:%M") if row.last_activity else "—",
                })
            nav_cache_set(cache_key, data, 90)
        return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, "section": "participations", "items": data})
    finally:
        db.close()


@app.get("/minha-conta/ganhos", response_class=HTMLResponse)
def my_wins(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        cache_key = f"account:wins:{user.id}"
        data = nav_cache_get(cache_key)
        if data is None:
            orders = db.query(WinnerOrder).options(selectinload(WinnerOrder.auction)).filter(WinnerOrder.user_id == user.id).order_by(desc(WinnerOrder.created_at)).limit(20).all()
            data = [build_order_card(x) for x in orders]
            nav_cache_set(cache_key, data, 90)
        return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, "section": "wins", "orders": data})
    finally:
        db.close()


@app.get("/minha-conta/pedido/{order_id}", response_class=HTMLResponse)
def my_order_detail(request: Request, order_id: int):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        order = (
            db.query(WinnerOrder)
            .options(selectinload(WinnerOrder.auction))
            .filter(WinnerOrder.id == order_id, WinnerOrder.user_id == user.id)
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        return templates.TemplateResponse(
            "account_pages.html",
            {"request": request, "user": user, "section": "order_detail", "order": build_order_card(order)},
        )
    finally:
        db.close()



@app.post("/minha-conta/pedido/{order_id}/escolher-atendimento")
def account_choose_order_fulfillment(request: Request, order_id: int, fulfillment_mode: str = Form(...)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        order = (
            db.query(WinnerOrder)
            .options(selectinload(WinnerOrder.auction))
            .filter(WinnerOrder.id == order_id, WinnerOrder.user_id == user.id)
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        if order.status not in {"paid", "aguardando_escolha"}:
            raise HTTPException(status_code=400, detail="Este pedido ainda não está liberado para escolha.")
        now = datetime.utcnow()
        if fulfillment_mode == "customer_purchase":
            order.fulfillment_mode = "customer_purchase"
            order.status = "aguardando_link"
            order.order_choice_at = now
            order.admin_note = ((order.admin_note or "") + "\nCliente escolheu fazer o próprio pedido no site original.").strip()
            audit_event(db, request, "order.fulfillment.customer_purchase", user, "order", order.id, "Cliente escolheu enviar link/código de pagamento do pedido.")
        elif fulfillment_mode == "site_purchase":
            order.fulfillment_mode = "site_purchase"
            # A escolha da forma de recebimento NÃO significa que o produto já foi comprado.
            # Mantemos o pedido aguardando ação do admin até a compra ser registrada manualmente.
            order.status = "aguardando_compra"
            order.order_choice_at = now
            order.admin_note = ((order.admin_note or "") + "\nCliente escolheu compra assistida pelo LanceioCerto. Aguardando registro da compra pelo admin.").strip()
            audit_event(db, request, "order.fulfillment.site_purchase", user, "order", order.id, "Cliente escolheu compra e envio pelo site.")
        else:
            raise HTTPException(status_code=400, detail="Opção inválida.")
        nav_cache_clear()
        db.commit()
        return RedirectResponse(f"/minha-conta/pedido/{order.id}", status_code=303)
    finally:
        db.close()


@app.post("/minha-conta/pedido/{order_id}/enviar-link-pagamento")
def account_submit_customer_purchase_link(request: Request, order_id: int, submitted_purchase_link: str = Form(...)):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        order = (
            db.query(WinnerOrder)
            .options(selectinload(WinnerOrder.auction))
            .filter(WinnerOrder.id == order_id, WinnerOrder.user_id == user.id)
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        if order.fulfillment_mode != "customer_purchase" and order.status not in {"aguardando_link", "link_rejeitado"}:
            raise HTTPException(status_code=400, detail="Este pedido não está aguardando link do cliente.")
        order.fulfillment_mode = "customer_purchase"
        order.submitted_purchase_link = (submitted_purchase_link or "").strip()
        valid, domain, note = validate_customer_purchase_link(order)
        order.submitted_link_domain = domain
        order.submitted_link_valid = bool(valid)
        order.submitted_link_validation_note = note
        order.submitted_link_checked_at = datetime.utcnow()
        order.status = "aguardando_aprovacao" if valid else "link_rejeitado"
        audit_event(db, request, "order.customer_link_validated" if valid else "order.customer_link_rejected", user, "order", order.id, f"Domínio: {domain or '—'} | Resultado: {note}")
        nav_cache_clear()
        db.commit()
        return RedirectResponse(f"/minha-conta/pedido/{order.id}", status_code=303)
    finally:
        db.close()


@app.post("/minha-conta/pedido/{order_id}/confirmar-recebimento")
def account_confirm_order_received(request: Request, order_id: int):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        order = (
            db.query(WinnerOrder)
            .filter(WinnerOrder.id == order_id, WinnerOrder.user_id == user.id)
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        if order.status != "delivered":
            raise HTTPException(status_code=400, detail="O recebimento só pode ser confirmado após a entrega ser registrada.")
        order.status = "finalized"
        order.admin_note = ((order.admin_note or "") + "\nCliente confirmou o recebimento do produto.").strip()
        audit_event(db, request, "order.received_confirmed", user, "order", order.id, f"Cliente confirmou recebimento do pedido #{order.id}.")
        nav_cache_clear()
        db.commit()
        return RedirectResponse(f"/minha-conta/pedido/{order.id}", status_code=303)
    finally:
        db.close()


@app.get("/minha-conta/pagamentos", response_class=HTMLResponse)
def my_pending_payments(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        cache_key = f"account:payments:{user.id}"
        data = nav_cache_get(cache_key)
        if data is None:
            orders = (
                db.query(WinnerOrder)
                .options(selectinload(WinnerOrder.auction))
                .filter(WinnerOrder.user_id == user.id, WinnerOrder.status.in_(["pending_payment", "pending_gateway"]))
                .order_by(desc(WinnerOrder.created_at))
                .limit(20)
                .all()
            )
            data = [build_order_card(x) for x in orders]
            nav_cache_set(cache_key, data, 45)
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

        # 💰 PAGAMENTO COM CRÉDITOS LC
        if payment_method == "wallet":
            if user.wallet_balance < order.final_price:
                raise HTTPException(status_code=400, detail="Créditos LC insuficientes.")

            user.wallet_balance = BR(user.wallet_balance - order.final_price)

            db.add(WalletTransaction(
                user_id=user.id,
                amount=-order.final_price,
                kind="payment",
                note=f"Pagamento do leilão #{order.auction_id}"
            ))

        # PIX Mercado Pago: gera QR Code e só marca como pago depois da confirmação oficial.
        elif payment_method == "pix":
            try:
                mp = create_mp_pix_payment(
                    amount=order.final_price,
                    description=f"Pagamento do pedido #{order.id} - LanceioCerto",
                    payer_email=user.email,
                )
            except Exception as exc:
                return templates.TemplateResponse(
                    "account_pages.html",
                    {
                        "request": request,
                        "user": user,
                        "section": "checkout",
                        "order": build_order_card(order),
                        "entity": order,
                        "error": str(exc),
                    },
                    status_code=400,
                )

            order.status = "pending_gateway"
            order.admin_note = "Pix Mercado Pago gerado. Aguardando confirmação oficial do pagamento."
            payment = MercadoPagoPayment(
                user_id=user.id,
                order_id=order.id,
                purpose="order_payment",
                mp_payment_id=mp["payment_id"],
                amount=order.final_price,
                status=mp["status"],
                qr_code=mp["qr_code"],
                qr_code_base64=mp["qr_code_base64"],
                description=f"Pagamento do pedido #{order.id}",
            )
            db.add(payment)
            audit_event(db, request, "order.payment_pix_created", user, "order", order.id, f"Pagamento MP #{mp['payment_id']} | Valor R$ {fmt_money(order.final_price)}")
            db.commit()
            db.refresh(payment)
            db.refresh(order)
            return templates.TemplateResponse(
                "account_pages.html",
                {
                    "request": request,
                    "user": user,
                    "section": "checkout",
                    "order": build_order_card(order),
                    "entity": order,
                    "pix_payment": build_pix_payment_view(payment),
                },
            )

        elif payment_method in ["card", "credit_card"]:
            try:
                checkout = create_mp_card_checkout(
                    request=request,
                    amount=order.final_price,
                    description=f"Pagamento do pedido #{order.id} - LanceioCerto",
                    payer_email=user.email,
                    purpose="order_payment",
                    user_id=user.id,
                    order_id=order.id,
                )
            except Exception as exc:
                return templates.TemplateResponse(
                    "account_pages.html",
                    {
                        "request": request,
                        "user": user,
                        "section": "checkout",
                        "order": build_order_card(order),
                        "entity": order,
                        "error": str(exc),
                    },
                    status_code=400,
                )

            order.status = "pending_gateway"
            order.admin_note = "Pagamento por cartão Mercado Pago iniciado. Aguardando confirmação oficial."
            payment = MercadoPagoPayment(
                user_id=user.id,
                order_id=order.id,
                purpose="order_payment",
                mp_payment_id=checkout["external_reference"],
                amount=order.final_price,
                status="pending",
                qr_code="",
                qr_code_base64="",
                description=f"Cartão pedido #{order.id} | preferência {checkout['preference_id']}",
            )
            db.add(payment)
            db.add(order)
            audit_event(db, request, "order.payment_card_created", user, "order", order.id, f"Referência {checkout['external_reference']} | Valor R$ {fmt_money(order.final_price)}")
            db.commit()
            return RedirectResponse(checkout["init_point"], status_code=303)

        # Pagamento com saldo interno confirmado.
        # Importante: pagar o pedido NÃO é a mesma coisa que comprar/enviar o produto.
        # Depois do pagamento, o usuário ainda precisa escolher a forma de recebimento.
        order.status = "paid"
        order.paid_at = datetime.utcnow()
        order.admin_note = "Pagamento confirmado com saldo interno. Aguardando escolha do modo de recebimento."
        audit_event(db, request, "order.payment_wallet_confirmed", user, "order", order.id, f"Valor: R$ {fmt_money(order.final_price)}")

        db.commit()

        return RedirectResponse("/minha-conta/ganhos", status_code=303)

    finally:
        db.close()


@app.get("/minha-conta/pagamentos/{auction_id}/pix/status")
def order_pix_payment_status(request: Request, auction_id: int, payment_id: str):
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
            return JSONResponse({"ok": False, "reason": "order_not_found"}, status_code=404)
        row = db.query(MercadoPagoPayment).filter(
            MercadoPagoPayment.user_id == user.id,
            MercadoPagoPayment.order_id == order.id,
            MercadoPagoPayment.mp_payment_id == str(payment_id),
            MercadoPagoPayment.purpose == "order_payment",
        ).first()
        if not row:
            return JSONResponse({"ok": False, "reason": "payment_not_found"}, status_code=404)
        row = refresh_mp_payment(db, request, row.mp_payment_id)
        db.refresh(order)
        return {
            "ok": True,
            "approved": row.status == "approved",
            "status": row.status,
            "redirect_url": f"/minha-conta/pedido/{order.id}" if row.status == "approved" else "",
        }
    finally:
        db.close()


@app.get("/minha-conta/mercadopago/retorno")
def mercadopago_checkout_return(request: Request):
    """Retorno do Checkout Pro. A confirmação real continua sendo feita por consulta/webhook."""
    payment_id = (
        request.query_params.get("payment_id")
        or request.query_params.get("collection_id")
        or request.query_params.get("id")
        or ""
    )
    external_reference = (request.query_params.get("external_reference") or request.query_params.get("ref") or "").strip()
    db = SessionLocal()
    try:
        user = require_user(request, db)
        row = None
        if payment_id:
            try:
                mp_status = get_mp_payment_status(payment_id)
                raw = mp_status.get("raw") or {}
                external_reference = external_reference or str(raw.get("external_reference") or "").strip()
            except Exception as exc:
                print(f"[MP RETURN LOOKUP ERROR] payment_id={payment_id}: {exc}")
        if external_reference:
            row = db.query(MercadoPagoPayment).filter(
                MercadoPagoPayment.user_id == user.id,
                MercadoPagoPayment.mp_payment_id == external_reference,
            ).first()
        if row and payment_id:
            # Mantém a referência local enquanto pendente; ao aprovar, aplica com segurança.
            status = get_mp_payment_status(payment_id)
            if status["status"] == "approved":
                row.mp_payment_id = str(payment_id)
                apply_approved_mp_payment(db, request, row)
            else:
                row.status = status["status"] or row.status
                db.add(row)
            db.commit()
            db.refresh(row)
            if row.purpose == "order_payment" and row.order_id:
                return RedirectResponse(f"/minha-conta/pedido/{row.order_id}", status_code=303)
        return RedirectResponse("/minha-conta/saldo", status_code=303)
    finally:
        db.close()


@app.api_route("/webhook/mercadopago", methods=["GET", "POST"])
async def mercadopago_webhook(request: Request):
    payment_id = (
        request.query_params.get("id")
        or request.query_params.get("data.id")
        or request.query_params.get("payment_id")
        or ""
    )
    if request.method == "POST":
        try:
            payload = await request.json()
            data = payload.get("data") if isinstance(payload, dict) else {}
            if isinstance(data, dict):
                payment_id = payment_id or str(data.get("id") or "")
            payment_id = payment_id or str(payload.get("id") or payload.get("payment_id") or "")
        except Exception:
            pass

    payment_id = str(payment_id or "").strip()
    if not payment_id:
        return {"ok": True, "ignored": "missing_payment_id"}

    db = SessionLocal()
    try:
        row = db.query(MercadoPagoPayment).filter(MercadoPagoPayment.mp_payment_id == payment_id).first()
        try:
            mp_status = get_mp_payment_status(payment_id)
            external_reference = str((mp_status.get("raw") or {}).get("external_reference") or "").strip()
            if not row and external_reference:
                row = db.query(MercadoPagoPayment).filter(MercadoPagoPayment.mp_payment_id == external_reference).first()
                if row:
                    # Troca a referência temporária pelo ID real do pagamento para evitar reaplicar.
                    row.mp_payment_id = payment_id
                    db.add(row)
                    db.flush()
            if not row:
                return {"ok": True, "ignored": "payment_not_registered"}
            if mp_status["status"] == "approved":
                apply_approved_mp_payment(db, request, row)
            else:
                row.status = mp_status["status"] or row.status
                db.add(row)
            db.commit()
            db.refresh(row)
        except Exception as exc:
            print(f"[MP WEBHOOK ERROR] payment_id={payment_id}: {exc}")
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "status": row.status}
    finally:
        db.close()


@app.get("/minha-conta/expirados", response_class=HTMLResponse)
def my_expired_orders(request: Request):
    db = SessionLocal()
    try:
        user = require_user(request, db)
        cache_key = f"account:expired:{user.id}"
        data = nav_cache_get(cache_key)
        if data is None:
            orders = (
                db.query(WinnerOrder)
                .options(selectinload(WinnerOrder.auction))
                .filter(WinnerOrder.user_id == user.id, WinnerOrder.status == "expired")
                .order_by(desc(WinnerOrder.created_at))
                .limit(30)
                .all()
            )
            data = [build_order_card(x) for x in orders]
            nav_cache_set(cache_key, data, 90)
        return templates.TemplateResponse("account_pages.html", {"request": request, "user": user, "section": "expired", "orders": data})
    finally:
        db.close()



def build_finished_auctions(db: Session) -> list[dict]:
    """Histórico finalizado usando colunas específicas, sem hidratar ORM completo."""
    finished_status = ["ended", "delivered", "expired"]
    items = (
        db.query(
            AuctionItem.id,
            AuctionItem.title,
            AuctionItem.source_price,
            AuctionItem.current_price,
            AuctionItem.total_bid_fees,
            AuctionItem.status,
            AuctionItem.winner_user_id,
            User.public_name.label("winner_public_name"),
            User.full_name.label("winner_full_name"),
        )
        .outerjoin(User, User.id == AuctionItem.winner_user_id)
        .filter(AuctionItem.status.in_(finished_status))
        .order_by(desc(AuctionItem.created_at))
        .limit(25)
        .all()
    )
    item_ids = [row.id for row in items]
    latest_order_by_auction: dict[int, object] = {}
    if item_ids:
        orders = (
            db.query(
                WinnerOrder.auction_id,
                WinnerOrder.final_price,
                WinnerOrder.status,
                WinnerOrder.created_at,
            )
            .filter(WinnerOrder.auction_id.in_(item_ids))
            .order_by(desc(WinnerOrder.created_at))
            .limit(80)
            .all()
        )
        for order in orders:
            latest_order_by_auction.setdefault(order.auction_id, order)

    rows = []
    paid_statuses = {"paid", "processing", "purchased", "sent", "delivered"}
    for item in items:
        order = latest_order_by_auction.get(item.id)
        source_price = BR(item.source_price or 0.0)
        final_price = BR((order.final_price if order else item.current_price) or 0.0)
        fees_total = BR(item.total_bid_fees or 0.0)
        paid_total = final_price if order and order.status in paid_statuses else 0.0
        result = BR(final_price + fees_total + paid_total - source_price)
        public_name = (item.winner_public_name or "").strip()
        full_name = (item.winner_full_name or "").strip()
        rows.append({
            "title": item.title,
            "winner_name": f"@{public_name}" if public_name else ((full_name.split()[0] if full_name else "") or ""),
            "source_price": source_price,
            "final_price": final_price,
            "fees_total": fees_total,
            "site_complement": BR(max(0.0, source_price - (final_price + fees_total))),
            "result": result,
            "status_label": order.status if order else item.status,
        })
    return rows

def user_audit_map(db: Session, users: list) -> dict[int, dict]:
    """Auditoria mínima para a ficha do usuário.

    A aba Usuários precisa abrir rápido. Por isso carregamos só as últimas
    movimentações dos usuários visíveis, sem contar tabelas inteiras a cada
    abertura. A auditoria profunda continua na aba Auditoria/Consulta.
    """
    user_ids = [getattr(u, "id", None) for u in users if getattr(u, "id", None)]
    if not user_ids:
        return {}

    data = {uid: {"bids": 0, "orders": 0, "transactions": [], "withdrawals": [], "tickets": []} for uid in user_ids}

    transactions = (
        db.query(WalletTransaction)
        .filter(WalletTransaction.user_id.in_(user_ids))
        .order_by(desc(WalletTransaction.created_at))
        .limit(30)
        .all()
    )
    for tx in transactions:
        bucket = data.get(tx.user_id)
        if bucket is not None and len(bucket["transactions"]) < 2:
            bucket["transactions"].append(tx)
    return data



def support_sla_due(category: str, priority: str) -> datetime:
    category = (category or "duvida_geral").strip()
    priority = (priority or "media").strip()
    if priority == "urgente" or category in {"pagamento_creditos", "creditos_descontados", "produto_defeito", "antifraude_bloqueio"}:
        hours = 2
    elif priority == "alta" or category in {"lance_leilao", "arremate_pagamento", "compra_assistida"}:
        hours = 4
    elif priority == "baixa":
        hours = 24
    else:
        hours = 8
    return datetime.utcnow() + timedelta(hours=hours)


def support_ticket_code(ticket_id: int) -> str:
    return f"LC-{int(ticket_id or 0):06d}"


def support_ticket_can_adjust(admin: User, amount: float) -> bool:
    if bool(getattr(admin, "is_superadmin", False)):
        return True
    return abs(BR(amount)) <= SUPPORT_ADMIN_ADJUST_LIMIT


def support_add_message(db: Session, ticket: SupportTicket, body: str, *, user_id: Optional[int] = None, admin_id: Optional[int] = None, message_type: str = "customer", proof_url: str = "") -> SupportTicketMessage:
    msg = SupportTicketMessage(ticket_id=ticket.id, user_id=user_id, admin_id=admin_id, message_type=message_type, body=(body or "").strip()[:5000], proof_url=proof_url or "")
    db.add(msg)
    ticket.updated_at = datetime.utcnow()
    if message_type == "customer":
        ticket.last_customer_message_at = datetime.utcnow()
        if ticket.status in {"resolved", "closed", "awaiting_customer"}:
            ticket.status = "open"
            ticket.closed_at = None
    elif message_type == "admin":
        ticket.last_admin_response_at = datetime.utcnow()
    return msg


def support_category_priority(category: str) -> str:
    category = (category or "duvida_geral").strip()
    if category in {"pagamento_creditos", "creditos_descontados", "produto_defeito", "antifraude_bloqueio", "lance_leilao", "arremate_pagamento", "compra_assistida"}:
        return "alta"
    if category in {"indicacao_bonus", "documentos_conta", "duvida_geral"}:
        return "baixa"
    return "media"


def support_recent_activity_for_user(db: Session, user: User) -> dict:
    user_id = int(user.id)
    return {
        "transactions": db.query(WalletTransaction).filter(WalletTransaction.user_id == user_id).order_by(desc(WalletTransaction.created_at)).limit(30).all(),
        "payments": db.query(MercadoPagoPayment).filter(MercadoPagoPayment.user_id == user_id).order_by(desc(MercadoPagoPayment.created_at)).limit(30).all(),
        "orders": db.query(WinnerOrder).options(selectinload(WinnerOrder.auction)).filter(WinnerOrder.user_id == user_id).order_by(desc(WinnerOrder.created_at)).limit(20).all(),
        "bids": db.query(Bid).options(selectinload(Bid.auction)).filter(Bid.user_id == user_id).order_by(desc(Bid.created_at)).limit(30).all(),
        "referrals_as_referrer": db.query(ReferralReward).options(selectinload(ReferralReward.referred)).filter(ReferralReward.referrer_user_id == user_id).order_by(desc(ReferralReward.created_at)).limit(20).all(),
        "referrals_as_referred": db.query(ReferralReward).options(selectinload(ReferralReward.referrer)).filter(ReferralReward.referred_user_id == user_id).order_by(desc(ReferralReward.created_at)).limit(5).all(),
        "tickets": db.query(SupportTicket).filter(SupportTicket.user_id == user_id).order_by(desc(SupportTicket.created_at)).limit(12).all(),
        "audits": db.query(AuditLog).filter(AuditLog.user_id == user_id).order_by(desc(AuditLog.created_at)).limit(30).all(),
    }


def support_user_search(db: Session, search: str, limit: int = 10) -> list[User]:
    search = (search or "").strip()
    if not search:
        return []
    like = f"%{search}%"
    digits = only_digits(search)
    return db.query(User).filter(or_(
        User.full_name.ilike(like), User.public_name.ilike(like), User.nickname.ilike(like), User.email.ilike(like),
        User.cpf.ilike(f"%{digits}%") if digits else User.cpf.ilike(like),
        User.phone.ilike(f"%{digits}%") if digits else User.phone.ilike(like),
        User.referral_code.ilike(like),
    )).order_by(desc(User.created_at)).limit(limit).all()


def support_context_for_user(db: Session, user: User, ticket_id: Optional[int] = None, error: str = "", success: str = "") -> dict:
    tickets = db.query(SupportTicket).filter(SupportTicket.user_id == user.id).order_by(desc(SupportTicket.created_at)).limit(30).all()
    orders = db.query(WinnerOrder).options(selectinload(WinnerOrder.auction)).filter(WinnerOrder.user_id == user.id).order_by(desc(WinnerOrder.created_at)).limit(20).all()
    active_ticket = None
    messages: list[SupportTicketMessage] = []
    if ticket_id:
        active_ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id, SupportTicket.user_id == user.id).first()
    if not active_ticket and tickets:
        active_ticket = tickets[0]
    if active_ticket:
        active_ticket.customer_last_seen_at = datetime.utcnow()
        messages = db.query(SupportTicketMessage).filter(SupportTicketMessage.ticket_id == active_ticket.id).order_by(SupportTicketMessage.created_at.asc()).limit(200).all()
    return {"section": "support", "support_tickets": tickets, "support_orders": orders, "support_ticket": active_ticket, "support_messages": messages, "support_categories": SUPPORT_CATEGORIES, "support_priorities": SUPPORT_PRIORITIES, "support_statuses": SUPPORT_STATUSES, "support_error": error, "support_success": success}


def admin_support_context(db: Session, request: Request, search: str, is_super_admin: bool) -> dict:
    status_filter = (request.query_params.get("status") or "open").strip()
    category_filter = (request.query_params.get("category") or "").strip()
    ticket_id_raw = (request.query_params.get("ticket") or "").strip()
    ticket_id = int(ticket_id_raw) if ticket_id_raw.isdigit() else None
    query = db.query(SupportTicket).options(selectinload(SupportTicket.user), selectinload(SupportTicket.order))
    if status_filter and status_filter != "todos":
        if status_filter == "ativos":
            query = query.filter(SupportTicket.status.in_(["open", "in_review", "awaiting_customer", "dispute"]))
        else:
            query = query.filter(SupportTicket.status == status_filter)
    if category_filter:
        query = query.filter(SupportTicket.category == category_filter)
    if search:
        like = f"%{search}%"
        query = query.outerjoin(User, User.id == SupportTicket.user_id).filter(or_(SupportTicket.subject.ilike(like), SupportTicket.message.ilike(like), SupportTicket.admin_note.ilike(like), User.full_name.ilike(like), User.public_name.ilike(like), User.email.ilike(like), User.cpf.ilike(like), User.phone.ilike(like)))
    tickets = query.order_by(desc(SupportTicket.updated_at), desc(SupportTicket.created_at)).limit(80).all()
    detail = db.query(SupportTicket).options(selectinload(SupportTicket.user), selectinload(SupportTicket.order)).filter(SupportTicket.id == ticket_id).first() if ticket_id else None
    if not detail and tickets:
        detail = tickets[0]
    messages: list[SupportTicketMessage] = []
    dossier = {}
    if detail:
        messages = db.query(SupportTicketMessage).options(selectinload(SupportTicketMessage.user), selectinload(SupportTicketMessage.admin)).filter(SupportTicketMessage.ticket_id == detail.id).order_by(SupportTicketMessage.created_at.asc()).limit(250).all()
        if detail.user:
            dossier = support_recent_activity_for_user(db, detail.user)
    counters_row = db.execute(text("""
        SELECT
          (SELECT COUNT(*) FROM support_tickets WHERE status IN ('open','in_review','awaiting_customer','dispute')) AS active_count,
          (SELECT COUNT(*) FROM support_tickets WHERE status = 'open') AS open_count,
          (SELECT COUNT(*) FROM support_tickets WHERE status = 'awaiting_customer') AS awaiting_count,
          (SELECT COUNT(*) FROM support_tickets WHERE status = 'dispute') AS dispute_count,
          (SELECT COUNT(*) FROM support_tickets WHERE sla_due_at IS NOT NULL AND sla_due_at < CURRENT_TIMESTAMP AND status IN ('open','in_review','awaiting_customer','dispute')) AS late_count
    """)).mappings().first()
    return {"support_tickets": tickets, "support_ticket_detail": detail, "support_messages": messages, "support_dossier": dossier, "support_categories": SUPPORT_CATEGORIES, "support_priorities": SUPPORT_PRIORITIES, "support_statuses": SUPPORT_STATUSES, "support_status_filter": status_filter, "support_category_filter": category_filter, "support_user_results": support_user_search(db, search, limit=8), "support_counters": dict(counters_row or {}), "support_admin_adjust_limit": SUPPORT_ADMIN_ADJUST_LIMIT}


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
        "support_ticket_detail": None,
        "support_messages": [],
        "support_dossier": {},
        "support_categories": SUPPORT_CATEGORIES,
        "support_priorities": SUPPORT_PRIORITIES,
        "support_statuses": SUPPORT_STATUSES,
        "support_status_filter": "open",
        "support_category_filter": "",
        "support_user_results": [],
        "support_counters": {},
        "support_admin_adjust_limit": SUPPORT_ADMIN_ADJUST_LIMIT,
        "user_audit": {},
        "audit_logs": [],
        "audit_folders": [],
        "audit_folder": "geral",
        "audit_search": "",
        "audit_date_from": "",
        "audit_date_to": "",
        "audit_total_loaded": 0,
        "finished_auctions": [],
        "returned_items": [],
        "finance": {},
        "cashflow_movements": [],
        "auction_results": [],
        "recent_chat_messages": [],
        "suggestion_vote_stats": [],
    }


def admin_light_stats(db: Session, is_super_admin: bool, returned_count: int = 0) -> dict:
    """Estatísticas mínimas do Admin em uma única ida ao banco.

    Nos logs do Railway, cada consulta simples remota custava perto de 200ms.
    A versão anterior fazia várias contagens separadas. Aqui usamos subconsultas
    escalares no mesmo SELECT para reduzir round-trips sem mudar os números.
    """
    row = db.execute(text("""
        SELECT
          (SELECT COUNT(*) FROM auction_items WHERE status = 'live') AS live,
          (SELECT COUNT(*) FROM auction_items WHERE status IN ('scheduled', 'relisted')) AS scheduled,
          (SELECT COUNT(*) FROM auction_items WHERE status = 'ended') AS completed,
          (SELECT COUNT(*) FROM winner_orders WHERE status IN ('pending_payment','pending_gateway')) AS pending_payment,
          (SELECT COUNT(*) FROM winner_orders WHERE status IN ('paid','aguardando_escolha','aguardando_link','link_recebido','link_rejeitado','aguardando_aprovacao','aprovado_para_pagamento','pagamento_pedido_realizado','processing','purchased','sent','delivered','dispute','resolved')) AS pending_shipping,
          (SELECT COUNT(*) FROM support_tickets WHERE status IN ('open', 'in_review', 'dispute')) AS open_tickets,
          (SELECT COUNT(*) FROM users) AS users,
          (SELECT COUNT(*) FROM users WHERE (is_banned IS NULL OR is_banned = false)) AS active_users,
          (SELECT COUNT(*) FROM users WHERE identity_status = 'pending') AS identity_pending,
          (SELECT COUNT(*) FROM withdrawal_requests WHERE status = 'pending') AS pending_withdrawals
    """)).mappings().first()

    stats = {
        "users": int((row["users"] if row else 0) or 0) if is_super_admin else 0,
        "active_users": int((row["active_users"] if row else 0) or 0) if is_super_admin else 0,
        "live": int((row["live"] if row else 0) or 0),
        "scheduled": int((row["scheduled"] if row else 0) or 0),
        "pending_payment": int((row["pending_payment"] if row else 0) or 0),
        "completed": int((row["completed"] if row else 0) or 0),
        "pending_shipping": int((row["pending_shipping"] if row else 0) or 0),
        "identity_pending": int((row["identity_pending"] if row else 0) or 0) if is_super_admin else 0,
        "pending_withdrawals": int((row["pending_withdrawals"] if row else 0) or 0) if is_super_admin else 0,
        "open_tickets": int((row["open_tickets"] if row else 0) or 0),
        "returned_products": returned_count,
    }
    return stats

def cached_admin_light_stats(db: Session, is_super_admin: bool, returned_count: int = 0, ttl_seconds: int = 120) -> dict:
    key = f"admin:light-stats:{int(is_super_admin)}:{int(returned_count)}"
    cached = nav_cache_get(key)
    if cached is not None:
        return cached
    return nav_cache_set(key, admin_light_stats(db, is_super_admin, returned_count), ttl_seconds)


def cached_admin_finance_summary(db: Session, ttl_seconds: int = 180) -> dict:
    cached = nav_cache_get("admin:finance-summary:v3")
    if cached is not None:
        return cached
    return nav_cache_set("admin:finance-summary:v3", build_finance_dashboard(db), ttl_seconds)



def build_admin_dashboard_context_snapshot(db: Session, is_super_admin: bool) -> dict:
    """Snapshot do Resumo Geral em uma única ida ao banco.

    Os logs mostraram que o dashboard continuava lento mesmo com cache porque,
    no primeiro acesso, ele ainda fazia estatísticas + financeiro leve em
    consultas separadas. Esta função consolida tudo em um SELECT só, reduzindo
    round-trips no banco remoto sem mudar o template.
    """
    row = db.execute(text("""
        SELECT
          (SELECT COUNT(*) FROM auction_items WHERE status = 'live') AS live,
          (SELECT COUNT(*) FROM auction_items WHERE status IN ('scheduled', 'relisted')) AS scheduled,
          (SELECT COUNT(*) FROM auction_items WHERE status = 'ended') AS completed,
          (SELECT COUNT(*) FROM winner_orders WHERE status IN ('pending_payment','pending_gateway')) AS pending_payment,
          (SELECT COUNT(*) FROM winner_orders WHERE status IN ('paid','aguardando_escolha','aguardando_link','link_recebido','link_rejeitado','aguardando_aprovacao','aprovado_para_pagamento','pagamento_pedido_realizado','processing','purchased','sent','delivered','dispute','resolved')) AS pending_shipping,
          (SELECT COUNT(*) FROM support_tickets WHERE status IN ('open', 'in_review', 'dispute')) AS open_tickets,
          (SELECT COUNT(*) FROM users) AS users,
          (SELECT COUNT(*) FROM users WHERE (is_banned IS NULL OR is_banned = false)) AS active_users,
          (SELECT COUNT(*) FROM users WHERE identity_status = 'pending') AS identity_pending,
          (SELECT COUNT(*) FROM withdrawal_requests WHERE status = 'pending') AS pending_withdrawals,
          (SELECT COALESCE(SUM(total_bid_spent), 0) FROM auction_items) AS total_bid_spent,
          ((SELECT COALESCE(SUM(total_bid_fees), 0) FROM auction_items) +
           (SELECT COALESCE(SUM(fee_amount), 0) FROM withdrawal_requests WHERE status IN ('pending','approved','paid'))) AS total_fees,
          (SELECT COALESCE(SUM(total_bid_fees), 0) FROM auction_items) AS bid_fee_total,
          (SELECT COALESCE(SUM(fee_amount), 0) FROM withdrawal_requests WHERE status IN ('pending','approved','paid')) AS withdrawal_fee_total,
          (SELECT COALESCE(SUM(current_price), 0) FROM auction_items) AS bid_product_cash,
          (SELECT COALESCE(SUM(final_price), 0)
             FROM winner_orders
            WHERE status IN ('paid','processing','purchased','sent','delivered')) AS total_payments,
          (SELECT COALESCE(SUM(CASE
                    WHEN (a.source_price - (COALESCE(a.current_price,0) + COALESCE(o.final_price,0))) > 0
                    THEN (a.source_price - (COALESCE(a.current_price,0) + COALESCE(o.final_price,0)))
                    ELSE 0 END), 0)
             FROM winner_orders o JOIN auction_items a ON a.id = o.auction_id
            WHERE o.status IN ('paid','processing','purchased','sent')) AS expected_products,
          (SELECT COALESCE(SUM(COALESCE(NULLIF(net_amount,0), amount)), 0) FROM withdrawal_requests WHERE status IN ('pending','approved')) AS pending_withdrawals_value,
          (SELECT COALESCE(SUM(wallet_balance), 0) FROM users) AS user_wallet_total,
          (SELECT COALESCE(SUM(ABS(amount)), 0) FROM wallet_transactions WHERE kind = 'product_outgoing') AS product_outgoing,
          (SELECT COALESCE(SUM(ABS(amount)), 0) FROM wallet_transactions WHERE kind = 'refund') AS refunds,
          (SELECT COALESCE(SUM(COALESCE(NULLIF(net_amount,0), amount)), 0) FROM withdrawal_requests WHERE status = 'paid') AS paid_withdrawals
    """)).mappings().first()

    def iv(name: str) -> int:
        return int((row[name] if row and row[name] is not None else 0) or 0)

    def fv(name: str) -> float:
        return BR((row[name] if row and row[name] is not None else 0.0) or 0.0)

    stats = {
        "users": iv("users") if is_super_admin else 0,
        "active_users": iv("active_users") if is_super_admin else 0,
        "live": iv("live"),
        "scheduled": iv("scheduled"),
        "pending_payment": iv("pending_payment"),
        "completed": iv("completed"),
        "pending_shipping": iv("pending_shipping"),
        "identity_pending": iv("identity_pending") if is_super_admin else 0,
        "pending_withdrawals": iv("pending_withdrawals") if is_super_admin else 0,
        "open_tickets": iv("open_tickets"),
        "returned_products": 0,
    }

    total_bid_spent = fv("total_bid_spent")
    total_fees = fv("total_fees")
    bid_fee_total = fv("bid_fee_total")
    withdrawal_fee_total = fv("withdrawal_fee_total")
    bid_product_cash = fv("bid_product_cash")
    total_payments = fv("total_payments")
    expected_products = fv("expected_products")
    pending_withdrawals_value = fv("pending_withdrawals_value")
    user_wallet_total = fv("user_wallet_total")
    product_outgoing = fv("product_outgoing")
    refunds = fv("refunds")
    paid_withdrawals = fv("paid_withdrawals")
    total_income = BR(total_bid_spent + total_payments)
    total_outgoing = BR(product_outgoing + refunds + paid_withdrawals)
    expected_outgoing = BR(expected_products + pending_withdrawals_value)
    available_cash = BR(total_income - total_outgoing - pending_withdrawals_value)
    real_cash = BR(total_income - total_outgoing - expected_outgoing)
    coverage_percent = BR((available_cash / expected_outgoing * 100.0) if expected_outgoing > 0 else 100.0)
    estimated_profit = BR(total_fees + bid_product_cash + total_payments - product_outgoing - refunds - pending_withdrawals_value)
    period_finance = build_finance_periods(db)
    finance = {
        **period_finance,
        "total_fees": total_fees,
        "bid_fee_total": bid_fee_total,
        "withdrawal_fee_total": withdrawal_fee_total,
        "bid_profit_total": bid_product_cash,
        "total_bid_spent": total_bid_spent,
        "bid_product_cash": bid_product_cash,
        "total_payments": total_payments,
        "user_wallet_total": user_wallet_total,
        "expected_outgoing": expected_outgoing,
        "total_income": total_income,
        "total_outgoing": total_outgoing,
        "net_result": BR(total_income - total_outgoing),
        "estimated_profit": estimated_profit,
        "available_cash": available_cash,
        "real_cash": real_cash,
        "coverage_percent": coverage_percent,
        "accumulated_loss": BR(abs(estimated_profit) if estimated_profit < 0 else 0.0),
        "pending_withdrawals": pending_withdrawals_value,
        "product_outgoing": product_outgoing,
        "expected_products": expected_products,
        "paid_withdrawals": paid_withdrawals,
        "refunds": refunds,
    }
    return {"stats": stats, "finance": finance}


def cached_admin_dashboard_context(db: Session, is_super_admin: bool, ttl_seconds: int = 300) -> dict:
    key = f"admin:dashboard-context:v4:{int(is_super_admin)}"
    cached = nav_cache_get(key)
    if cached is not None:
        return cached
    return nav_cache_set(key, build_admin_dashboard_context_snapshot(db, is_super_admin), ttl_seconds)

def cached_admin_cashflow_context(db: Session, ttl_seconds: int = 300) -> dict:
    """Blocos financeiros completos com cache curto.

    O resumo financeiro usa totais cacheados dos leilões. As tabelas detalhadas
    continuam disponíveis, mas com limites menores para o Admin não travar ao
    trocar de aba.
    """
    cached = nav_cache_get("admin:cashflow-context")
    if cached is not None:
        return cached
    value = {
        "finance": cached_admin_finance_summary(db, ttl_seconds),
        "cashflow_movements": build_cashflow_movements(db),
        "auction_results": build_auction_results(db),
    }
    return nav_cache_set("admin:cashflow-context", value, ttl_seconds)


@app.get("/admin/audit/export")
def admin_audit_export(request: Request):
    db = SessionLocal()
    try:
        require_superadmin(request, db)
        search = (request.query_params.get("q") or "").strip()
        folder = (request.query_params.get("folder") or "geral").strip()
        date_from = (request.query_params.get("from") or "").strip()
        date_to = (request.query_params.get("to") or "").strip()
        data = build_audit_center(db, search=search, folder=folder, date_from=date_from, date_to=date_to, limit=1000)
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["data", "pasta", "usuario", "acao", "entidade", "id_entidade", "ip", "detalhes", "arquivo_morto"])
        for row in data["audit_logs"]:
            writer.writerow([
                row.created_at.strftime("%d/%m/%Y %H:%M:%S") if row.created_at else "",
                row.folder_label,
                row.user_label,
                row.action,
                row.entity_type,
                row.entity_id,
                row.ip_address,
                row.details,
                "sim" if row.archived else "não",
            ])
        filename = f"auditoria_lanceiocerto_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    finally:
        db.close()


def cached_finished_auctions(db: Session, ttl_seconds: int = 180) -> list[dict]:
    cached = nav_cache_get("admin:finished-auctions")
    if cached is not None:
        return cached
    return nav_cache_set("admin:finished-auctions", build_finished_auctions(db), ttl_seconds)



def _perf_mark(timings: list[tuple[str, float]], label: str, last: float) -> float:
    import time
    now = time.perf_counter()
    timings.append((label, (now - last) * 1000))
    return now


def admin_html_response(ctx: dict, timings: list[tuple[str, float]] | None = None) -> HTMLResponse:
    """Renderiza o Admin de forma mensurável.

    O middleware mede o tempo total da navegação, mas o TemplateResponse padrão
    renderiza o corpo depois do retorno da rota. Para investigar e reduzir o
    atraso fixo do /admin, renderizamos aqui e registramos quanto tempo foi
    gasto em autenticação, dados da aba ativa e template.
    """
    import time
    timings = timings or []
    render_started = time.perf_counter()
    html = templates.env.get_template("admin.html").render(ctx)
    render_ms = (time.perf_counter() - render_started) * 1000
    timings.append(("render", render_ms))

    total_ms = sum(ms for _, ms in timings)
    tab = ctx.get("admin_active_panel", "-")
    if total_ms >= ADMIN_PERF_LOG_THRESHOLD_MS:
        parts = " ".join(f"{name}={ms:.1f}ms" for name, ms in timings)
        print(f"[ADMIN-PERF] tab={tab} total={total_ms:.1f}ms {parts}")

    response = HTMLResponse(html)
    response.headers["X-Admin-Panel"] = str(tab)
    response.headers["X-Admin-Render-Ms"] = f"{render_ms:.1f}"
    response.headers["X-Admin-Perf-Ms"] = f"{total_ms:.1f}"
    return response




@app.post("/admin/sistema/reconciliar-pagamentos")
def admin_reconcile_payments(request: Request, minutes: int = Form(60), limit: int = Form(20)):
    db = SessionLocal()
    try:
        admin_user = require_admin(request, db)
        if not getattr(admin_user, "is_superadmin", False):
            raise HTTPException(status_code=403, detail="Apenas admin master pode executar reconciliação manual de pagamentos.")
        result = reconcile_recent_pending_payments(request, minutes=minutes, limit=limit)
        audit_event(db, request, "system.payments_reconciled", admin_user, "system", "mercadopago", json.dumps(result, ensure_ascii=False))
        db.commit()
        return RedirectResponse(f"/admin?tab=support&success=Reconciliação concluída: {result['checked']} verificados, {result['approved']} aprovados", status_code=303)
    finally:
        db.close()


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    import time
    started = time.perf_counter()
    last = started
    timings: list[tuple[str, float]] = []
    db = SessionLocal()
    try:
        admin = admin_current_user_fast(request, db)
        last = _perf_mark(timings, "auth", last)

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
        audit_folder = (request.query_params.get("folder") or "geral").strip()
        audit_date_from = (request.query_params.get("from") or "").strip()
        audit_date_to = (request.query_params.get("to") or "").strip()

        ctx = blank_admin_context()
        ctx.update({
            "request": request,
            "user": admin,
            "search": search,
            "payment_deadline_minutes": PAYMENT_DEADLINE_MINUTES,
            "is_super_admin": is_super_admin,
            "admin_active_panel": active_panel,
        })
        last = _perf_mark(timings, "base", last)

        # Produto é a aba mais leve e padrão do admin operacional. Não precisa
        # carregar financeiro, auditoria, usuários nem pedidos.
        if active_panel == "admin-dashboard":
            ctx.update(cached_admin_dashboard_context(db, is_super_admin))
        elif active_panel == "admin-cashflow" and is_super_admin:
            cashflow_ctx = cached_admin_cashflow_context(db)
            ctx.update(cashflow_ctx)
            ctx["stats"] = cached_admin_light_stats(db, is_super_admin)
        elif active_panel == "admin-returned":
            returned_items = cached_returned_items(db)
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
            orders_query = db.query(WinnerOrder).options(selectinload(WinnerOrder.auction), selectinload(WinnerOrder.user))
            if active_panel == "admin-pending-payments":
                orders_query = orders_query.filter(WinnerOrder.status.in_(["pending_payment", "pending_gateway"]))
            elif active_panel == "admin-shipping":
                orders_query = orders_query.filter(WinnerOrder.status.in_(["paid", "aguardando_escolha", "aguardando_link", "link_recebido", "link_rejeitado", "aguardando_aprovacao", "aprovado_para_pagamento", "pagamento_pedido_realizado", "processing", "purchased", "sent", "delivered", "dispute", "resolved"]))
            elif active_panel == "admin-search-orders":
                if search:
                    like = f"%{search}%"
                    orders_query = orders_query.join(AuctionItem, AuctionItem.id == WinnerOrder.auction_id).join(User, User.id == WinnerOrder.user_id).filter(
                        or_(
                            AuctionItem.title.ilike(like),
                            User.full_name.ilike(like),
                            User.public_name.ilike(like),
                            User.email.ilike(like),
                            User.cpf.ilike(like),
                            WinnerOrder.tracking_code.ilike(like),
                        )
                    )
                else:
                    orders_query = orders_query.filter(WinnerOrder.status.in_(["sent", "delivered", "dispute", "resolved", "closed"]))
            orders_limit = 100 if active_panel in {"admin-shipping", "admin-pending-payments"} else 20
            orders = orders_query.order_by(desc(WinnerOrder.created_at)).limit(orders_limit).all()
            ctx["orders"] = orders
            ctx["admin_order_cards"] = build_admin_order_cards(db, orders)
            ctx["pending_payment_orders"] = orders if active_panel == "admin-pending-payments" else []
            ctx["shipping_orders"] = orders if active_panel == "admin-shipping" else []
            ctx["consultation_orders"] = orders if active_panel == "admin-search-orders" else []
        elif active_panel == "admin-finished":
            ctx["finished_auctions"] = cached_finished_auctions(db)
        elif active_panel == "admin-users" and is_super_admin:
            users_query = db.query(
                User.id, User.full_name, User.public_name, User.nickname, User.email, User.cpf, User.phone,
                User.wallet_balance, User.identity_status, User.is_banned, User.chat_muted, User.is_admin,
                User.is_superadmin, User.ban_count, User.banned_until, User.ban_reason, User.street, User.number, User.city, User.state, User.document_type,
                User.document_number, User.identity_note, User.document_file_url, User.document_back_file_url,
                User.selfie_file_url, User.created_at,
            )
            if search:
                like = f"%{search}%"
                users_query = users_query.filter((User.full_name.ilike(like)) | (User.public_name.ilike(like)) | (User.email.ilike(like)) | (User.cpf.ilike(like)) | (User.phone.ilike(like)))
            user_rows = users_query.order_by(desc(User.wallet_balance), desc(User.created_at)).limit(50).all()
            users = [
                SimpleNamespace(
                    id=r.id, full_name=r.full_name or "", public_name=r.public_name or "", nickname=r.nickname or "",
                    email=r.email or "", cpf=r.cpf or "", phone=r.phone or "", wallet_balance=float(r.wallet_balance or 0.0),
                    identity_status=r.identity_status or "pending", is_banned=bool(r.is_banned), chat_muted=bool(r.chat_muted),
                    is_admin=bool(r.is_admin), is_superadmin=bool(r.is_superadmin), ban_count=int(r.ban_count or 0), banned_until=r.banned_until, ban_reason=r.ban_reason or "", street=r.street or "", number=r.number or "",
                    city=r.city or "", state=r.state or "", document_type=r.document_type or "CPF", document_number=r.document_number or "",
                    identity_note=r.identity_note or "", document_file_url=safe_image_url(r.document_file_url or ""),
                    document_back_file_url=safe_image_url(r.document_back_file_url or ""), selfie_file_url=safe_image_url(r.selfie_file_url or ""),
                    created_at=r.created_at,
                )
                for r in user_rows
            ]
            ctx["users"] = users
            ctx["user_audit"] = user_audit_map(db, users)
        elif active_panel == "admin-identity-pending" and is_super_admin:
            ctx["identity_pending_users"] = db.query(User).filter(User.identity_status == "pending").order_by(desc(User.created_at)).limit(30).all()
        elif active_panel == "admin-withdrawals" and is_super_admin:
            ctx["withdrawal_requests"] = db.query(WithdrawalRequest).options(selectinload(WithdrawalRequest.user)).order_by(desc(WithdrawalRequest.created_at)).limit(25).all()
        elif active_panel == "admin-tickets":
            ctx.update(admin_support_context(db, request, search=search, is_super_admin=is_super_admin))
        elif active_panel == "admin-suggestions":
            ctx["suggestion_vote_stats"] = cached_suggestion_vote_stats(db)
        elif active_panel == "admin-audit" and is_super_admin:
            ctx.update(build_audit_center(db, search=search, folder=audit_folder, date_from=audit_date_from, date_to=audit_date_to, limit=120))
            ctx["recent_chat_messages"] = []
        elif active_panel == "admin-moderation":
            ctx["moderation_users"] = db.query(User).order_by(desc(User.is_banned), desc(User.chat_muted), desc(User.created_at)).limit(100).all()

        last = _perf_mark(timings, f"data:{active_panel}", last)
        return admin_html_response(ctx, timings)
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
    cashback_enabled: int = Form(0),
):
    db = SessionLocal()
    try:
        require_admin(request, db)
        final_image = await asyncio.to_thread(save_product_image_data_url, image_file) or STATIC_FALLBACK_IMAGE
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
            cashback_enabled=False,
        )
        db.add(item)
        db.commit()
        nav_cache_clear()
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

        final_image = await asyncio.to_thread(save_product_image_data_url, image_file) or item.image_url

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
        item.cashback_enabled = False

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
):
    db = SessionLocal()
    try:
        admin_user = require_admin(request, db)
        order = db.query(WinnerOrder).options(selectinload(WinnerOrder.auction)).filter(WinnerOrder.id == order_id).first()
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")

        order.purchase_link = purchase_link.strip()
        order.tracking_code = tracking_code.strip()
        order.admin_note = admin_note.strip()
        now = datetime.utcnow()

        requested_status = (purchase_status or "").strip()
        legacy_status_map = {
            "pedido_pago": "pagamento_pedido_realizado",
            "compra_realizada": "purchased",
            "produto_enviado": "sent",
            "produto_entregue": "delivered",
        }
        requested_status = legacy_status_map.get(requested_status, requested_status)

        allowed_statuses = {
            "aguardando_escolha",
            "aguardando_link",
            "link_recebido",
            "link_rejeitado",
            "aguardando_aprovacao",
            "aprovado_para_pagamento",
            "pagamento_pedido_realizado",
            "purchased",
            "sent",
            "delivered",
            "finalized",
        }
        if requested_status and requested_status not in allowed_statuses:
            raise HTTPException(status_code=400, detail="Status do pedido inválido.")

        previous_status = order.status

        if requested_status:
            if requested_status == "aprovado_para_pagamento":
                if order.fulfillment_mode == "customer_purchase" or order.submitted_purchase_link:
                    if not getattr(order, "submitted_link_valid", False):
                        raise HTTPException(status_code=400, detail="O link do cliente ainda não passou na validação automática.")
                    order.approved_by_admin = admin_user.id
                    order.approved_at = order.approved_at or now
                order.status = "aprovado_para_pagamento"
                audit_event(db, request, "order.customer_link_approved", admin_user, "order", order.id, f"Status alterado para aprovado para pagamento. Link: {order.submitted_purchase_link or 'sem link'}")

            elif requested_status == "link_rejeitado":
                order.status = "link_rejeitado"
                order.submitted_link_valid = False
                order.submitted_link_validation_note = (order.submitted_link_validation_note or "Link recusado pelo administrador após conferência.").strip()
                audit_event(db, request, "order.customer_link_admin_rejected", admin_user, "order", order.id, f"Link recusado pelo admin: {order.submitted_purchase_link or 'sem link'}")

            elif requested_status == "pagamento_pedido_realizado":
                if order.fulfillment_mode == "customer_purchase" and order.status not in {"aprovado_para_pagamento", "pagamento_pedido_realizado"}:
                    raise HTTPException(status_code=400, detail="Aprove o link antes de marcar o pagamento do pedido.")
                order.status = "pagamento_pedido_realizado"
                order.purchased_at = order.purchased_at or now
                register_product_outgoing_if_needed(db, order, now)
                audit_event(db, request, "order.customer_purchase_paid", admin_user, "order", order.id, f"Pagamento do pedido registrado pelo status. Link: {order.submitted_purchase_link or order.purchase_link or 'sem link'}")

            elif requested_status == "purchased":
                order.status = "purchased"
                order.purchased_at = order.purchased_at or now
                register_product_outgoing_if_needed(db, order, now)
                audit_event(db, request, "order.purchased", admin_user, "order", order.id, f"Compra realizada. Pedido #{order.id}")

            elif requested_status == "sent":
                order.status = "sent"
                order.sent_at = order.sent_at or now
                register_product_outgoing_if_needed(db, order, now)
                audit_event(db, request, "order.sent", admin_user, "order", order.id, f"Produto enviado. Rastreio: {order.tracking_code or 'sem código'}")

            elif requested_status == "delivered":
                order.status = "delivered"
                order.delivered_at = order.delivered_at or now
                register_product_outgoing_if_needed(db, order, now)
                audit_event(db, request, "order.delivered", admin_user, "order", order.id, f"Produto entregue. Pedido #{order.id}")

            elif requested_status == "finalized":
                if order.status not in {"delivered", "resolved", "pagamento_pedido_realizado", "finalized"}:
                    raise HTTPException(status_code=400, detail="A operação só pode ser finalizada depois da entrega, resolução da disputa ou pagamento do pedido do cliente.")
                order.status = "finalized"
                order.admin_note = ((order.admin_note or "") + "\nOperação finalizada administrativamente.").strip()
                item = db.get(AuctionItem, order.auction_id)
                if item:
                    item.status = "ended"
                audit_event(db, request, "order.finalized", admin_user, "order", order.id, f"Operação finalizada. Pedido #{order.id}")

            else:
                order.status = requested_status
                audit_event(db, request, "order.status_updated", admin_user, "order", order.id, f"Status alterado de {previous_status or '—'} para {requested_status}.")

            order.purchase_status = requested_status

        nav_cache_clear()
        db.commit()
        return RedirectResponse("/admin?tab=admin-shipping", status_code=303)
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
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Crédito manual deve ser maior que zero. Use uma rota de estorno/ajuste específica para débito.")
        previous_balance = BR(user.wallet_balance or 0.0)
        user.wallet_balance = BR(previous_balance + amount)
        db.add(WalletTransaction(user_id=user.id, amount=amount, kind="manual_adjustment", note="Crédito admin"))
        admin_user = require_superadmin(request, db)
        audit_event(db, request, "wallet.manual_credit", admin_user, "user", user.id, f"Usuário: {user.full_name} | saldo antes R$ {fmt_money(previous_balance)} | crédito R$ {fmt_money(amount)} | saldo depois R$ {fmt_money(user.wallet_balance)}")
        nav_cache_clear()
        db.commit()
        return RedirectResponse("/admin?tab=admin-users", status_code=303)
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
        user.banned_until = None if user.is_banned else None
        user.ban_reason = "Bloqueio permanente pelo super admin." if user.is_banned else "Liberado pelo super admin."
        ADMIN_USER_NAV_CACHE.clear()
        db.commit()
        return RedirectResponse("/admin?tab=admin-moderation", status_code=303)
    finally:
        db.close()


@app.post("/admin/user/{user_id}/ban-1-day")
def admin_temp_ban_user(request: Request, user_id: int, reason: str = Form("")):
    db = SessionLocal()
    try:
        admin = require_admin(request, db)
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        if user.id == admin.id or user.is_superadmin:
            raise HTTPException(status_code=400, detail="Você não pode aplicar essa ação nesse usuário.")
        user.ban_count = int(getattr(user, "ban_count", 0) or 0) + 1
        user.ban_reason = (reason or "Banimento temporário aplicado pela moderação.").strip()
        user.is_banned = True
        if user.ban_count >= 3:
            if not admin.is_superadmin:
                user.banned_until = datetime.utcnow() + timedelta(days=1)
                user.ban_reason += " Conta com 3 ocorrências; revisão do super admin necessária."
            else:
                user.banned_until = None
        else:
            user.banned_until = datetime.utcnow() + timedelta(days=1)
        audit_event(db, request, "moderation.ban_1_day", admin, "user", user.id, f"{user.full_name} | ocorrências: {user.ban_count} | {user.ban_reason}")
        ADMIN_USER_NAV_CACHE.clear()
        db.commit()
        return RedirectResponse("/admin?tab=admin-moderation", status_code=303)
    finally:
        db.close()

@app.post("/admin/user/{user_id}/unban")
def admin_unban_user(request: Request, user_id: int):
    db = SessionLocal()
    try:
        admin = require_superadmin(request, db)
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        user.is_banned = False
        user.banned_until = None
        user.ban_reason = "Liberado pelo super admin."
        audit_event(db, request, "moderation.unban", admin, "user", user.id, user.full_name)
        ADMIN_USER_NAV_CACHE.clear()
        db.commit()
        return RedirectResponse("/admin?tab=admin-moderation", status_code=303)
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
        return RedirectResponse("/admin?tab=admin-moderation", status_code=303)
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
        admin_user = require_admin(request, db)
        if tracking_code.strip():
            order.tracking_code = tracking_code.strip()
            if order.status in {"paid", "processing", "purchased"}:
                order.status = "sent"
                order.sent_at = datetime.utcnow()
                register_product_outgoing_if_needed(db, order, order.sent_at)
            audit_event(db, request, "order.tracking_updated", admin_user, "order", order.id, f"Rastreio atualizado: {order.tracking_code}")
        if admin_note.strip():
            order.admin_note = admin_note.strip()
        nav_cache_clear()
        db.commit()
        return RedirectResponse("/admin?tab=admin-shipping", status_code=303)
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
        allowed = {"pending_payment", "pending_gateway", "paid", "processing", "purchased", "sent", "delivered", "dispute", "resolved", "finalized", "expired"}
        if status not in allowed:
            raise HTTPException(status_code=400, detail="Status inválido.")
        if not admin_user.is_superadmin and status in {"pending_payment", "pending_gateway", "paid", "expired"}:
            raise HTTPException(status_code=403, detail="Apenas o super admin pode alterar status financeiro do pedido.")
        previous_status = order.status
        order.status = status
        now = datetime.utcnow()
        if status == "paid" and previous_status != "paid":
            order.paid_at = order.paid_at or now
            db.add(WalletTransaction(user_id=order.user_id, amount=BR(order.final_price or 0.0), kind="payment_confirmed_external", note=f"Pagamento externo confirmado manualmente pelo admin no pedido #{order.id}"))
        if status == "purchased" and previous_status != "purchased":
            order.purchased_at = order.purchased_at or now
            register_product_outgoing_if_needed(db, order, now)
        if status == "sent" and previous_status != "sent":
            order.sent_at = order.sent_at or now
            register_product_outgoing_if_needed(db, order, now)
        if status == "delivered" and previous_status != "delivered":
            order.delivered_at = order.delivered_at or now
            register_product_outgoing_if_needed(db, order, now)
        if status == "finalized":
            if previous_status not in {"delivered", "resolved", "finalized"}:
                raise HTTPException(status_code=400, detail="Finalize somente depois da entrega ou resolução da disputa.")
            order.admin_note = ((order.admin_note or "") + "\nOperação finalizada administrativamente.").strip()
        if admin_note.strip():
            order.admin_note = admin_note.strip()
        item = db.get(AuctionItem, order.auction_id)
        if item and status in {"delivered", "finalized"}:
            item.status = "ended"
        audit_event(db, request, "order.status_changed", admin_user, "order", order.id, f"{previous_status} -> {status}. {admin_note.strip()}")
        nav_cache_clear()
        db.commit()
        tab = "admin-shipping" if status in {"processing", "purchased", "sent", "delivered", "dispute", "resolved"} else "admin-search-orders"
        return RedirectResponse(f"/admin?tab={tab}", status_code=303)
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
def account_request_withdrawal(request: Request, amount: float = Form(...), pix_key: str = Form("")):
    # Créditos LC são pré-pagos internos, como crédito de telefone: depois de comprados,
    # não podem ser sacados nem convertidos novamente em dinheiro. Mantemos a rota
    # para não quebrar formulários antigos, mas ela apenas informa a regra.
    db = SessionLocal()
    try:
        user = require_user(request, db)
        audit_event(db, request, "wallet.withdrawal_blocked_lc", user, "wallet", user.id, "Tentativa de saque bloqueada: Créditos LC não são sacáveis.")
        db.commit()
        return templates.TemplateResponse(
            "account_pages.html",
            {
                "request": request,
                "user": user,
                "section": "wallet",
                "error": "Créditos LC são créditos pré-pagos de uso interno e não podem ser sacados ou convertidos em dinheiro.",
            },
            status_code=400,
        )
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
        previous_status = req.status
        if previous_status == "paid" and status != "paid":
            raise HTTPException(status_code=400, detail="Saque já pago não pode voltar de status sem lançamento manual de estorno.")
        if previous_status == "rejected" and status != "rejected":
            raise HTTPException(status_code=400, detail="Saque rejeitado já foi devolvido ao saldo. Crie uma nova solicitação se necessário.")
        if previous_status in {"pending", "approved"} and status == "rejected":
            user = db.get(User, req.user_id)
            if user:
                user.wallet_balance = BR(user.wallet_balance + req.amount)
                db.add(WalletTransaction(user_id=user.id, amount=req.amount, kind="withdrawal_reversal", note=f"Saque #{req.id} recusado/devolvido ao saldo do usuário"))
        req.status = status
        req.admin_note = admin_note.strip()
        req.updated_at = datetime.utcnow()
        if status == "paid" and previous_status != "paid":
            db.add(WalletTransaction(user_id=req.user_id, amount=-BR(req.net_amount or req.amount or 0.0), kind="withdrawal_paid", note=f"Saque #{req.id} pago manualmente ao cliente"))
        admin_user = require_superadmin(request, db)
        audit_event(db, request, "withdrawal.status_changed", admin_user, "withdrawal", req.id, f"{previous_status} -> {status}. Bruto R$ {fmt_money(req.amount)} | taxa R$ {fmt_money(req.fee_amount)} | líquido R$ {fmt_money(req.net_amount or req.amount)}")
        nav_cache_clear()
        db.commit()
        return RedirectResponse("/admin?tab=admin-withdrawals", status_code=303)
    finally:
        db.close()




@app.post("/admin/order/{order_id}/finalize-operation")
def admin_finalize_order_operation(request: Request, order_id: int, admin_note: str = Form("")):
    db = SessionLocal()
    try:
        admin_user = require_admin(request, db)
        order = db.get(WinnerOrder, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        if order.status not in {"delivered", "resolved", "finalized"}:
            raise HTTPException(status_code=400, detail="A operação só pode ser finalizada depois da entrega ou resolução da disputa.")
        previous_status = order.status
        order.status = "finalized"
        note = admin_note.strip() or "Operação conferida e finalizada administrativamente."
        order.admin_note = ((order.admin_note or "") + f"\n{note}").strip()
        item = db.get(AuctionItem, order.auction_id)
        if item:
            item.status = "ended"
        audit_event(db, request, "order.finalized", admin_user, "order", order.id, f"{previous_status} -> finalized. {note}")
        nav_cache_clear()
        db.commit()
        return RedirectResponse("/admin?tab=admin-search-orders", status_code=303)
    finally:
        db.close()

@app.post("/admin/order/{order_id}/extend-payment")
def admin_extend_payment(request: Request, order_id: int, extra_minutes: int = Form(10)):
    db = SessionLocal()
    try:
        admin_user = require_superadmin(request, db)
        order = db.get(WinnerOrder, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        base = order.payment_deadline if order.payment_deadline and order.payment_deadline > datetime.utcnow() else datetime.utcnow()
        order.payment_deadline = base + timedelta(minutes=max(1, int(extra_minutes)))
        order.status = "pending_payment"
        audit_event(db, request, "order.payment_extended", admin_user, "order", order.id, f"Prazo estendido em {max(1, int(extra_minutes))} minutos")
        nav_cache_clear()
        db.commit()
        return RedirectResponse("/admin?tab=admin-pending-payments", status_code=303)
    finally:
        db.close()


@app.post("/admin/order/{order_id}/refund")
def admin_refund_order(request: Request, order_id: int, amount: float = Form(...), admin_note: str = Form("")):
    db = SessionLocal()
    try:
        admin_user = require_superadmin(request, db)
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
def admin_set_ticket_status(request: Request, ticket_id: int, status: str = Form(...), admin_note: str = Form(""), result: str = Form(""), priority: str = Form(""), category: str = Form("")):
    db = SessionLocal()
    try:
        admin_user = require_admin(request, db)
        ticket = db.get(SupportTicket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Chamado não encontrado.")
        if status not in SUPPORT_STATUSES:
            raise HTTPException(status_code=400, detail="Status inválido.")
        if result and result not in {"client", "site", "agreement", "manual_adjustment"}:
            raise HTTPException(status_code=400, detail="Resultado inválido.")
        previous_status = ticket.status
        if priority in SUPPORT_PRIORITIES:
            ticket.priority = priority
        if category in SUPPORT_CATEGORIES:
            ticket.category = category
        ticket.status = status
        ticket.result = result or ticket.result or ""
        note = (admin_note or "").strip()
        if note:
            ticket.admin_note = note
        ticket.updated_at = datetime.utcnow()
        ticket.closed_at = datetime.utcnow() if status in {"resolved", "closed"} else None
        if ticket.order_id:
            order = db.get(WinnerOrder, ticket.order_id)
            if order and status in {"dispute", "resolved", "closed"}:
                order.status = "resolved" if status in {"resolved", "closed"} else "dispute"
        support_add_message(db, ticket, f"Status alterado: {support_status_label(previous_status)} → {support_status_label(status)}. {note}", admin_id=admin_user.id, message_type="internal")
        audit_event(db, request, "ticket.status_changed", admin_user, "ticket", ticket.id, f"{previous_status} -> {status}. {ticket.admin_note}")
        nav_cache_clear()
        db.commit()
        return RedirectResponse(f"/admin?tab=admin-tickets&ticket={ticket.id}", status_code=303)
    finally:
        db.close()


@app.post("/admin/ticket/{ticket_id}/reply")
def admin_reply_ticket(request: Request, ticket_id: int, message: str = Form(""), next_status: str = Form("awaiting_customer")):
    db = SessionLocal()
    try:
        admin_user = require_admin(request, db)
        ticket = db.get(SupportTicket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Chamado não encontrado.")
        body = (message or "").strip()
        if len(body) < 2:
            raise HTTPException(status_code=400, detail="Digite uma resposta para o usuário.")
        if next_status not in SUPPORT_STATUSES:
            next_status = "awaiting_customer"
        ticket.assigned_admin_id = ticket.assigned_admin_id or admin_user.id
        ticket.status = next_status
        if next_status in {"resolved", "closed"}:
            ticket.closed_at = datetime.utcnow()
        support_add_message(db, ticket, body, admin_id=admin_user.id, message_type="admin")
        audit_event(db, request, "ticket.admin_replied", admin_user, "ticket", ticket.id, body[:300])
        nav_cache_clear()
        db.commit()
        return RedirectResponse(f"/admin?tab=admin-tickets&ticket={ticket.id}", status_code=303)
    finally:
        db.close()


@app.post("/admin/ticket/{ticket_id}/internal-note")
def admin_ticket_internal_note(request: Request, ticket_id: int, note: str = Form("")):
    db = SessionLocal()
    try:
        admin_user = require_admin(request, db)
        ticket = db.get(SupportTicket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Chamado não encontrado.")
        body = (note or "").strip()
        if len(body) < 2:
            raise HTTPException(status_code=400, detail="Digite uma nota interna.")
        ticket.assigned_admin_id = ticket.assigned_admin_id or admin_user.id
        ticket.admin_note = ((ticket.admin_note or "") + "\n" + body).strip()[-4000:]
        support_add_message(db, ticket, body, admin_id=admin_user.id, message_type="internal")
        audit_event(db, request, "ticket.internal_note", admin_user, "ticket", ticket.id, body[:300])
        nav_cache_clear()
        db.commit()
        return RedirectResponse(f"/admin?tab=admin-tickets&ticket={ticket.id}", status_code=303)
    finally:
        db.close()


@app.post("/admin/ticket/{ticket_id}/request-master-review")
def admin_ticket_master_review(request: Request, ticket_id: int, reason: str = Form("")):
    db = SessionLocal()
    try:
        admin_user = require_admin(request, db)
        ticket = db.get(SupportTicket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Chamado não encontrado.")
        body = (reason or "").strip() or "Solicitação de revisão pelo admin master."
        ticket.priority = "urgente"
        ticket.status = "dispute"
        support_add_message(db, ticket, "REVISÃO MASTER SOLICITADA: " + body, admin_id=admin_user.id, message_type="internal")
        audit_event(db, request, "ticket.master_review_requested", admin_user, "ticket", ticket.id, body[:500])
        nav_cache_clear()
        db.commit()
        return RedirectResponse(f"/admin?tab=admin-tickets&ticket={ticket.id}", status_code=303)
    finally:
        db.close()


@app.post("/admin/ticket/{ticket_id}/adjust-lc")
def admin_ticket_adjust_lc(request: Request, ticket_id: int, operation: str = Form("credit"), amount: float = Form(...), reason: str = Form("")):
    db = SessionLocal()
    try:
        admin_user = require_admin(request, db)
        ticket = db.get(SupportTicket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Chamado não encontrado.")
        target_user = db.get(User, ticket.user_id)
        if not target_user:
            raise HTTPException(status_code=404, detail="Usuário do chamado não encontrado.")
        amount = BR(amount)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Valor deve ser maior que zero.")
        if operation not in {"credit", "debit"}:
            raise HTTPException(status_code=400, detail="Operação inválida.")
        signed_amount = amount if operation == "credit" else -amount
        if not support_ticket_can_adjust(admin_user, signed_amount):
            support_add_message(db, ticket, f"Ajuste de {fmt_money(amount)} LC solicitado ao admin master. Motivo: {reason.strip()}", admin_id=admin_user.id, message_type="internal")
            ticket.priority = "urgente"
            ticket.status = "dispute"
            audit_event(db, request, "ticket.adjustment_requires_master", admin_user, "ticket", ticket.id, f"Valor: {fmt_money(amount)} LC | Motivo: {reason}")
            db.commit()
            return RedirectResponse(f"/admin?tab=admin-tickets&ticket={ticket.id}", status_code=303)
        reason = (reason or "").strip()
        if len(reason) < 8:
            raise HTTPException(status_code=400, detail="Informe um motivo claro para o ajuste manual.")
        previous_balance = BR(target_user.wallet_balance or 0.0)
        new_balance = BR(previous_balance + signed_amount)
        if new_balance < 0:
            raise HTTPException(status_code=400, detail="O ajuste deixaria a conta negativa.")
        target_user.wallet_balance = new_balance
        kind = "support_manual_credit" if signed_amount > 0 else "support_manual_debit"
        db.add(WalletTransaction(user_id=target_user.id, amount=signed_amount, kind=kind, note=f"Chamado {support_ticket_code(ticket.id)}: {reason[:160]}"))
        ticket.assigned_admin_id = ticket.assigned_admin_id or admin_user.id
        ticket.result = "manual_adjustment"
        support_add_message(db, ticket, f"Ajuste manual aplicado: {'+' if signed_amount > 0 else ''}{fmt_money(signed_amount)} LC. Saldo antes: {fmt_money(previous_balance)} LC. Saldo depois: {fmt_money(new_balance)} LC. Motivo: {reason}", admin_id=admin_user.id, message_type="internal")
        audit_event(db, request, "support.wallet_adjustment", admin_user, "user", target_user.id, f"Ticket {ticket.id} | {signed_amount} LC | {previous_balance} -> {new_balance} | {reason}")
        nav_cache_clear()
        db.commit()
        return RedirectResponse(f"/admin?tab=admin-tickets&ticket={ticket.id}", status_code=303)
    finally:
        db.close()


def _auction_ws_initial_payload_sync(auction_id: int) -> tuple[Optional[dict], bool]:
    db = SessionLocal()
    try:
        item = db.get(AuctionItem, auction_id)
        if not item:
            return None, False
        now = datetime.utcnow()
        changed = start_auction_if_due(item, now)
        finished_now = finish_auction_if_due(item, db, now, create_side_effects=False)
        changed = finished_now or changed
        if changed:
            db.commit()
            db.refresh(item)
        return public_auction_live_payload(item, db), bool(finished_now)
    finally:
        db.close()


@app.websocket("/ws/auction/{auction_id}")
async def auction_socket(websocket: WebSocket, auction_id: int):
    await manager.connect(auction_id, websocket)
    try:
        payload, finished_now = await asyncio.to_thread(_auction_ws_initial_payload_sync, auction_id)
        if payload:
            await manager.send_to(auction_id, websocket, {"type": "auction_update", "auction": payload})
        if finished_now:
            asyncio.create_task(asyncio.to_thread(ensure_finished_auction_side_effects, auction_id))

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(auction_id, websocket)
    except Exception:
        manager.disconnect(auction_id, websocket)
        try:
            await websocket.close()
        except Exception:
            pass
