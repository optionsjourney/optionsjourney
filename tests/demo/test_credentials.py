from datetime import UTC, datetime, timedelta

from optionsjourney.demo.credentials import DemoCredentials


def test_demo_credentials_never_expire_soon():
    creds = DemoCredentials()
    for t in (creds.tastytrade_token(), creds.schwab_token()):
        assert t.value == "demo" and t.is_test
        assert t.expires_at > datetime.now(UTC) + timedelta(days=300)


def test_demo_mode_never_gets_real_credentials(demo_settings):
    """Final review M-1: the one place that picks a provider gives demo mode the pretend
    one, whoever asks (never the keychain or the gateway)."""
    from optionsjourney.brokers.credentials import provider_for

    assert isinstance(provider_for(demo_settings), DemoCredentials)
