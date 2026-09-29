#!/usr/bin/env bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo 'Run with sudo.'; exit 1; }
PREVIOUS=$(readlink -f /opt/rdf-node/previous || true)
[ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ] || { echo 'No previous release.'; exit 1; }
ln -sfn "$PREVIOUS" /opt/rdf-node/current.new
mv -Tf /opt/rdf-node/current.new /opt/rdf-node/current
systemctl try-restart rdf-edge.service rdf-control-helper.service rdf-ground.service
printf 'Rolled back application to %s. Config, journals, PPP and SDR were not reverted.\n' "$PREVIOUS"
