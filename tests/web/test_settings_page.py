import pytest

from optionsjourney.app import create_app

ORIGIN = {"Origin": "http://localhost"}


@pytest.fixture
def client(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    return app.test_client()


def test_settings_is_in_the_nav(client):
    assert 'href="/settings"' in client.get("/journal").get_data(as_text=True)


def test_settings_page_explains_each_setting(client):
    html = client.get("/settings").get_data(as_text=True)
    assert "Backups" in html and "Backup folder" in html
    assert "iCloud Drive" in html  # the explanation
    assert 'value="daily" selected' in html


def test_saving_settings(client):
    response = client.post(
        "/settings/backups",
        data={"backup_folder": "/tmp/oj-test", "backup_frequency": "weekly"},
        headers=ORIGIN,
    )
    assert response.status_code == 302
    html = client.get("/settings").get_data(as_text=True)
    assert 'value="/tmp/oj-test"' in html and 'value="weekly" selected' in html


def test_a_problem_is_shown_and_what_was_typed_kept(client):
    response = client.post(
        "/settings/backups",
        data={"backup_folder": "Backups", "backup_frequency": "weekly"},
        headers=ORIGIN,
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "enter a full path" in html
    assert 'value="Backups"' in html


def test_unknown_section_is_404(client):
    assert client.post("/settings/nonsense", data={}, headers=ORIGIN).status_code == 404


def _use_folder(client, tmp_path, frequency="daily"):
    folder = tmp_path / "backups"
    client.post(
        "/settings/backups",
        data={"backup_folder": str(folder), "backup_frequency": frequency},
        headers=ORIGIN,
    )
    return folder


def test_back_up_now(client, tmp_path):
    folder = _use_folder(client, tmp_path)
    response = client.post("/settings/backups/now", headers=ORIGIN, follow_redirects=True)
    html = response.get_data(as_text=True)
    assert "Backed up" in html
    [made] = list(folder.iterdir())
    assert made.name in html  # listed on the page, with Restore


def test_restore(client, tmp_path):
    folder = _use_folder(client, tmp_path)
    client.post("/settings/backups/now", headers=ORIGIN)
    [made] = list(folder.iterdir())
    response = client.post(
        "/settings/backups/restore", data={"name": made.name}, headers=ORIGIN, follow_redirects=True
    )
    assert "Restored" in response.get_data(as_text=True)
    assert (
        client.get("/journal").status_code == 200
    )  # the app carries on with the restored database


def test_restore_refuses_other_files(client, tmp_path):
    _use_folder(client, tmp_path)
    response = client.post(
        "/settings/backups/restore", data={"name": "../x.db"}, headers=ORIGIN, follow_redirects=True
    )
    assert "isn&#39;t in the backup folder" in response.get_data(as_text=True)


def test_a_backup_problem_is_shown(client, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    client.post(
        "/settings/backups",
        data={"backup_folder": str(blocker / "in"), "backup_frequency": "daily"},
        headers=ORIGIN,
    )
    html = client.post("/settings/backups/now", headers=ORIGIN, follow_redirects=True).get_data(
        as_text=True
    )
    assert "can&#39;t be used" in html


def test_send_a_test_notification_says_when_nothing_is_set_up(client):
    html = client.post(
        "/settings/notifications/test", headers=ORIGIN, follow_redirects=True
    ).get_data(as_text=True)
    assert "Nothing to send to yet" in html
    assert "Discord webhook URL" in html and 'type="password"' in html


def test_settings_has_a_sidebar_of_sections(client):
    """Modern layout: a sidebar picks the section shown (#73)."""
    html = client.get("/settings").get_data(as_text=True)
    nav = html.split('aria-label="Settings sections"')[1].split("</nav>")[0]
    links = ["#brokers", "#backups", "#notifications", "#computer"]
    assert [nav.index(f'href="{link}"') for link in links] == sorted(
        nav.index(f'href="{link}"') for link in links
    )
    assert "Run setup again" in nav
    assert 'data-active="brokers"' in html


def test_each_setting_has_a_tooltip(client):
    html = client.get("/settings").get_data(as_text=True)
    assert 'aria-describedby="tip-backup_folder"' in html
    tip = html.split('id="tip-backup_folder"')[1].split("</span>")[0]
    assert 'role="tooltip"' in html and "iCloud Drive" in tip
    assert "js/tooltips.js" in html


def test_buttons_say_what_they_do(client):
    html = client.get("/settings").get_data(as_text=True)
    assert 'aria-describedby="tip-backup-now"' in html
    assert 'aria-describedby="tip-send-test"' in html


def test_a_problem_shows_its_section(client):
    html = client.post(
        "/settings/notifications", data={"telegram_chat_id": "abc"}, headers=ORIGIN
    ).get_data(as_text=True)
    assert 'data-active="notifications"' in html and "it&#39;s a number" in html


def test_send_a_test_is_beside_save(client):
    html = client.get("/settings").get_data(as_text=True)
    form = html.split('action="/settings/notifications"')[1].split("</form>")[0]
    assert 'formaction="/settings/notifications/test"' in form
