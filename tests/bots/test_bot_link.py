import pytest

from optionsjourney.bots import bot_link
from optionsjourney.bots.bot_file import FileError


@pytest.mark.parametrize("url, direct", [
    # a link straight to the file is fetched as it is
    ("https://example.com/bots/spx-ic.json", "https://example.com/bots/spx-ic.json"),
    ("  https://example.com/a.json  ", "https://example.com/a.json"),
    # Google Drive's share page and its older open link: the file itself
    ("https://drive.google.com/file/d/1AbC_d-9/view?usp=sharing",
     "https://drive.google.com/uc?export=download&id=1AbC_d-9"),
    ("https://drive.google.com/open?id=1AbC_d-9",
     "https://drive.google.com/uc?export=download&id=1AbC_d-9"),
    # Dropbox: dl=1 downloads instead of showing the page; its other keys stay
    ("https://www.dropbox.com/scl/fi/xyz/spx-ic.json?rlkey=k1&dl=0",
     "https://www.dropbox.com/scl/fi/xyz/spx-ic.json?rlkey=k1&dl=1"),
    ("https://www.dropbox.com/s/xyz/spx-ic.json",
     "https://www.dropbox.com/s/xyz/spx-ic.json?dl=1"),
    # GitHub: a file's page is its raw file; a gist's page its raw file
    ("https://github.com/someone/bots/blob/main/spx/ic.json",
     "https://raw.githubusercontent.com/someone/bots/main/spx/ic.json"),
    ("https://gist.github.com/someone/0123abcd", "https://gist.github.com/someone/0123abcd/raw"),
    ("https://gist.github.com/someone/0123abcd/raw/ic.json",
     "https://gist.github.com/someone/0123abcd/raw/ic.json"),
    # A Tracker2 share page is its .json (share_import.normalize_url)
    ("https://share.example.com/share/bot/Ab_c-9",
     "https://share.example.com/share/bot/Ab_c-9.json"),
    ("https://share.example.com/share/bot/Ab_c-9/?x=1",
     "https://share.example.com/share/bot/Ab_c-9.json"),
    ("https://share.example.com/share/bot/Ab_c-9.json",
     "https://share.example.com/share/bot/Ab_c-9.json"),
])  # fmt: skip
def test_a_share_page_link_becomes_the_files_own_link(url, direct):
    assert bot_link.direct(url) == direct


@pytest.mark.parametrize("url, words", [
    ("", "Paste a link"),
    ("example.com/a.json", "https://"),
    ("http://example.com/a.json", "https://"),
    ("ftp://example.com/a.json", "https://"),
    ("https://", "https://"),
    ("https://me:secret@example.com/a.json", "name or password"),
    ("https://localhost:5000/automation/7/export", "this computer"),
    ("https://app.localhost/a.json", "this computer"),
    ("https://127.0.0.1/a.json", "this computer"),
    ("https://[::1]/a.json", "this computer"),
    ("https://192.168.1.20/a.json", "this computer"),
    ("https://10.0.0.5/a.json", "this computer"),
    ("https://printer.local/a.json", "this computer"),
    ("https://example.com/" + "a" * 2000, "too long"),
])  # fmt: skip
def test_links_that_cant_be_fetched_are_refused_with_the_reason(url, words):
    with pytest.raises(FileError, match=words):
        bot_link.direct(url)


def test_a_link_to_this_apps_own_export_says_to_download_the_file():
    with pytest.raises(FileError, match="Download bot file"):
        bot_link.direct("http://localhost:5050/automation/7/export")


@pytest.mark.parametrize("address, public", [
    ("93.184.215.14", True), ("2606:2800:21f:cb07:6820:80da:af6b:8b2c", True),
    ("127.0.0.1", False), ("10.1.2.3", False), ("172.16.0.1", False), ("192.168.0.1", False),
    ("169.254.169.254", False), ("100.64.0.1", False), ("::1", False), ("fd00::1", False),
    ("fe80::1", False), ("0.0.0.0", False), ("::ffff:192.168.0.1", False),
])  # fmt: skip
def test_only_internet_addresses_are_public(address, public):
    assert bot_link.is_public(address) is public
