from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, Boolean, JSON, Numeric, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

def now(): return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    passcode_ciphertext: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class LoginSession(Base):
    __tablename__ = 'login_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class LoginAttempt(Base):
    __tablename__ = 'login_attempts'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)

class MenuItem(Base):
    __tablename__ = 'menu_items'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    service_details: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    category: Mapped[str] = mapped_column(String(40))
    price: Mapped[Decimal] = mapped_column(Numeric(12,2))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Category(Base):
    __tablename__ = 'categories'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Customer(Base):
    gstin: Mapped[str] = mapped_column(String(15), default='', server_default='')
    __tablename__ = 'customers'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str] = mapped_column(String(20), default='')

class CompanySettings(Base):
    gstin: Mapped[str] = mapped_column(String(15), default='', server_default='')
    state_code: Mapped[str] = mapped_column(String(2), default='', server_default='')
    __tablename__ = 'company_settings'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    address: Mapped[str] = mapped_column(String(200), default='')
    phone: Mapped[str] = mapped_column(String(20), default='')
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5,2), default=0)

class Order(Base):
    gst_details: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    __tablename__ = 'orders'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(20), default='Completed', server_default='Completed')
    cancellation: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    request_id: Mapped[str] = mapped_column(String(36), unique=True)
    request_body: Mapped[dict] = mapped_column(JSON)
    staff_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    staff_name: Mapped[str] = mapped_column(String(80))
    staff_username: Mapped[str] = mapped_column(String(80), default='', server_default='')
    customer_id: Mapped[str | None] = mapped_column(ForeignKey('customers.id'), nullable=True)
    customer_name: Mapped[str] = mapped_column(String(80), default='')
    order_type: Mapped[str] = mapped_column(String(20))
    payment: Mapped[str] = mapped_column(String(20))
    payment_details: Mapped[list] = mapped_column(JSON,default=list,server_default='[]')
    items: Mapped[list] = mapped_column(JSON)
    total: Mapped[Decimal] = mapped_column(Numeric(14,2))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14,2))
    tax: Mapped[Decimal] = mapped_column(Numeric(14,2))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5,2))
    address: Mapped[str] = mapped_column(String(200), default='')
    phone: Mapped[str] = mapped_column(String(20), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)

class Audit(Base):
    __tablename__ = 'audit_log'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    action: Mapped[str] = mapped_column(String(60))
    target_id: Mapped[str] = mapped_column(String(60))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class CashDay(Base):
    __tablename__='cash_days'
    day: Mapped[str] = mapped_column(String(10),primary_key=True)
    opening_cash: Mapped[Decimal] = mapped_column(Numeric(14,2))
    opened_by: Mapped[str] = mapped_column(ForeignKey('users.id'))
    opened_name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20),default='Open')
    closing: Mapped[dict] = mapped_column(JSON,default=dict)
    history: Mapped[list] = mapped_column(JSON,default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)

class CashMovement(Base):
    __tablename__='cash_movements'
    id: Mapped[int] = mapped_column(Integer,primary_key=True,autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(36),unique=True)
    day: Mapped[str] = mapped_column(ForeignKey('cash_days.day'),index=True)
    kind: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2))
    reason: Mapped[str] = mapped_column(String(300))
    bill_id: Mapped[int | None] = mapped_column(ForeignKey('orders.id'),nullable=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    actor_name: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean,default=True)
    void_reason: Mapped[str] = mapped_column(String(300),default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)
