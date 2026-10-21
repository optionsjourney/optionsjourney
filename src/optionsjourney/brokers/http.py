"""The one place broker REST requests are made and their failures translated."""

from decimal import Decimal

import httpx

from optionsjourney import __version__

TIMEOUT_S = 10
SLOW_TIMEOUT_S = 30  # long reads: Schwab's 180 days of orders take about 6 s


class BrokerError(Exception):
    """A broker refused or failed a request. The message is shown to the user.
    `detail` is the broker's reply when it has one worth showing (a refused preview's costs)."""

    def __init__(self, message: str = "", detail: dict | None = None):
        super().__init__(message)
        self.detail = detail


def new_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_S)


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": f"optionsjourney/{__version__}",
    }


def get_json(
    http: httpx.Client,
    url: str,
    token: str,
    broker: str,
    params: dict | None = None,
    timeout: float | None = None,
):
    try:
        response = http.get(
            url,
            params=params,
            headers=_headers(token),
            **({"timeout": timeout} if timeout else {}),
        )
    except httpx.HTTPError as e:
        raise BrokerError(f"Can't reach {broker}.") from e
    if response.status_code == 401:
        raise BrokerError(f"{broker} rejected the access token.")
    if response.status_code != 200:
        raise BrokerError(f"{broker} answered {response.status_code} for {response.url.path}.")
    try:
        return response.json()
    except ValueError as e:
        raise BrokerError(f"{broker} sent a reply that isn't JSON for {response.url.path}.") from e


class RequestUnclear(BrokerError):
    """A request that changes something (an order) may or may not have
    reached the broker: the connection failed or timed out after sending.
    Never retry one blindly; check the broker first."""


def _refusal_message(response: httpx.Response) -> str:
    """The broker's own words, when it gives them."""
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        details = [e.get("message", "") for e in error.get("errors", []) if isinstance(e, dict)]
        return "; ".join(m for m in [error.get("message", ""), *details] if m)
    if isinstance(body, dict) and body.get("message"):
        return str(body["message"])
    return ""


def send_json(
    http: httpx.Client, method: str, url: str, token: str, broker: str, body: dict | None = None
) -> httpx.Response:
    """A request that changes something (an order, a cancel, a replace).
    A refusal (4xx) raises BrokerError with the broker's reason. A failed
    connection, or a 5xx after the request was sent, raises RequestUnclear:
    it may have gone through, so it must never be retried blindly."""
    try:
        response = http.request(method, url, json=body, headers=_headers(token))
    except httpx.HTTPError as e:
        raise RequestUnclear(
            f"Lost the connection to {broker}; check whether it went through."
        ) from e
    if response.status_code == 401:
        raise BrokerError(f"{broker} rejected the access token.")
    if response.status_code >= 500:
        raise RequestUnclear(
            f"{broker} had a problem ({response.status_code}); check whether it went through."
        )
    if not 200 <= response.status_code < 300:
        reason = _refusal_message(response)
        raise BrokerError(
            f"{broker} refused it ({response.status_code}){': ' + reason if reason else '.'}"
        )
    return response


def post_json(http: httpx.Client, url: str, token: str, broker: str, body: dict):
    """POST a JSON body; the answer's JSON, or {} when there's none."""
    response = send_json(http, "POST", url, token, broker, body)
    return response.json() if response.content else {}


def decimal(value) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else Decimal("0")
