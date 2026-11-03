
import os
import json
import base64
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from openai import OpenAI

load_dotenv()

app = FastAPI(title="StockSense")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "stocksense.db"
STATIC_DIR = BASE_DIR / "static"

STATIC_DIR.mkdir(exist_ok=True)

app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static",
)


# ---------------- DATABASE ----------------

def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    with connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                stock INTEGER NOT NULL DEFAULT 0
                    CHECK(stock >= 0),
                min_stock INTEGER NOT NULL DEFAULT 5
                    CHECK(min_stock >= 0)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                expected INTEGER NOT NULL CHECK(expected >= 0),
                received INTEGER NOT NULL CHECK(received >= 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(product_id) REFERENCES products(id)
            )
        """)

        # Migrate older databases created by the first prototype.
        columns = {
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(deliveries)"
            ).fetchall()
        }

        if "created_at" not in columns:
            db.execute("""
                ALTER TABLE deliveries
                ADD COLUMN created_at TEXT
            """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS stock_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                change INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(product_id) REFERENCES products(id)
            )
        """)


init_db()


# ---------------- REQUEST MODELS ----------------

class Product(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    stock: int = Field(default=0, ge=0, strict=True)
    min_stock: int = Field(default=5, ge=0, strict=True)


class Delivery(BaseModel):
    product_id: int = Field(gt=0, strict=True)
    expected: int = Field(ge=0, strict=True)
    received: int = Field(ge=0, strict=True)


class ConfirmDelivery(BaseModel):
    items: list[Delivery] = Field(min_length=1, max_length=100)


class StockAdjustment(BaseModel):
    change: int = Field(strict=True)
    reason: str = Field(min_length=1, max_length=200)


# ---------------- HELPERS ----------------

def get_product(db, product_id):
    product = db.execute(
        "SELECT * FROM products WHERE id = ?",
        (product_id,),
    ).fetchone()

    if not product:
        raise HTTPException(404, "Product not found")

    return product


def save_delivery(db, item):
    db.execute(
        """
        INSERT INTO deliveries (product_id, expected, received)
        VALUES (?, ?, ?)
        """,
        (item.product_id, item.expected, item.received),
    )

    db.execute(
        """
        UPDATE products
        SET stock = stock + ?
        WHERE id = ?
        """,
        (item.received, item.product_id),
    )


def delivery_result(items):
    return {
        "message": "Delivery recorded",
        "items": [
            {
                "product_id": item.product_id,
                "expected": item.expected,
                "received": item.received,
                "difference": item.received - item.expected,
                "discrepancy": item.received != item.expected,
            }
            for item in items
        ],
    }


# ---------------- GENERAL ----------------

@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---------------- PRODUCTS ----------------

@app.get("/api/products")
def get_products():
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM products ORDER BY name"
        ).fetchall()

    return [dict(row) for row in rows]


@app.post("/api/products")
def add_product(product: Product):
    name = product.name.strip()

    if not name:
        raise HTTPException(400, "Product name is required")

    with connect() as db:
        existing = db.execute(
            "SELECT id FROM products WHERE LOWER(name) = LOWER(?)",
            (name,),
        ).fetchone()

        if existing:
            raise HTTPException(409, "Product already exists")

        cursor = db.execute(
            """
            INSERT INTO products (name, stock, min_stock)
            VALUES (?, ?, ?)
            """,
            (name, product.stock, product.min_stock),
        )

        return {
            "id": cursor.lastrowid,
            "message": "Product added",
        }


@app.patch("/api/products/{product_id}/adjust")
def adjust_stock(product_id: int, adjustment: StockAdjustment):
    reason = adjustment.reason.strip()

    if not reason:
        raise HTTPException(400, "Adjustment reason is required")

    if adjustment.change == 0:
        raise HTTPException(400, "Stock change cannot be zero")

    with connect() as db:
        product = get_product(db, product_id)

        new_stock = product["stock"] + adjustment.change

        if new_stock < 0:
            raise HTTPException(
                400,
                "Stock adjustment would make inventory negative",
            )

        db.execute(
            "UPDATE products SET stock = ? WHERE id = ?",
            (new_stock, product_id),
        )

        db.execute(
            """
            INSERT INTO stock_adjustments
                (product_id, change, reason)
            VALUES (?, ?, ?)
            """,
            (product_id, adjustment.change, reason),
        )

    return {
        "message": "Stock adjusted",
        "new_stock": new_stock,
    }


# ---------------- DELIVERIES ----------------

@app.get("/api/deliveries")
def get_deliveries():
    with connect() as db:
        rows = db.execute("""
            SELECT
                d.id,
                d.product_id,
                p.name,
                d.expected,
                d.received,
                d.received - d.expected AS difference,
                d.created_at
            FROM deliveries d
            JOIN products p ON p.id = d.product_id
            ORDER BY d.id DESC
        """).fetchall()

    return [dict(row) for row in rows]


@app.post("/api/deliveries")
def record_delivery(delivery: Delivery):
    with connect() as db:
        get_product(db, delivery.product_id)
        save_delivery(db, delivery)

    return delivery_result([delivery])


@app.post("/api/ai/confirm")
def confirm_ai_delivery(data: ConfirmDelivery):
    items = data.items

    # Reject duplicate product mappings in the same confirmation.
    product_ids = [item.product_id for item in items]

    if len(product_ids) != len(set(product_ids)):
        raise HTTPException(
            400,
            "Each product can appear only once per confirmation",
        )

    with connect() as db:
        # Validate every item before changing anything.
        for item in items:
            get_product(db, item.product_id)

        # The transaction commits only if all writes succeed.
        for item in items:
            save_delivery(db, item)

    return delivery_result(items)


# ---------------- META AI ----------------

@app.post("/api/ai/extract")
async def extract_delivery(file: UploadFile = File(...)):
    allowed_types = {
        "image/jpeg",
        "image/png",
        "image/webp",
    }

    if file.content_type not in allowed_types:
        raise HTTPException(
            400,
            "Upload a JPG, PNG, or WEBP image",
        )

    image_bytes = await file.read()

    if not image_bytes:
        raise HTTPException(400, "Empty image")

    if len(image_bytes) > 5 * 1024 * 1024:
        raise HTTPException(400, "Image must be under 5 MB")

    api_key = os.getenv("MODEL_API_KEY")

    if not api_key:
        raise HTTPException(
            500,
            "MODEL_API_KEY is not configured",
        )

    client = OpenAI(
        base_url="https://api.meta.ai/v1",
        api_key=api_key,
        timeout=60,
    )

    image_data = base64.b64encode(image_bytes).decode("utf-8")

    try:
        response = client.chat.completions.create(
            model="muse-spark-1.3",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": """
                            Extract product names and received quantities
                            from this delivery document.

                            Return only a JSON array:
                            [
                              {
                                "name": "Rice bags",
                                "quantity": 10
                              }
                            ]

                            Use non-negative integer quantities.
                            Do not guess unreadable quantities.
                            Return [] if no delivery items are visible.
                            """
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    f"data:{file.content_type};"
                                    f"base64,{image_data}"
                                )
                            },
                        },
                    ],
                }
            ],
        )

        content = response.choices[0].message.content

        if not content:
            raise HTTPException(502, "Empty AI response")

        # Some models may wrap JSON in a code fence.
        content = content.strip()

        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            content = content.rsplit("```", 1)[0].strip()

        items = json.loads(content)

    except json.JSONDecodeError:
        raise HTTPException(502, "AI returned invalid JSON")

    except HTTPException:
        raise

    except Exception:
        raise HTTPException(
            502,
            "Meta AI request failed. Check API access and configuration.",
        )

    if not isinstance(items, list):
        raise HTTPException(502, "Unexpected AI response")

    validated_items = []

    for item in items:
        if not isinstance(item, dict):
            raise HTTPException(502, "Invalid product data")

        name = item.get("name")
        quantity = item.get("quantity")

        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name.strip()) > 100
            or not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or quantity < 0
        ):
            raise HTTPException(502, "Invalid product data")

        validated_items.append({
            "name": name.strip(),
            "quantity": quantity,
        })

    return {"items": validated_items}
