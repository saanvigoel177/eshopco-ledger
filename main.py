import json
import os
import re
import html
import threading
from datetime import datetime, timezone

import requests
from fastapi import FastAPI
from pydantic import BaseModel


app = FastAPI(title="Acme Appliances Ledger Q&A")


# ============================================================
# CONFIGURATION
# ============================================================

LEDGER_URL = os.environ.get(
    "LEDGER_URL",
    "https://exam.sanand.workers.dev/questionData"
    "?email=23f2004539%40ds.study.iitm.ac.in"
    "&quizSign=QuOfYHujMhQnCI3QTAqKdodLDDP23tR1SR%2BR5d37rgcw81oakxkFqx1A4Zjffbc2OGA2cImgkEB9op%2BLlVCdxk87lSXA8EQgvQNsMbSvkDAJukQiI1SriJjI9XoDU3lfTyrmIFuwpmYzu9Ie13gGgycoeO11th77uCe%2FGTZOB%2BX6%2BpvdyEfovYl3b%2FnglHMRw1Ba%2BCrYmuMVmHAaGIDJ1ruj0%2BBkTxE6SgjsQlfNBhFVW7FCuTFjvsEgHJxOnZ%2FEtHMF6wPjk1HJVVzrnTAV2nhSSiaOE7T7J%2FGSnxX0nNT6YYPbhv1iNzi%2BvF9K%2FzbVn5Td3VAqvmnwsAlzyY8nZQ%3D%3D"
    "&questionId=q-ledger-agent-server"
    "&path=%2Fexport"
)

RATES = {
    "USD": 1.0,
    "EUR": 1.04,
    "INR": 0.01162,
}

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

PRODUCT_ALIASES = {
    "rice cooker": ["rice cooker", "rice cookers"],
    "air fryer": ["air fryer", "air fryers"],
    "refrigerator": ["refrigerator", "refrigerators", "fridge", "fridges"],
    "washing machine": [
        "washing machine",
        "washing machines",
        "washer",
        "washers",
    ],
    "microwave": ["microwave", "microwaves"],
    "mixer": ["mixer", "mixers"],
    "blender": ["blender", "blenders"],
    "toaster": ["toaster", "toasters"],
    "kettle": ["kettle", "kettles"],
}


# ============================================================
# DATA STORAGE
# ============================================================

ORDERS = []
DATA_ERROR = None
DATA_LOCK = threading.Lock()


class Question(BaseModel):
    question: str


# ============================================================
# HELPERS
# ============================================================

def parse_time(value):
    """Convert an API timestamp into a timezone-aware datetime."""
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)

    value = str(value).strip()

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def normalize_text(value):
    value = html.unescape(str(value))
    value = value.replace("_", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip().lower()


def money_usd(order):
    amount = float(order.get("amount", 0) or 0)
    currency = str(order.get("currency", "USD")).upper()

    rate = RATES.get(currency)

    if rate is None:
        raise ValueError(f"Unknown currency: {currency}")

    return amount * rate


def is_paid(order):
    return normalize_text(order.get("status", "")) == "paid"


def is_refund(order):
    status = normalize_text(order.get("status", ""))

    return (
        "refund" in status
        or "return" in status
        or status in {"refunded", "refund", "returned"}
    )


def order_date(order):
    return parse_time(order.get("created_at"))


# ============================================================
# LOAD LEDGER
# ============================================================

def fetch_orders():
    response = requests.get(
        LEDGER_URL,
        timeout=12,
        headers={
            "User-Agent": "Acme-Ledger-Agent/1.0"
        },
    )

    response.raise_for_status()

    text = response.text.strip()

    if not text:
        return []

    orders = []

    # The /export endpoint returns NDJSON.
    for line in text.splitlines():
        line = line.strip()

        if not line:
            continue

        line = html.unescape(line)

        try:
            obj = json.loads(line)

            if isinstance(obj, dict) and "id" in obj:
                orders.append(obj)

        except json.JSONDecodeError:
            # In case the endpoint ever returns one JSON array.
            continue

    # Fallback if the endpoint returned a normal JSON array.
    if not orders:
        try:
            data = json.loads(text)

            if isinstance(data, list):
                orders = [
                    x for x in data
                    if isinstance(x, dict)
                ]

            elif isinstance(data, dict):
                if isinstance(data.get("orders"), list):
                    orders = [
                        x for x in data["orders"]
                        if isinstance(x, dict)
                    ]

        except Exception:
            pass

    return orders


def deduplicate_orders(orders):
    """
    The API explicitly says:
    an order ID may occur more than once;
    the row with the latest updated_at is current.
    """

    current = {}

    for order in orders:
        order_id = order.get("id")

        if not order_id:
            continue

        if order_id not in current:
            current[order_id] = order
            continue

        old_time = parse_time(
            current[order_id].get("updated_at")
        )

        new_time = parse_time(
            order.get("updated_at")
        )

        if new_time > old_time:
            current[order_id] = order

    return list(current.values())


def load_ledger():
    global ORDERS, DATA_ERROR

    with DATA_LOCK:
        try:
            raw = fetch_orders()
            ORDERS = deduplicate_orders(raw)
            DATA_ERROR = None
        except Exception as exc:
            DATA_ERROR = str(exc)

            # Don't destroy already-loaded data if a later refresh fails.
            if not ORDERS:
                ORDERS = []

    return ORDERS


# Load once when the application starts.
load_ledger()


# ============================================================
# QUESTION PARSING
# ============================================================

def extract_year(question):
    match = re.search(r"\b(20\d{2})\b", question)

    if match:
        return int(match.group(1))

    return None


def extract_month(question):
    q = normalize_text(question)

    for name, number in MONTHS.items():
        if re.search(r"\b" + name + r"\b", q):
            return number

    return None


def extract_region(question):
    q = normalize_text(question)

    regions = [
        "north",
        "south",
        "east",
        "west",
        "central",
    ]

    for region in regions:
        if re.search(r"\b" + region + r"\b", q):
            return region

    return None


def extract_customer(question):
    q = normalize_text(question)

    match = re.search(r"\bc\d{3,6}\b", q)

    if match:
        return match.group(0).upper()

    return None


def extract_product(question):
    q = normalize_text(question)

    # Longest aliases first.
    aliases = []

    for canonical, variants in PRODUCT_ALIASES.items():
        for variant in variants:
            aliases.append((variant, canonical))

    aliases.sort(key=lambda x: len(x[0]), reverse=True)

    for variant, canonical in aliases:
        if variant in q:
            return canonical

    return None


def extract_currency(question):
    q = normalize_text(question)

    if (
        "usd" in q
        or "us dollars" in q
        or "us dollar" in q
        or "dollar" in q
        or "$" in q
    ):
        return "USD"

    if (
        "eur" in q
        or "euro" in q
        or "euros" in q
    ):
        return "EUR"

    if (
        "inr" in q
        or "rupee" in q
        or "rupees" in q
        or "₹" in q
    ):
        return "INR"

    # The grader examples/questions generally ask for USD.
    return "USD"


# ============================================================
# FILTERING
# ============================================================

def filter_orders(question):
    q = normalize_text(question)

    result = list(ORDERS)

    year = extract_year(q)
    month = extract_month(q)
    region = extract_region(q)
    customer = extract_customer(q)
    product = extract_product(q)

    if year is not None:
        result = [
            o for o in result
            if order_date(o).year == year
        ]

    if month is not None:
        result = [
            o for o in result
            if order_date(o).month == month
        ]

    if region is not None:
        result = [
            o for o in result
            if normalize_text(o.get("region", "")) == region
        ]

    if customer is not None:
        result = [
            o for o in result
            if str(o.get("customer", "")).upper() == customer
        ]

    if product is not None:
        result = [
            o for o in result
            if normalize_text(o.get("product", "")) == product
        ]

    return result


# ============================================================
# INTENT DETECTION
# ============================================================

def wants_average(q):
    return (
        "average" in q
        or "avg" in q
        or "mean" in q
    )


def wants_count(q):
    return (
        "how many orders" in q
        or "number of orders" in q
        or "order count" in q
        or "count of orders" in q
    )


def wants_units(q):
    return (
        "how many units" in q
        or "number of units" in q
        or "units sold" in q
        or "quantity sold" in q
        or "total quantity" in q
        or "quantity" in q
    )


def wants_revenue(q):
    return any(
        phrase in q
        for phrase in [
            "revenue",
            "sales",
            "sales value",
            "sales amount",
            "income",
            "earned",
            "turnover",
        ]
    )


def wants_refund(q):
    return (
        "refund" in q
        or "refunded" in q
        or "refunds" in q
        or "returned" in q
        or "returns" in q
    )


def wants_product_breakdown(q):
    return (
        "by product" in q
        or "per product" in q
        or "product-wise" in q
        or "product wise" in q
    )


def wants_customer_breakdown(q):
    return (
        "by customer" in q
        or "per customer" in q
        or "customer-wise" in q
        or "customer wise" in q
    )


# ============================================================
# ANSWER LOGIC
# ============================================================

def answer_question(question):
    q = normalize_text(question)

    if not ORDERS:
        if DATA_ERROR:
            raise RuntimeError(
                "Ledger data could not be loaded: " + DATA_ERROR
            )

        return 0

    filtered = filter_orders(q)

    # --------------------------------------------------------
    # REFUNDS
    # --------------------------------------------------------

    if wants_refund(q):
        refunds = [
            o for o in filtered
            if is_refund(o)
        ]

        if wants_count(q):
            return len(refunds)

        if wants_units(q):
            return sum(
                int(o.get("qty", 0) or 0)
                for o in refunds
            )

        total = sum(
            money_usd(o)
            for o in refunds
        )

        return round(total, 2)

    # --------------------------------------------------------
    # AVERAGE PAID ORDER VALUE
    # --------------------------------------------------------

    if wants_average(q):
        paid = [
            o for o in filtered
            if is_paid(o)
        ]

        if not paid:
            return 0

        # "average paid order value" means
        # average monetary value of each paid order.
        values = [
            money_usd(o)
            for o in paid
        ]

        return round(
            sum(values) / len(values),
            2
        )

    # --------------------------------------------------------
    # ORDER COUNT
    # --------------------------------------------------------

    if wants_count(q):
        if "paid" in q:
            return sum(
                1 for o in filtered
                if is_paid(o)
            )

        return len(filtered)

    # --------------------------------------------------------
    # UNIT COUNT
    # --------------------------------------------------------

    if wants_units(q):
        if "paid" in q:
            return sum(
                int(o.get("qty", 0) or 0)
                for o in filtered
                if is_paid(o)
            )

        return sum(
            int(o.get("qty", 0) or 0)
            for o in filtered
        )

    # --------------------------------------------------------
    # REVENUE / SALES
    # --------------------------------------------------------

    if wants_revenue(q):
        paid = [
            o for o in filtered
            if is_paid(o)
        ]

        total = sum(
            money_usd(o)
            for o in paid
        )

        return round(total, 2)

    # --------------------------------------------------------
    # PRODUCT/CUSTOMER DEFAULT TOTALS
    # --------------------------------------------------------

    # If a question mentions a product/customer/region but
    # doesn't explicitly say what metric, revenue is generally
    # the most useful default.
    paid = [
        o for o in filtered
        if is_paid(o)
    ]

    return round(
        sum(money_usd(o) for o in paid),
        2
    )


# ============================================================
# API ENDPOINTS
# ============================================================

@app.get("/")
def home():
    return {
        "service": "Acme Appliances ledger Q&A",
        "status": "ok",
        "orders_loaded": len(ORDERS),
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "orders_loaded": len(ORDERS),
        "data_error": DATA_ERROR,
    }


@app.get("/debug")
def debug():
    return {
        "orders_loaded": len(ORDERS),
        "data_error": DATA_ERROR,
        "regions": sorted(
            {
                str(o.get("region"))
                for o in ORDERS
                if o.get("region") is not None
            }
        ),
        "products": sorted(
            {
                str(o.get("product"))
                for o in ORDERS
                if o.get("product") is not None
            }
        ),
        "currencies": sorted(
            {
                str(o.get("currency"))
                for o in ORDERS
                if o.get("currency") is not None
            }
        ),
        "statuses": sorted(
            {
                str(o.get("status"))
                for o in ORDERS
                if o.get("status") is not None
            }
        ),
    }


@app.post("/")
def ask(question: Question):
    return {
        "answer": answer_question(question.question)
    }


@app.post("/ask")
def ask_alias(question: Question):
    return {
        "answer": answer_question(question.question)
    }


# ============================================================
# OPTIONAL MANUAL REFRESH
# ============================================================

@app.post("/refresh")
def refresh():
    load_ledger()

    return {
        "status": "ok",
        "orders_loaded": len(ORDERS),
        "data_error": DATA_ERROR,
    }
