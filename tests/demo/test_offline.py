import subprocess
import sys
import textwrap

# Run in a subprocess so the (never undone) patch cannot leak into other tests.
SCRIPT = textwrap.dedent(
    """
    import asyncio
    import httpx
    import websockets.asyncio.client
    import websockets.sync.client
    from optionsjourney.demo import offline

    offline.install()
    offline.install()  # idempotent

    def blocked(fn):
        try:
            fn()
        except offline.DemoOffline as e:
            assert "example.com" in str(e) or "tastyworks" in str(e)
            return
        raise SystemExit("not blocked")

    blocked(lambda: httpx.get("https://example.com"))
    blocked(lambda: websockets.sync.client.connect("wss://streamer.tastyworks.com"))
    blocked(lambda: websockets.asyncio.client.connect("wss://streamer.tastyworks.com"))

    async def go():
        async with httpx.AsyncClient() as c:
            await c.get("https://example.com")

    try:
        asyncio.run(go())
    except offline.DemoOffline:
        pass
    else:
        raise SystemExit("async not blocked")
    print("ok")
    """
)


def test_offline_blocks_http_and_websockets():
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok"


LOG_SCRIPT = textwrap.dedent(
    """
    import asyncio
    import logging
    import httpx
    import websockets.sync.client
    from optionsjourney.demo import offline

    records = []

    class Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    logging.getLogger("optionsjourney.demo.offline").addHandler(Keep())
    offline.install()

    def swallow(fn):
        try:
            fn()
        except OSError:  # what a route's own except clause would do
            pass

    swallow(lambda: httpx.get("https://sync.example.com/a"))
    swallow(lambda: websockets.sync.client.connect("wss://ws.example.com/b"))

    async def go():
        async with httpx.AsyncClient() as c:
            await c.get("https://async.example.com/c")

    try:
        asyncio.run(go())
    except OSError:
        pass
    text = " ".join(r.getMessage() for r in records if r.levelno == logging.WARNING)
    for url in ("sync.example.com/a", "ws.example.com/b", "async.example.com/c"):
        assert url in text, (url, text)
    print("ok")
    """
)


def test_a_blocked_attempt_is_logged_even_when_the_caller_swallows_it():
    result = subprocess.run(
        [sys.executable, "-c", LOG_SCRIPT], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok"
