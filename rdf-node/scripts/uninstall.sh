#!/usr/bin/env bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo 'Run with sudo.'; exit 1; }
echo 'This removes RDF Node service units, not PPP, SDR, data/config or credentials.'
echo 'Review any explicitly installed SDR lifecycle drop-in BEFORE removal.'
read -r -p 'Type UNINSTALL to continue: ' answer
[ "$answer" = UNINSTALL ] || exit 0
for unit in rdf-edge.service rdf-control-helper.service rdf-ground.service rdf-ground-mqtt.service; do
 systemctl disable --now "$unit" 2>/dev/null || true
 rm -f "/etc/systemd/system/$unit"
done
systemctl daemon-reload
echo 'Application releases and /etc/rdf-*/var/lib/rdf-* retained for recovery.'
echo 'Remove per-user rdf-kiosk.service + autostart entries manually if installed.'
echo 'PPP/SDR ownership is NOT silently changed. See docs/OPERATIONS.md for rollback.'
