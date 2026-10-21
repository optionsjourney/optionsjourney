from pathlib import Path

import pytest

from optionsjourney.core.settings import SECTIONS, SettingError, clean_section, spec


def test_every_setting_explains_itself_and_has_a_valid_default():
    for section in SECTIONS:
        assert section.title and section.help
        for setting in section.settings:
            assert setting.label and setting.help and setting.tip, setting.key
            assert len(setting.help) <= 60, f"{setting.key}: keep help to one short line"
            assert setting.clean(setting.default_text()) == setting.default, setting.key


def test_sections_are_in_settings_page_order():
    assert [s.key for s in SECTIONS] == [
        "backups",
        "notifications",
        "computer",
        "your_orders",
        "market_calendar",
        "charts",
        "sync",
    ]


def test_backup_frequency_is_a_choice():
    frequency = spec("backup_frequency")
    assert frequency.clean("weekly") == "weekly"
    with pytest.raises(SettingError, match="Backups"):
        frequency.clean("hourly")


def test_backup_folder_is_a_full_path():
    folder = spec("backup_folder")
    assert folder.clean("~/Backups") == str(Path.home() / "Backups")
    assert folder.clean("  /Volumes/USB/OJ  ") == "/Volumes/USB/OJ"
    with pytest.raises(SettingError, match="folder"):
        folder.clean("")
    with pytest.raises(SettingError, match="full path"):
        folder.clean("Backups")


def test_a_section_is_checked_as_a_whole():
    values = clean_section("backups", {"backup_folder": "~/B", "backup_frequency": "off"})
    assert values == {"backup_folder": str(Path.home() / "B"), "backup_frequency": "off"}
    with pytest.raises(SettingError) as refused:
        clean_section("backups", {"backup_folder": "", "backup_frequency": "hourly"})
    assert len(refused.value.problems) == 2  # every problem at once, not one at a time


def test_largest_order_you_can_place():
    setting = spec("max_order_contracts")
    assert setting.default == "10" and setting.clean(" 25 ") == "25"
    for bad in ("0", "1001", "2.5", "ten", ""):
        with pytest.raises(SettingError):
            setting.clean(bad)


def test_the_fred_key_is_optional_and_checked():
    from optionsjourney.core.settings import SettingError, spec

    key = spec("fred_api_key")
    assert key.secret and key.default == ""
    assert key.clean("") == ""
    assert key.clean(" " + "a1" * 16 + " ") == "a1" * 16
    with pytest.raises(SettingError):
        key.clean("not-a-key")


def test_the_finnhub_key_is_optional_and_checked():
    from optionsjourney.core.settings import SettingError, spec

    key = spec("finnhub_api_key")
    assert key.secret and key.default == ""
    assert key.clean("") == ""
    assert key.clean(" abc123def456ghi789jk ") == "abc123def456ghi789jk"
    with pytest.raises(SettingError):
        key.clean("not a key")
