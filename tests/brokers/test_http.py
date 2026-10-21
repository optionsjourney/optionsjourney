import httpx
import pytest

from optionsjourney.brokers.http import BrokerError, RequestUnclear, get_json, send_json


def http(status, **kw):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, **kw)))


def test_a_5xx_on_a_write_is_unclear():
    with pytest.raises(RequestUnclear):
        send_json(http(502), "POST", "https://x.test/orders", "t", "TastyTrade", {})


def test_a_4xx_is_a_clean_refusal_and_a_dropped_connection_is_unclear():
    with pytest.raises(BrokerError) as refused:
        send_json(
            http(422, json={"message": "bad price"}), "PUT", "https://x.test/o/1", "t", "X", {}
        )
    assert not isinstance(refused.value, RequestUnclear) and "bad price" in str(refused.value)

    def drop(request):
        raise httpx.ReadTimeout("slow", request=request)

    client = httpx.Client(transport=httpx.MockTransport(drop))
    with pytest.raises(RequestUnclear):
        send_json(client, "DELETE", "https://x.test/o/1", "t", "X")


def test_a_reply_that_isnt_json_is_a_broker_error():
    client = http(200, text="<html>Down for maintenance</html>")
    with pytest.raises(BrokerError, match="isn't JSON"):
        get_json(client, "https://x.test/accounts", "t", "Schwab")
