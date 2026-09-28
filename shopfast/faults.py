"""Fault switches for the ShopFast demo. Each fault matches a pattern in the seed incident history,
except PAYMENT_GATEWAY_TIMEOUT and PROMO_CONFIG_BROKEN, which have no history and demonstrate the learning loop."""

from enum import Enum


class Fault(str, Enum):
    DB_POOL_EXHAUST = "DB_POOL_EXHAUST"
    REDIS_TIMEOUT = "REDIS_TIMEOUT"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    PAYMENT_GATEWAY_TIMEOUT = "PAYMENT_GATEWAY_TIMEOUT"
    INVENTORY_DEADLOCK = "INVENTORY_DEADLOCK"
    CERT_EXPIRED = "CERT_EXPIRED"
    SEARCH_DISK_FULL = "SEARCH_DISK_FULL"
    PROMO_CONFIG_BROKEN = "PROMO_CONFIG_BROKEN"


# Realistic log line each fault produces. Kept here so app and tests share one source.
FAULT_LOGS: dict[Fault, str] = {
    Fault.DB_POOL_EXHAUST: (
        "ERROR payment-api sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) "
        "FATAL: remaining connection slots are reserved for non-replication superuser connections"
    ),
    Fault.REDIS_TIMEOUT: (
        "ERROR cart-service redis.exceptions.TimeoutError: "
        "Timeout reading from redis-cart.shopfast.internal:6379"
    ),
    Fault.AUTH_TOKEN_EXPIRED: (
        "WARN api-gateway jwt.exceptions.ExpiredSignatureError: Signature has expired"
    ),
    Fault.PAYMENT_GATEWAY_TIMEOUT: (
        "ERROR payment-api httpx.ReadTimeout: POST https://api.paygate.example/v1/charges "
        "timed out after 10.0s"
    ),
    Fault.INVENTORY_DEADLOCK: (
        "ERROR inventory-service psycopg2.errors.DeadlockDetected: deadlock detected "
        "DETAIL: Process 5120 waits for ShareLock on transaction 118233; blocked by process 5127."
    ),
    Fault.CERT_EXPIRED: (
        "ERROR api-gateway ssl.SSLCertVerificationError: certificate verify failed: "
        "certificate has expired (auth-service.shopfast.internal:443)"
    ),
    Fault.SEARCH_DISK_FULL: (
        "WARN search-service elasticsearch cluster health status changed from [YELLOW] to [RED]; "
        "reason: [shards unassigned] disk watermark [95%] exceeded on node es-data-1"
    ),
    Fault.PROMO_CONFIG_BROKEN: (
        "ERROR promotions-service KeyError: 'discount_pct' while applying promotion FESTIVE25 "
        "(config version 2026-09-28.3)"
    ),
}


class FaultRegistry:
    """In-memory set of active faults. State resets on restart, which is fine for a demo."""

    def __init__(self) -> None:
        self._active: set[Fault] = set()

    def enable(self, fault: Fault) -> None:
        self._active.add(fault)

    def disable(self, fault: Fault) -> None:
        self._active.discard(fault)

    def is_active(self, fault: Fault) -> bool:
        return fault in self._active

    def active(self) -> list[str]:
        return sorted(f.value for f in self._active)
