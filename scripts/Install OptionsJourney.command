#!/bin/bash
# Double-click to install OptionsJourney on a Mac. The first time, macOS may
# say it can't be opened: right-click this file, choose Open, then Open again.
bash "$(dirname "$0")/install-mac.sh"
echo
read -r -p "Press Return to close this window."
