from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upc: Mapped[str] = mapped_column(Text, unique=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_code_raw: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    gtin_normalized: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    size: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    default_location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lookup_source: Mapped[str] = mapped_column(String(64), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    receipt_items = relationship("ReceiptItem", back_populates="product")
    inventory = relationship("Inventory", back_populates="product", uselist=False)


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_receipt_fingerprint"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store: Mapped[str] = mapped_column(String(64), default="Meijer")
    purchase_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    store_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    terminal: Mapped[str | None] = mapped_column(String(32), nullable=True)
    transaction_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    operator: Mapped[str | None] = mapped_column(String(32), nullable=True)
    purchase_time: Mapped[str | None] = mapped_column(String(32), nullable=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_type: Mapped[str] = mapped_column(String(64))
    source_format: Mapped[str] = mapped_column(String(64))
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    items = relationship("ReceiptItem", back_populates="receipt", cascade="all, delete-orphan")


class ReceiptItem(Base):
    __tablename__ = "receipt_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    raw_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalized_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    line_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    tax_flag: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    parse_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="unresolved")

    receipt = relationship("Receipt", back_populates="items")
    product = relationship("Product", back_populates="receipt_items")


class Inventory(Base):
    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), unique=True, index=True)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    package_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    package_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    package_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    usable_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    usable_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reserved_quantity: Mapped[float] = mapped_column(Float, default=0, server_default="0")

    product = relationship("Product", back_populates="inventory")


class BrowserDraft(Base):
    __tablename__ = "browser_drafts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scan_json: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ProductLookupCache(Base):
    __tablename__ = "product_lookup_cache"
    __table_args__ = (UniqueConstraint("upc", "source", name="uq_lookup_upc_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upc: Mapped[str] = mapped_column(Text, index=True)
    source: Mapped[str] = mapped_column(String(64))
    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    lookup_timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16))


class ProductLookupRateLimit(Base):
    __tablename__ = "product_lookup_rate_limits"

    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_lookup_at: Mapped[datetime] = mapped_column(DateTime)


class RecipeSession(Base):
    __tablename__ = "recipe_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="planning")
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    items = relationship("RecipeSessionItem", back_populates="session", cascade="all, delete-orphan")


class RecipeSessionItem(Base):
    __tablename__ = "recipe_session_items"
    __table_args__ = (UniqueConstraint("recipe_session_id", "inventory_item_id", name="uq_recipe_inventory"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_session_id: Mapped[int] = mapped_column(ForeignKey("recipe_sessions.id"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory.id"), index=True)
    ingredient_name: Mapped[str] = mapped_column(String(255))
    requested_amount: Mapped[float] = mapped_column(Float)
    requested_unit: Mapped[str] = mapped_column(String(16))
    reserved_amount: Mapped[float] = mapped_column(Float, default=0)
    reserved_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    consumed_amount: Mapped[float] = mapped_column(Float, default=0)
    consumed_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    session = relationship("RecipeSession", back_populates="items")


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory.id"), index=True)
    change_amount: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    quantity_field: Mapped[str] = mapped_column(String(32), default="usable_quantity")
    transaction_type: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipe_sessions.id"), nullable=True, index=True)
    undo_of_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_transactions.id"), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
