# 0129. The demo runs on the Linux prod box, fenced by nftables and systemd

- **Status:** Accepted
- **Date:** 2026-11-10

## Context
Decision 0128 moved the site to the Dell running Ubuntu and switched the demo off, because its fence (0103, 0126) was macOS-only: pf rules for the `ojdemo` user, a LaunchDaemon that turns pf on at boot, sudoers for the menu bar. The trading app's database on the Dell trusts every connection from the computer itself, so the demo must not start there without an equally strict fence.

## Decision
- **Same shape as on the Mac.** The `ojdemo` user (a system user, no login shell) runs the seat manager, Caddy on `127.0.0.1:8095` and the 5-minute update job from its own checkout of the public repo. `oj-demo` keeps its commands and its privilege rule: root runs only the root-owned copy in `/usr/local/libexec/optionsjourney-demo/` and treats what `ojdemo` writes as data.
- **systemd system units instead of LaunchDaemons:** `optionsjourney-demo` (the manager, `Nice=10`, restarted if it stops), `-proxy` (Caddy), `-update.timer` (every 5 minutes, idle I/O). Logs go to the journal (`oj-demo logs`). The stats password is in `/etc/optionsjourney-demo/manager.env`, root's and 600.
- **nftables instead of pf.** `optionsjourney-demo-firewall.service` loads the table `inet optionsjourney_demo` from `/etc/optionsjourney-demo/firewall.nft` at boot. It judges only connections `ojdemo`'s uid makes, with 0103's rules: on this computer only the demo's ports and DNS at the local resolver; nothing at this computer's other addresses or on private, Tailscale and link-local ranges; elsewhere only DNS, ssh and https. Refusals are TCP resets, so `firewall-check` still reads curl's exit 7 as "blocked".
- **Fail closed, per unit.** Every demo unit `Requires=` the firewall unit and runs `oj-demo firewall-ok` as root before starting (`ExecStartPre=+`), which fails unless the table is loaded with `ojdemo`'s uid in it. That covers boot (0126) and `start` alike; there is no separate boot job.
- **systemd sandboxing on top:** `ProtectHome=tmpfs` with only `ojdemo`'s home bound back, so other users' homes (the trading app's checkout, `.env`, tokens) aren't visible to the demo even if their permissions loosen; `ProtectSystem=strict`, `PrivateTmp`, `PrivateDevices`, `NoNewPrivileges`, and only Unix and IP sockets.
- **No sudoers rule or menu bar item on Linux.** The admin runs `sudo oj-demo …` over ssh.

## Why
- One fence per OS keeps 0103's rules word for word; systemd's sandboxing hides the trading app's files by construction, not only by permissions.
- `ExecStartPre` makes "no fence, no demo" part of every start, including restarts systemd does by itself.

## Consequences
- `uv` must be root-owned at `/usr/local/bin/uv`; `install` refuses otherwise. Caddy comes from Caddy's own apt repo: Ubuntu's package (2.6) is too old for the Caddyfile's `handle_errors 502 503`, which needs 2.8.
- The Mac's demo LaunchDaemons are left disabled there, not deleted, until the Mac is wiped.
