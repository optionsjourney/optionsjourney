#!/bin/bash
# SwiftBar menu item for the demo (demo.optionsjourney.com) on the prod Mac.
# `oj-demo install` copies it into SwiftBar's plugin folder; it refreshes every minute.
# Start, Stop, Restart and status run without a password (the sudoers rule install writes).
OJ=/usr/local/libexec/optionsjourney-demo/oj-demo
health=$(curl -s --max-time 3 http://127.0.0.1:8096/_seat/health || true)
field() { echo "$health" | sed -n "s/.*\"$1\": *\([0-9]*\).*/\1/p"; }
assigned=$(field assigned)
queued=$(field queued)
ready=$(field ready)
if [ -n "$assigned" ]; then echo "🎓 $assigned"; else echo "🎓⏸"; fi
echo "---"
if [ -n "$assigned" ]; then
    echo "Demo: on"
    echo "Seats in use: $assigned, ready: $ready, waiting: $queued"
else
    echo "Demo: stopped or not answering"
fi
sudo -n "$OJ" status 2>/dev/null | grep -E '^(live commit|update waiting|firewall)' | sed 's/|/¦/g'
echo "---"
echo "Start | bash=/usr/bin/sudo param1=-n param2=$OJ param3=start terminal=false refresh=true"
echo "Stop (stays stopped after a reboot) | bash=/usr/bin/sudo param1=-n param2=$OJ param3=stop terminal=false refresh=true"
echo "Restart (ends every visit) | bash=/usr/bin/sudo param1=-n param2=$OJ param3=restart terminal=false refresh=true"
echo "Open demo.optionsjourney.com | href=https://demo.optionsjourney.com"
echo "Visitor counts | href=https://demo.optionsjourney.com/stats"
echo "Show status | bash=/usr/bin/sudo param1=-n param2=$OJ param3=status terminal=true"
