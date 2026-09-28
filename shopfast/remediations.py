"""Runbook actions for the ShopFast demo: the only remediation the agent may perform.

Each action is simulated: it clears the faults it would fix in a real system. The agent sees only an
action's name and description, never which fault it fixes, so choosing the right one takes reasoning and
memory. Some actions are decoys that fix nothing, like restarting pods during pool exhaustion.
"""

from dataclasses import dataclass, field

from shopfast.faults import Fault


@dataclass(frozen=True)
class Remediation:
    description: str
    fixes: frozenset[Fault] = field(default_factory=frozenset)


ACTIONS: dict[str, Remediation] = {
    "rollback_payment_api": Remediation(
        "Roll payment-api back to its previous release (reverts the latest worker and connection pool settings)",
        frozenset({Fault.DB_POOL_EXHAUST}),
    ),
    "restart_payment_api_pods": Remediation("Restart all payment-api pods"),
    "scale_out_payment_api": Remediation("Add two more payment-api pods"),
    "disable_cart_analytics": Remediation(
        "Turn off the cart analytics feature flag, which scans Redis keys",
        frozenset({Fault.REDIS_TIMEOUT}),
    ),
    "raise_redis_client_timeout": Remediation("Raise the cart-service Redis client read timeout from 1 s to 5 s"),
    "failover_cart_redis": Remediation("Fail the cart Redis over to its replica"),
    "resync_gateway_clocks": Remediation(
        "Re-enable NTP time sync on api-gateway nodes",
        frozenset({Fault.AUTH_TOKEN_EXPIRED}),
    ),
    "raise_payment_gateway_timeout": Remediation(
        "Raise the payment gateway client timeout from 10 s to 30 s and retry charges with backoff",
        frozenset({Fault.PAYMENT_GATEWAY_TIMEOUT}),
    ),
    "enable_sorted_row_locking": Remediation(
        "Turn on the inventory-service flag that locks product rows in sorted ID order during reservations",
        frozenset({Fault.INVENTORY_DEADLOCK}),
    ),
    "restart_inventory_service": Remediation("Restart all inventory-service pods"),
    "renew_auth_certificate": Remediation(
        "Force cert-manager to reissue the auth-service TLS certificate",
        frozenset({Fault.CERT_EXPIRED}),
    ),
    "delete_old_log_indices": Remediation(
        "Delete log indices older than 14 days from the search Elasticsearch cluster",
        frozenset({Fault.SEARCH_DISK_FULL}),
    ),
    "scale_out_search": Remediation("Add one more search-service pod"),
    "rollback_promotions_config": Remediation(
        "Roll the promotions-service configuration back to its previous version",
        frozenset({Fault.PROMO_CONFIG_BROKEN}),
    ),
    "restart_promotions_service": Remediation("Restart all promotions-service pods"),
}
