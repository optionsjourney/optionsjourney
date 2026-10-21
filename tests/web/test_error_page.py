"""The page for an unexpected error (decision 0094)."""

from optionsjourney.app import create_app


def _broken_app(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.get("/boom")
    def boom():
        raise ValueError("Invalid format string")

    @app.get("/api/boom")
    def api_boom():
        raise ValueError("Invalid format string")

    return app


def test_an_error_says_what_to_do(settings, fake_credentials):
    client = _broken_app(settings, fake_credentials).test_client()
    r = client.get("/boom")
    html = r.get_data(as_text=True)
    assert r.status_code == 500
    assert "Something went wrong" in html and "Restart OptionsJourney" in html
    assert "Open logs" in html and "ValueError on /boom" in html


def test_an_api_error_is_json(settings, fake_credentials):
    r = _broken_app(settings, fake_credentials).test_client().get("/api/boom")
    assert r.status_code == 500 and "Restart" in r.get_json()["error"]
