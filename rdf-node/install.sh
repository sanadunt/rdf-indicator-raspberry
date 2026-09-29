#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROLE=edge
KIOSK_USER=""
SETUP=1
for arg in "$@"; do
  case "$arg" in
    --ground) ROLE=ground; SETUP=0;;
    --no-setup) SETUP=0;;
    --kiosk=*) KIOSK_USER=${arg#*=};;
    --help) echo 'sudo bash install.sh [--no-setup] [--kiosk=rdf] [--ground]'; exit 0;;
    *) echo "Unknown argument: $arg" >&2; exit 1;;
  esac
done
if [ "$(id -u)" -ne 0 ]; then echo 'Run this installer with sudo.' >&2; exit 1; fi
[ -d /run/systemd/system ] || { echo 'Install on a booted systemd host (not a plain container).'; exit 1; }
command -v systemctl >/dev/null || { echo 'A systemd Linux host is required.' >&2; exit 1; }
/usr/bin/python3 -c 'import sys,ssl,sqlite3; assert sys.version_info >= (3,10), "Python 3.10+ required"'
if [ -f "$ROOT/SHA256SUMS" ]; then (cd "$ROOT" && sha256sum --quiet -c SHA256SUMS); fi
printf '\nRDF Node install (%s). No PPP, udev, Conda, display-driver, or SDR service rewrites.\n' "$ROLE"
VERSION=$(/usr/bin/python3 -c 'import sys;sys.path.insert(0,sys.argv[1]);from rdf_node import __version__;print(__version__)' "$ROOT/src")
RELEASE="/opt/rdf-node/releases/${VERSION}-$(date +%Y%m%d%H%M%S)-$$"
mkdir -p "$RELEASE"
cp -a "$ROOT"/. "$RELEASE"/
chown -R root:root "$RELEASE"
chmod -R go-w "$RELEASE"
find "$RELEASE" -type d -name __pycache__ -prune -exec rm -rf {} +
mkdir -p /opt/rdf-node
PREVIOUS=$(readlink -f /opt/rdf-node/current 2>/dev/null || true)
if [ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ]; then ln -sfn "$PREVIOUS" /opt/rdf-node/previous; fi
ln -sfn "$RELEASE" /opt/rdf-node/current.new
mv -Tf /opt/rdf-node/current.new /opt/rdf-node/current
cat > /usr/local/bin/rdf-node <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /opt/rdf-node/current/run.py "$@"
EOF
chmod 0755 /usr/local/bin/rdf-node
if [ "$ROLE" = edge ]; then
  ACCOUNT=rdf-edge; CONFIG_DIR=/etc/rdf-node; STATE=/var/lib/rdf-node
else
  ACCOUNT=rdf-ground; CONFIG_DIR=/etc/rdf-ground; STATE=/var/lib/rdf-ground
fi
getent passwd "$ACCOUNT" >/dev/null || useradd --system --user-group --home-dir "$STATE" --shell /usr/sbin/nologin "$ACCOUNT"
install -d -m 0750 -o root -g "$ACCOUNT" "$CONFIG_DIR"
install -d -m 0750 -o "$ACCOUNT" -g "$ACCOUNT" "$STATE"
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
  /usr/bin/python3 - "$ROLE" "$CONFIG_DIR" "$STATE" <<'PY'
import sys,pathlib
sys.path[:0]=['/opt/rdf-node/current/src','/opt/rdf-node/current/vendor']
from rdf_node.config import load_config,save_config
c=load_config(); c['state_dir']=sys.argv[3]
c['api']['admin_hash_file']=sys.argv[2]+'/admin-hash.json'
if sys.argv[1]=='ground': c['api']['port']=8791
save_config(pathlib.Path(sys.argv[2])/'config.yaml',c)
PY
  chown root:"$ACCOUNT" "$CONFIG_DIR/config.yaml"; chmod 0640 "$CONFIG_DIR/config.yaml"
fi
if [ ! -f "$CONFIG_DIR/admin-hash.json" ]; then
 /usr/bin/python3 - "$CONFIG_DIR" <<'PY'
import sys,secrets,pathlib
sys.path[:0]=['/opt/rdf-node/current/src','/opt/rdf-node/current/vendor']
from rdf_node.api import set_password
from rdf_node.util import atomic_write
p=pathlib.Path(sys.argv[1]); password=secrets.token_urlsafe(20)
set_password(p/'admin-hash.json',password)
atomic_write(p/'initial-admin-password.txt',(password+'\n').encode(),0o600)
PY
 chown root:"$ACCOUNT" "$CONFIG_DIR/admin-hash.json"; chmod 0640 "$CONFIG_DIR/admin-hash.json"
fi
if [ "$ROLE" = edge ]; then
 if [ ! -f /etc/rdf-node/helper.yaml ]; then
  /usr/bin/python3 - <<'PY'
import sys,pathlib
sys.path[:0]=['/opt/rdf-node/current/src','/opt/rdf-node/current/vendor']
import yaml
from rdf_node.helper import DEFAULT_POLICY
from rdf_node.util import atomic_write
atomic_write(pathlib.Path('/etc/rdf-node/helper.yaml'),yaml.safe_dump(DEFAULT_POLICY,sort_keys=False).encode(),0o600)
PY
 fi
 install -m 0644 "$RELEASE/deploy/systemd/rdf-edge.service" /etc/systemd/system/
 install -m 0644 "$RELEASE/deploy/systemd/rdf-control-helper.service" /etc/systemd/system/
 systemctl daemon-reload
 systemctl enable rdf-edge.service rdf-control-helper.service
 systemctl restart rdf-control-helper.service rdf-edge.service
 if [ "$SETUP" = 1 ]; then
   if [ -t 0 ]; then
     rdf-node setup
     systemctl restart rdf-edge.service
   else
     echo 'No interactive terminal. Run: sudo rdf-node setup'
   fi
 fi
else
 install -m 0644 "$RELEASE/deploy/systemd/rdf-ground.service" /etc/systemd/system/
 systemctl daemon-reload
 echo 'Ground code installed. Next: sudo bash scripts/provision-ground.sh'
fi
if [ -n "$KIOSK_USER" ]; then
 getent passwd "$KIOSK_USER" >/dev/null || { echo 'Desktop user not found.' >&2; exit 1; }
 [ "$(id -u "$KIOSK_USER")" -ne 0 ] || { echo 'Root cannot be the kiosk user.' >&2; exit 1; }
 HOME_DIR=$(getent passwd "$KIOSK_USER" | cut -d: -f6)
 install -m 0755 "$RELEASE/deploy/kiosk/rdf-kiosk" /usr/local/bin/rdf-kiosk
 install -m 0755 "$RELEASE/deploy/kiosk/rdf-kiosk-session" /usr/local/bin/rdf-kiosk-session
 install -d -m 0755 -o "$KIOSK_USER" -g "$(id -gn "$KIOSK_USER")" "$HOME_DIR/.config/systemd/user" "$HOME_DIR/.config/autostart"
 install -m 0644 -o "$KIOSK_USER" -g "$(id -gn "$KIOSK_USER")" "$RELEASE/deploy/kiosk/rdf-kiosk.service" "$HOME_DIR/.config/systemd/user/"
 cat > "$HOME_DIR/.config/autostart/rdf-node.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=RDF Node Kiosk
Exec=/usr/local/bin/rdf-kiosk-session
Terminal=false
EOF
 chown "$KIOSK_USER:$(id -gn "$KIOSK_USER")" "$HOME_DIR/.config/autostart/rdf-node.desktop"
 if [ -d "$HOME_DIR/.config/labwc" ]; then
   touch "$HOME_DIR/.config/labwc/autostart"
   if ! grep -q '^# RDF_NODE_KIOSK' "$HOME_DIR/.config/labwc/autostart"; then
     printf '\n# RDF_NODE_KIOSK\n/usr/local/bin/rdf-kiosk-session &\n' >> "$HOME_DIR/.config/labwc/autostart"
   fi
   chown "$KIOSK_USER:$(id -gn "$KIOSK_USER")" "$HOME_DIR/.config/labwc/autostart"
 fi
 echo 'Kiosk autostart installed. From the graphical desktop, run: rdf-kiosk-session'
 echo 'If Chromium is missing, install your distro Chromium package. No display driver is changed.'
fi
printf '\nInstalled. Initial admin password is local only: sudo cat %s/initial-admin-password.txt\n' "$CONFIG_DIR"
echo 'No reboot is needed to start the backend. Cold-boot acceptance still needs a later device test.'
