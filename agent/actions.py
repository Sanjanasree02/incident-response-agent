"""The agent's tools: ShopFast runbook actions and health checks.

The agent can only list allow-listed actions, run one of them, and probe the shop's public endpoints.
It has no access to /admin/faults, so it cannot fix an incident by switching the fault off directly.
"""

import re

import httpx

from agent.config import Settings
from agent.models import RemediationAction, ShopFastAlert

# Endpoints probed to verify the shop is healthy: method, path, JSON body.
HEALTH_CHECKS: list[tuple[str, str, dict | None]] = [
    ("GET", "/products", None),
    ("POST", "/login", {"username": "healthcheck", "password": "healthcheck"}),
    ("POST", "/cart/items", {"product_id": "sku-100", "quantity": 1}),
    ("POST", "/checkout", {"items": [{"product_id": "sku-100", "quantity": 1}]}),
]
_ACTION_NAME = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class ShopFastError(RuntimeError):
    """Raised when ShopFast cannot be reached or refuses an action."""


class ShopFastClient:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    @classmethod
    def from_settings(cls, settings: Settings) -> "ShopFastClient":
        headers = {"X-Admin-Token": settings.shopfast_admin_token} if settings.shopfast_admin_token else None
        return cls(httpx.Client(base_url=settings.shopfast_url, headers=headers, timeout=10.0))

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            return self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ShopFastError(f"ShopFast {method} {path} failed: {exc}") from exc

    def list_actions(self) -> list[RemediationAction]:
        response = self._request("GET", "/ops/actions")
        if response.status_code != 200:
            raise ShopFastError(f"ShopFast listed no actions: HTTP {response.status_code}")
        return [RemediationAction.model_validate(item) for item in response.json()]

    def run_action(self, name: str) -> None:
        """Run one allow-listed action. The name is checked against ShopFast's current allow-list first."""
        if not _ACTION_NAME.fullmatch(name) or name not in {a.name for a in self.list_actions()}:
            raise ShopFastError(f"Action {name!r} is not an allow-listed runbook action")
        response = self._request("POST", f"/ops/actions/{name}")
        if response.status_code != 200:
            raise ShopFastError(f"ShopFast refused action {name}: HTTP {response.status_code}")

    def latest_incident(self) -> ShopFastAlert | None:
        """The newest failure ShopFast recorded, or None if nothing has failed."""
        response = self._request("GET", "/ops/incidents/latest")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ShopFastError(f"ShopFast incident feed failed: HTTP {response.status_code}")
        return ShopFastAlert.model_validate(response.json())

    def health_check(self) -> dict[str, int]:
        """HTTP status of each probed endpoint, keyed like 'POST /checkout'."""
        return {
            f"{method} {path}": self._request(method, path, json=body).status_code
            for method, path, body in HEALTH_CHECKS
        }

    def close(self) -> None:
        self._http.close()
