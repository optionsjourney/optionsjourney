"""Code that only works on a Mac: caught here, since the tests run on one."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "optionsjourney"
# strftime's %-d, %-I, ... (no leading zero) are glibc/BSD only; Windows raises ValueError.
NO_PAD = re.compile(r"""strftime\(\s*['"][^'"]*%-[a-zA-Z]""")


def test_no_unpadded_strftime_codes():
    found = [
        f"{path.relative_to(SRC)}:{n}"
        for path in SRC.rglob("*")
        if path.suffix in {".py", ".html"}
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if NO_PAD.search(line)
    ]
    assert not found, f"%-x in strftime fails on Windows: {found}"
