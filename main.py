from fastapi import FastAPI
from pydantic import BaseModel
import requests
import json
import re
from datetime import datetime

app = FastAPI()

LEDGER_URL = "https://exam.sanand.workers.dev/questionData?email=23f2004539%40ds.study.iitm.ac.in&quizSign=QuOfYHujMhQnCI3QTAqKdodLDDP23tR1SR%2BR5d37rgcw81oakxkFqx1A4Zjffbc2OGA2cImgkEB9op%2BLlVCdxk87lSXA8EQgvQNsMbSvkDAJukQiI1SriJjI9XoDU3lfTyrmIFuwpmYzu9Ie13gGgycoeO11th77uCe%2FGTZOB%2BX6%2BpvdyEfovYl3b%2FnglHMRw1Ba%2BCrYmuMVmHAaGIDJ1ruj0%2BBkTxE6SgjsQlfNBhFVW7FCuTFjvsEgHJxOnZ%2FEtHMF6wPjk1HJVVzrnTAV2nhSSiaOE7T7J%2FGSnxX0nNT6YYPbhv1iNzi%2BvF9K%2FzbVn5Td3VAqvmnwsAlzyY8nZQ%3D%3D&questionId=q-ledger-agent-server&path=%2Fexport"

RATES = {
    "USD": 1.0,
    "EUR": 1.04,
    "INR": 0.01162
}

class Question(BaseModel):
    question: str


def load_orders():
    response = requests.get(LEDGER_URL, timeout=10)
    response.raise_for_status()

    orders = []

    for line in response.text.splitlines():
        line = line.strip()

        if not line:
            continue

        try:
            orders.append(json.loads(line))
        except Exception:
            continue

    # Keep only the latest version of each order
    current = {}

    for order in orders:
        order_id = order["id"]

        if (
            order_id not in current
            or order["updated_at"] > current[order_id]["updated_at"]
        ):
            current[order_id] = order

    return list(current.values())


def filter_orders(orders, question):
    q = question.lower()
    result = orders

    # Year
    year_match = re.search(r"\b(20\d{2})\b", q)
    year = int(year_match.group(1)) if year_match else None

    # Month
    months = {
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
        "december": 12
    }

    month = None

    for name, number in months.items():
        if name in q:
            month = number
            break

    # Region
    regions = ["north", "south", "east", "west", "central"]
    region = next((r for r in regions if r in q), None)

    # Product
    products = [
        "rice cooker",
        "air fryer",
        "refrigerator",
        "washing machine",
        "microwave",
        "mixer",
        "blender",
        "toaster",
        "kettle"
    ]

    product = next((p for p in products if p in q), None)

    # Customer
    customer_match = re.search(r"\bc\d{4}\b", q)
    customer = customer_match.group(0).upper() if customer_match else None

    # Date filtering
    if year or month:
        filtered = []

        for order in result:
            try:
                date_string = order["created_at"].replace("Z", "+00:00")
                dt = datetime.fromisoformat(date_string)

                if year and dt.year != year:
                    continue

                if month and dt.month != month:
                    continue

                filtered.append(order)

            except Exception:
                pass

        result = filtered

    if region:
        result = [
            o for o in result
            if o.get("region", "").lower() == region
        ]

    if product:
        result = [
            o for o in result
            if o.get("product", "").lower() == product
        ]

    if customer:
        result = [
            o for o in result
            if o.get("customer", "").upper() == customer
        ]

    return result


def usd_amount(order):
    currency = order.get("currency", "USD")
    return float(order.get("amount", 0)) * RATES.get(currency, 1)


def answer_question(question):
    orders = load_orders()
    q = question.lower()

    filtered = filter_orders(orders, question)

    # Refunds
    if "refund" in q or "refunded" in q:
        refunds = [
            o for o in filtered
            if o.get("status", "").lower() in
            ("refunded", "refund", "returned")
        ]

        if "how many" in q or "count" in q or "number" in q:
            return len(refunds)

        return round(sum(usd_amount(o) for o in refunds), 2)

    # Revenue / sales
    if (
        "revenue" in q
        or "sales" in q
        or "income" in q
        or "earned" in q
    ):
        paid = [
            o for o in filtered
            if o.get("status", "").lower() == "paid"
        ]

        if "how many units" in q or "number of units" in q:
            return sum(int(o.get("qty", 0)) for o in paid)

        if "how many orders" in q or "number of orders" in q:
            return len(paid)

        return round(sum(usd_amount(o) for o in paid), 2)

    # Quantity
    if "quantity" in q or "units" in q:
        return sum(int(o.get("qty", 0)) for o in filtered)

    # Order count
    if "order count" in q or "how many orders" in q or "number of orders" in q:
        return len(filtered)

    # Average paid order value
    if "average" in q or "avg" in q:
        paid = [
            o for o in filtered
            if o.get("status", "").lower() == "paid"
        ]

        if not paid:
            return 0

        values = [usd_amount(o) for o in paid]
        return round(sum(values) / len(values), 2)
    # Default to paid revenue
    paid = [
        o for o in filtered
        if o.get("status", "").lower() == "paid"
    ]

    return round(sum(usd_amount(o) for o in paid), 2)


@app.post("/")
def ask(question: Question):
    return {"answer": answer_question(question.question)}


@app.post("/answer")
def answer(question: Question):
    return {"answer": answer_question(question.question)}


@app.get("/")
def root():
    return {"status": "ok"}
