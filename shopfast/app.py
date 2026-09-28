"""ShopFast mock API. Run: uvicorn shopfast.app:app --host 127.0.0.1 --port 8001

Bind to 127.0.0.1 only: /admin/faults and /ops/actions have no auth and must stay local.

When a fault is on, the affected endpoint fails with its real-looking log line. The line is written to the
server log and returned in the response body as "log", ready to paste into the agent UI.
"""

import logging
import secrets
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from shopfast.alerts import AlertLog
from shopfast.faults import FAULT_LOGS, Fault, FaultRegistry
from shopfast.remediations import ACTIONS

logger = logging.getLogger("shopfast")

app = FastAPI(title="ShopFast (mock)")
faults = FaultRegistry()
alerts = AlertLog()

PRODUCTS: dict[str, dict] = {
    "sku-100": {"id": "sku-100", "name": "Running shoes", "price": 89.99},
    "sku-200": {"id": "sku-200", "name": "Rain jacket", "price": 129.00},
    "sku-300": {"id": "sku-300", "name": "Water bottle", "price": 14.50},
}

# Endpoint failure for each fault: HTTP status and short error message.
FAULT_RESPONSES: dict[Fault, tuple[int, str]] = {
    Fault.REDIS_TIMEOUT: (503, "Cart and catalog cache unavailable"),
    Fault.AUTH_TOKEN_EXPIRED: (401, "Session token expired"),
    Fault.DB_POOL_EXHAUST: (503, "Orders database unavailable"),
    Fault.PAYMENT_GATEWAY_TIMEOUT: (504, "Payment gateway timed out"),
    Fault.INVENTORY_DEADLOCK: (500, "Stock reservation failed"),
    Fault.CERT_EXPIRED: (502, "Upstream TLS handshake failed"),
    Fault.SEARCH_DISK_FULL: (503, "Catalog search unavailable"),
    Fault.PROMO_CONFIG_BROKEN: (500, "Promotion engine error"),
}


class CartItem(BaseModel):
    product_id: str = Field(min_length=1, max_length=32)
    quantity: int = Field(ge=1, le=10)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class CheckoutRequest(BaseModel):
    items: list[CartItem] = Field(min_length=1, max_length=20)


class FaultTriggered(Exception):
    def __init__(self, fault: Fault) -> None:
        self.fault = fault


@app.exception_handler(FaultTriggered)
def fault_response(request: Request, exc: FaultTriggered) -> JSONResponse:
    status, error = FAULT_RESPONSES[exc.fault]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{stamp} {FAULT_LOGS[exc.fault]}"
    logger.error(line)
    alerts.record(exc.fault, request.method, request.url.path, status, error, line)
    return JSONResponse(status_code=status, content={"error": error, "log": line})


def fail_if_active(*checks: Fault) -> None:
    """Raise for the first active fault, in the order the endpoint would hit those dependencies."""
    for fault in checks:
        if faults.is_active(fault):
            raise FaultTriggered(fault)


def product(product_id: str) -> dict:
    if product_id not in PRODUCTS:
        raise HTTPException(status_code=404, detail=f"Unknown product {product_id}")
    return PRODUCTS[product_id]


@app.get("/products")
def list_products() -> list[dict]:
    fail_if_active(Fault.REDIS_TIMEOUT, Fault.SEARCH_DISK_FULL)
    return list(PRODUCTS.values())


@app.post("/cart/items")
def add_to_cart(item: CartItem) -> dict:
    product(item.product_id)
    fail_if_active(Fault.REDIS_TIMEOUT)
    return {"product_id": item.product_id, "quantity": item.quantity}


@app.post("/login")
def login(credentials: LoginRequest) -> dict:
    fail_if_active(Fault.CERT_EXPIRED, Fault.AUTH_TOKEN_EXPIRED)
    return {"token": secrets.token_urlsafe(16), "username": credentials.username}


@app.post("/checkout")
def checkout(order: CheckoutRequest) -> dict:
    total = sum(product(item.product_id)["price"] * item.quantity for item in order.items)
    # Order of dependencies a checkout hits: orders DB, stock reservation, promotions, payment gateway.
    fail_if_active(Fault.DB_POOL_EXHAUST, Fault.INVENTORY_DEADLOCK, Fault.PROMO_CONFIG_BROKEN,
                   Fault.PAYMENT_GATEWAY_TIMEOUT)
    return {"order_id": f"ORD-{secrets.randbelow(10**8):08d}", "total": round(total, 2)}


@app.get("/ops/actions")
def list_actions() -> list[dict]:
    """Runbook actions the agent may propose. Which fault each one fixes is deliberately not exposed."""
    return [{"name": name, "description": action.description} for name, action in ACTIONS.items()]


@app.post("/ops/actions/{name}")
def run_action(name: str) -> dict:
    if name not in ACTIONS:
        raise HTTPException(status_code=404, detail=f"Unknown action {name}")
    for fault in ACTIONS[name].fixes:
        faults.disable(fault)
    logger.info("ops action %s executed", name)
    return {"action": name, "executed": True}


@app.get("/ops/incidents/latest")
def latest_incident() -> dict:
    """The newest failure alert, for the agent's automatic incident intake."""
    alert = alerts.latest()
    if alert is None:
        raise HTTPException(status_code=404, detail="No failures recorded")
    return alert


@app.get("/admin/faults")
def get_faults() -> dict:
    return {"active": faults.active()}


@app.post("/admin/faults/{fault}")
def enable_fault(fault: Fault) -> dict:
    faults.enable(fault)
    return {"active": faults.active()}


@app.delete("/admin/faults/{fault}")
def disable_fault(fault: Fault) -> dict:
    faults.disable(fault)
    return {"active": faults.active()}
