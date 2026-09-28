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
}
