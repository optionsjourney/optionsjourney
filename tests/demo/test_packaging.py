"""The demo's hosting files (packaging/demo/): Caddy's routing to the seats, the oj-demo
script and the menu bar item. The real run through Caddy is by hand (developer/demo.md)."""

import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "packaging" / "demo"
CADDYFILE = DEMO / "Caddyfile"
OJ_DEMO = DEMO / "oj-demo"
MENU = DEMO / "optionsjourney-demo.1m.sh"

PUBLISH = ROOT / "scripts" / "publish_public.py"  # private: not in the public copy


def test_each_seat_has_a_matcher_and_its_upstream():
    text = CADDYFILE.read_text()
    for n in range(1, 11):
        matcher = rf"@seat{n} header_regexp Cookie \(\^\|;\\s\*\)oj_seat={n}\\\."
        assert re.search(matcher, text), f"no matcher for seat {n}"
        assert re.search(rf"handle @seat{n} \{{\s*reverse_proxy 127\.0\.0\.1:{5100 + n}\b", text)


def test_seat_1_does_not_match_seat_10():
    pattern = r"(^|;\s*)oj_seat=1\."
    assert re.search(pattern, "oj_seat=1.abc")
    assert re.search(pattern, "a=b; oj_seat=1.abc")
    assert not re.search(pattern, "oj_seat=10.abc")
    assert not re.search(pattern, "xoj_seat=1.abc")


def test_caddy_listens_only_on_this_computer_and_says_https():
    text = CADDYFILE.read_text()
    assert "bind 127.0.0.1" in text
    assert "admin off" in text
    proxies = text.count("reverse_proxy ")
    assert proxies == 12  # ten seats, and the manager twice (its pages, and the fallback)
    assert text.count("header_up X-Forwarded-Proto https") == proxies


def test_the_manager_routes_come_before_the_seats():
    text = CADDYFILE.read_text()
    assert text.index("handle @manager") < text.index("handle @seat1 ")
    assert "respond /_seat/health 404" in text


def test_oj_demo_parses():
    for script in (OJ_DEMO, MENU):
        subprocess.run(["bash", "-n", str(script)], check=True)
        assert os.access(script, os.X_OK)


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_oj_demo_passes_shellcheck():
    subprocess.run(["shellcheck", str(OJ_DEMO), str(MENU)], check=True)


def test_the_dry_run_install_writes_three_daemons_as_the_demo_user():
    done = subprocess.run(
        [str(OJ_DEMO), "install"],
        env={**os.environ, "OJ_DEMO_DRY_RUN": "1"},
        capture_output=True,
        text=True,
        check=True,
    )
    out = done.stdout
    for label in ("com.optionsjourney.demo", "demo-proxy", "demo-update"):
        assert label in out
    assert out.count("<key>UserName</key><string>ojdemo</string>") == 3
    assert "<key>Nice</key><integer>10</integer>" in out
    assert "<key>ExitTimeOut</key><integer>30</integer>" in out  # the seats get 10 s of it
    assert "<key>StartInterval</key><integer>300</integer>" in out
    assert "visudo -c -f" in out


def test_the_hosting_files_hold_no_personal_paths():
    for path in (CADDYFILE, OJ_DEMO, MENU):
        assert "/Users/" not in path.read_text(), path.name


@pytest.mark.skipif(not PUBLISH.exists(), reason="the public copy has no publish script")
def test_the_hosting_files_are_public():
    spec = importlib.util.spec_from_file_location("publish_public", PUBLISH)
    publish = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publish)
    for path in (CADDYFILE, OJ_DEMO, MENU):
        assert publish.is_public(path.relative_to(ROOT).as_posix()), path.name


# ----- the privilege rule: root never runs, sources or reads what ojdemo can write -----

ROOT_RUN = (
    "status",
    "install_demo",
    "trusted_source",
    "only_theirs",
    "opens_up",
    "sync_app",
    "stats_password",
    "pf_rules",
    "resolvers",
    "probe_verdict",
    "pf_steps",
    "pf_on",
    "firewall_check",
    "up",
    "down",
    "loaded",
)


def _functions(text: str) -> dict[str, list[str]]:
    found, name = {}, None
    for line in text.splitlines():
        start = re.match(r"^(\w+)\(\) \{", line)
        if start:
            name = start.group(1)
            found[name] = []
        elif line == "}":
            name = None
        elif name:
            found[name].append(line)
    return found


def _root_lines() -> list[str]:
    text = OJ_DEMO.read_text()
    bodies = _functions(text)
    lines = [line for name in ROOT_RUN for line in bodies[name]]
    arms = text[text.index('case "${1:-status}" in') : text.rindex("esac")].splitlines()
    lines += [line for line in arms if not line.strip().startswith("update)")]
    return [line for line in lines if not line.strip().startswith(("#", "echo ", "say "))]


# The only root-run lines that name ojdemo's paths without running as ojdemo: values
# written into the plists, and the path comparison that refuses an install from there.
AS_DATA = (
    'local base_env=("HOME=$DEMO_HOME" ',
    '"$REPO/.venv/bin/python" -m optionsjourney.demo.manager ',
    '"$caddy" run --adapter caddyfile --config "$REPO/packaging/demo/Caddyfile" |',
    'home=$( (cd -P "$DEMO_HOME" 2>/dev/null && pwd -P) || echo "$DEMO_HOME")',
    '"$home"/* | "$DEMO_HOME"/*)',
)


def test_root_never_runs_git_or_reads_the_demo_users_files():
    assert "safe.directory" not in OJ_DEMO.read_text()
    for line in _root_lines():
        line = line.strip()
        if re.search(r"\bgit\b|\$REPO|\$STATE|\$\{?DEMO_HOME", line) and "as_demo" not in line:
            assert line.startswith(AS_DATA), line


def test_root_runs_with_system_folders_only_on_its_path():
    text = OJ_DEMO.read_text()
    root_path = re.search(r'if \[ "\$\(id -u\)" -eq 0 \]; then\n\s+export PATH="([^"]+)"', text)
    assert root_path is not None
    assert root_path.group(1) == "/usr/bin:/bin:/usr/sbin:/sbin"


def test_install_refuses_a_copy_from_the_demo_users_home(tmp_path):
    home = tmp_path / "ojdemo"
    folder = home / "optionsjourney" / "packaging" / "demo"
    folder.mkdir(parents=True)
    for source in (OJ_DEMO, MENU):
        shutil.copy2(source, folder / source.name)
    done = subprocess.run(
        [str(folder / "oj-demo"), "install"],
        env={**os.environ, "OJ_DEMO_DRY_RUN": "1", "OJ_DEMO_HOME": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1
    assert "Refusing to install" in done.stdout
    assert "would write" not in done.stdout


def test_the_dry_run_keeps_logs_in_roots_folder_and_the_updater_low():
    done = subprocess.run(
        [str(OJ_DEMO), "install"],
        env={**os.environ, "OJ_DEMO_DRY_RUN": "1"},
        capture_output=True,
        text=True,
        check=True,
    )
    out = done.stdout
    assert out.count("<string>/Library/Logs/optionsjourney-demo/com.optionsjourney.demo") == 6
    assert "<key>LowPriorityIO</key><true/>" in out
    assert out.count("<key>Nice</key><integer>10</integer>") == 2
    assert "would look up SwiftBar" in out  # not looked up for real in a dry run


# The only root-run lines that name the logs without running as ojdemo: making the
# folder and replacing a missing or linked log file. Reading one is as ojdemo.
LOGS_SETUP = (
    'run /usr/bin/install -d -m 755 -o root -g wheel "$LOGS"',
    'if [ -n "$DRY" ] || [ -L "$LOGS/$label.log" ] || [ ! -f "$LOGS/$label.log" ]; then',
    'run /bin/rm -f "$LOGS/$label.log"',
    'run /usr/bin/install -m 644 -o "$DEMO_USER" -g "$group" /dev/null "$LOGS/$label.log"',
)


def test_root_reads_the_logs_only_as_ojdemo():
    for line in _root_lines():
        line = line.strip()
        if "$LOGS" in line and "as_demo" not in line:
            assert line.startswith(LOGS_SETUP), line
    logs = next(line for line in _root_lines() if "$LOGS/$MANAGER.log" in line)
    assert logs.strip().startswith("as_demo /usr/bin/tail ")


def _dry(*args, env=None, cwd_script=OJ_DEMO):
    return subprocess.run(
        [str(cwd_script), *args],
        env={**os.environ, "OJ_DEMO_DRY_RUN": "1", **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


# ----- what ojdemo runs is data: no input, cleaned output -----


def test_the_demo_user_gets_no_input_and_its_output_is_cleaned():
    bodies = _functions(OJ_DEMO.read_text())
    assert bodies["as_demo"] == ['    /usr/bin/sudo -u "$DEMO_USER" -H "$@" </dev/null']
    sync = "\n".join(bodies["sync_app"])
    assert "out=$(as_demo /bin/sh -c" in sync and '$(printf %s "$out" | clean 600)' in sync
    assert "uv sync" not in "\n".join(bodies["install_demo"])  # only through sync_app
    text = OJ_DEMO.read_text()
    tail = text[text.index("as_demo /usr/bin/tail") :].splitlines()[1]
    assert tail.strip() == "clean_lines 300 ;;"
    assert "clean_lines() { LC_ALL=C /usr/bin/tr -cd '[:print:]\\n' |" in text


def test_the_dry_run_syncs_the_app_without_running_it():
    out = _dry("install").stdout
    assert "--- would run as ojdemo, in its checkout: " in out
    assert "sync -q --frozen --no-dev" in out


# ----- the root copy lives where only root can change it -----

LIBEXEC = "/usr/local/libexec/optionsjourney-demo/oj-demo"


def test_the_root_copy_is_in_a_root_only_folder():
    out = _dry("install").stdout
    assert "/usr/local/bin/oj-demo" not in out
    assert f"NOPASSWD: {LIBEXEC} start, {LIBEXEC} stop, {LIBEXEC} restart, {LIBEXEC} status" in out
    assert f"<string>{LIBEXEC}</string><string>update</string>" in out
    assert f"OJ={LIBEXEC}" in MENU.read_text()


def test_install_refuses_a_root_copy_folder_others_can_change(tmp_path):
    folder = tmp_path / "libexec"  # belongs to whoever runs the test, not root
    folder.mkdir()
    done = _dry("install", env={"OJ_DEMO_LIBEXEC": str(folder)})
    assert done.returncode == 1
    assert "Refusing to install" in done.stdout and "not root" in done.stdout
    assert "would write" not in done.stdout


def _clone(tmp_path):
    """A copy of the hosting files in a folder of the admin's (the one running the test)."""
    folder = tmp_path / "clone" / "packaging" / "demo"
    folder.mkdir(parents=True)
    for source in (OJ_DEMO, MENU):
        shutil.copy2(source, folder / source.name)
    return folder


def _from(folder, home):
    return _dry("install", cwd_script=folder / "oj-demo", env={"OJ_DEMO_HOME": str(home)})


def test_install_runs_from_a_clean_clone(tmp_path):
    folder = _clone(tmp_path)
    done = _from(folder, tmp_path / "oj")
    assert done.returncode == 0, done.stdout
    assert "would write" in done.stdout


def test_install_refuses_a_folder_others_can_write(tmp_path):
    folder = _clone(tmp_path)
    (tmp_path / "clone").chmod(0o775)  # group-writable, above the files
    done = _from(folder, tmp_path / "oj")
    assert done.returncode == 1
    assert f"others can write {(tmp_path / 'clone').resolve()}" in done.stdout
    assert "would write" not in done.stdout


def test_install_refuses_a_folder_with_an_allowing_acl(tmp_path):
    folder = _clone(tmp_path)
    acl = subprocess.run(
        ["/bin/chmod", "+a", "everyone allow add_file", str(tmp_path / "clone")], check=False
    )
    if acl.returncode != 0:
        pytest.skip("no ACLs here")
    done = _from(folder, tmp_path / "oj")
    assert done.returncode == 1
    assert "access list (ACL)" in done.stdout


def test_a_deny_acl_like_a_home_folders_is_fine(tmp_path):
    folder = _clone(tmp_path)
    acl = subprocess.run(
        ["/bin/chmod", "+a", "group:everyone deny delete", str(tmp_path / "clone")], check=False
    )
    if acl.returncode != 0:
        pytest.skip("no ACLs here")
    done = _from(folder, tmp_path / "oj")
    assert done.returncode == 0, done.stdout


def test_install_refuses_a_menu_item_linked_into_the_demo_users_files(tmp_path):
    folder = _clone(tmp_path)
    home = tmp_path / "ojdemo"
    home.mkdir()
    planted = home / "menu.sh"
    planted.write_text("#!/bin/bash\necho planted\n")
    (folder / MENU.name).unlink()
    (folder / MENU.name).symlink_to(planted)
    done = _dry("install", cwd_script=folder / "oj-demo", env={"OJ_DEMO_HOME": str(home)})
    assert done.returncode == 1
    assert "Refusing to install from ojdemo's files" in done.stdout
    assert "would write" not in done.stdout


# ----- the demo user's firewall (decision 0103) -----

ANCHOR = "/etc/pf.anchors/optionsjourney-demo"


def _anchor(out: str) -> str:
    start = out.index(f"--- would write {ANCHOR} (644, root:wheel)\n")
    body = out[start:].split("\n", 1)[1]
    return body[: body.index("--- ")]


def test_install_writes_the_firewall_rules_and_only_says_how_to_turn_them_on():
    out = _dry("install").stdout
    rules = _anchor(out)
    assert "port { 8095, 8096, 5101:5110 } user ojdemo" in rules
    assert "to { 127.0.0.0/8, ::1 } flags S/SA user ojdemo" in rules  # the rest of loopback
    assert "port 53 user ojdemo" in rules and "port { 22, 443 } user ojdemo" in rules
    assert rules.rstrip().endswith("block return out quick proto udp from any to any user ojdemo")
    assert 'load anchor "optionsjourney-demo" from "/etc/pf.anchors/optionsjourney-demo"' in out
    assert "firewall-check <port>" in out
    # install itself never turns pf on or edits pf.conf
    assert "would run: /sbin/pfctl" not in out and "would write /etc/pf.conf" not in out
    body = "\n".join(_functions(OJ_DEMO.read_text())["install_demo"])
    assert "pfctl" not in body and "pf.conf" not in body


@pytest.mark.skipif(not Path("/sbin/pfctl").exists(), reason="no pf here")
def test_the_firewall_rules_parse(tmp_path):
    """pfctl -n only parses; the rules name ojdemo, which may not exist here."""
    import getpass  # noqa: PLC0415

    rules = _anchor(_dry("install").stdout).replace("user ojdemo", f"user {getpass.getuser()}")
    path = tmp_path / "anchor"
    path.write_text(rules)
    done = subprocess.run(
        ["/sbin/pfctl", "-n", "-f", str(path)], capture_output=True, text=True, check=False
    )
    assert "syntax error" not in done.stderr, done.stderr
    assert done.returncode == 0, done.stderr


def test_firewall_check_is_a_command_but_not_a_passwordless_one():
    text = OJ_DEMO.read_text()
    assert "    firewall-check) as_root firewall-check" in text
    out = _dry("install").stdout
    sudoers = next(line for line in out.splitlines() if "NOPASSWD" in line)
    assert "firewall-check" not in sudoers
    check = "\n".join(_functions(text)["firewall_check"])
    # ojdemo's curl reads no ~/.curlrc and uses no proxy; root never connects itself
    assert check.count("as_demo /usr/bin/curl -q ") == 2
    assert check.count("--noproxy '*'") == 2
    assert "/usr/sbin/lsof -nP -t -iTCP" in check
    assert "_seat/health" in check


def _verdict(code: int) -> subprocess.CompletedProcess:
    body = "\n".join(_functions(OJ_DEMO.read_text())["probe_verdict"])
    call = f"probe_verdict 127.0.0.1:2026 {code}"
    script = f"DEMO_USER=ojdemo\nprobe_verdict() {{\n{body}\n}}\n{call}"
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)


def test_firewall_check_calls_only_a_refused_connection_blocked():
    """pf's `block return` makes curl fail to connect (exit 7). Any other failure (an empty
    reply 52, a reset 56, a timeout 28) means it may have connected: not blocked."""
    blocked = _verdict(7)
    assert blocked.returncode == 0 and "blocked, as it should be" in blocked.stdout
    for code in (0, 28, 52, 56):
        done = _verdict(code)
        assert done.returncode != 0, code
        assert "as it should be" not in done.stdout
        assert ("REACHED" if code == 0 else "NOT blocked, or unclear") in done.stdout
    check = "\n".join(_functions(OJ_DEMO.read_text())["firewall_check"])
    assert 'probe_verdict "127.0.0.1:$port" "$code" || failed=1' in check
    assert "if as_demo /usr/bin/curl" not in check


PRIVATE = (
    "self",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "fc00::/7",
    "fe80::/10",
)


def test_the_firewall_keeps_the_demo_user_off_the_home_network():
    """Before DNS, ssh and https pass anywhere: nothing private, nor this computer's own
    addresses, except DNS at the resolvers it had at install (a home router, often)."""
    env = {"OJ_DEMO_RESOLVERS": "192.0.2.53 fe80::1%en0 1.1.1.1 not;an-address"}
    lines = _anchor(_dry("install", env=env).stdout).splitlines()

    def first(fragment: str) -> int:
        return next(i for i, line in enumerate(lines) if fragment in line)

    dns = first("port 53 user ojdemo")
    assert lines[dns] == (
        "pass out quick proto { tcp, udp } from any to { 1.1.1.1, 192.0.2.53 } port 53 user ojdemo"
    )
    blocks = [i for i, line in enumerate(lines) if line.startswith("block") and "self," in line]
    assert len(blocks) == 2  # tcp and udp
    for i in blocks:
        assert all(f"{net}," in lines[i] or f"{net} }}" in lines[i] for net in PRIVATE)
    assert dns < min(blocks)
    assert max(blocks) < first("from any to any port 53 user ojdemo")
    assert max(blocks) < first("port { 22, 443 } user ojdemo")
    # No resolvers found: no DNS exception, the blocks still come first
    lines = _anchor(_dry("install", env={"OJ_DEMO_RESOLVERS": ""}).stdout).splitlines()
    assert not any("port 53" in line and "any to {" in line for line in lines)
    assert first("self,") < first("from any to any port 53")
