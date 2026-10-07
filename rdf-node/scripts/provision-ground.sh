#!/usr/bin/env bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo 'Run with sudo.' >&2; exit 1; }
command -v rdf-node >/dev/null || { echo 'First: sudo bash install.sh --ground' >&2; exit 1; }
command -v mosquitto >/dev/null || { echo 'Install prerequisites: sudo apt install mosquitto mosquitto-clients openssl'; exit 1; }
command -v mosquitto_passwd >/dev/null
command -v openssl >/dev/null
VERSION=$(mosquitto -h 2>&1 | head -n 1)
case "$VERSION" in *'version 2.'*) ;; *) echo "Supported provisioning target: Mosquitto 2.x; detected $VERSION"; exit 1;; esac
DIR=/etc/rdf-ground-mqtt
BUNDLE_HOME=$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)
OUT=${1:-$BUNDLE_HOME/rdf-uav-bundle}
if [ -e "$DIR/mosquitto.conf" ]; then echo 'Provisioning already exists. Refusing to rotate credentials/certificates silently.'; exit 1; fi
printf 'This creates an isolated TLS broker on 10.90.0.1:8883 and 127.0.0.1:8883.\nExisting Mosquitto and PPP configs are not overwritten.\n'
read -r -p 'Continue? [y/N]: ' answer
[ "$answer" = y ] || exit 0
install -d -m 0750 -o root -g mosquitto "$DIR"
install -d -m 0750 -o mosquitto -g mosquitto /var/lib/rdf-ground-mqtt
install -d -m 0700 "$OUT"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
umask 077
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$TMP/ca.key" 2>/dev/null
openssl req -x509 -new -key "$TMP/ca.key" -sha256 -days 3650 -subj '/CN=RDF Local CA' -out "$TMP/ca.crt"
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$TMP/server.key" 2>/dev/null
openssl req -new -key "$TMP/server.key" -subj '/CN=RDF Ground Broker' -out "$TMP/server.csr"
printf 'subjectAltName=IP:10.90.0.1,IP:127.0.0.1,DNS:localhost\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\n' > "$TMP/server.ext"
openssl x509 -req -in "$TMP/server.csr" -CA "$TMP/ca.crt" -CAkey "$TMP/ca.key" -CAcreateserial -days 825 -sha256 -extfile "$TMP/server.ext" -out "$TMP/server.crt" 2>/dev/null
install -m 0640 -o root -g mosquitto "$TMP/server.key" "$DIR/server.key"
install -m 0640 -o root -g mosquitto "$TMP/server.crt" "$DIR/server.crt"
install -m 0640 -o root -g mosquitto "$TMP/ca.crt" "$DIR/ca.crt"
install -m 0600 -o root -g root "$TMP/ca.key" "$DIR/ca.key"
export RDF_PROVISION_DIR="$DIR" RDF_BUNDLE_OUT="$OUT" RDF_TMP_DIR="$TMP"
/usr/bin/python3 - <<'PY'
import os,sys,secrets,json,pathlib,grp
sys.path[:0]=['/opt/rdf-node/current/src','/opt/rdf-node/current/vendor']
from rdf_node.config import load_config,save_config
from rdf_node.util import atomic_write
cfg=load_config('/etc/rdf-ground/config.yaml'); node=cfg['node_id']; prefix='sdr/v2/'+node
out=pathlib.Path(os.environ['RDF_BUNDLE_OUT']); tmp=pathlib.Path(os.environ['RDF_TMP_DIR']); broker=pathlib.Path(os.environ['RDF_PROVISION_DIR'])
users={node+'-control':secrets.token_urlsafe(30),node+'-bulk':secrets.token_urlsafe(30),'rdf-ground-controller':secrets.token_urlsafe(30),'rdf-ground-viewer':secrets.token_urlsafe(30)}
atomic_write(tmp/'passwd',''.join(k+':'+v+'\n' for k,v in users.items()).encode())
acl=[]
def section(user,pubs,subs):
 acl.append('user '+user)
 acl.extend('topic write '+prefix+'/'+x for x in pubs)
 acl.extend('topic read '+prefix+'/'+x for x in subs)
 acl.append('')
section(node+'-control',['telemetry/doa','telemetry/diagnostic/doa','telemetry/diagnostic/angular','telemetry/health','telemetry/health/detail','state','availability','settings/reported','capabilities','ack/config','ack/operation'],['cmd/#'])
section(node+'-bulk',['telemetry/angular'],[])
section('rdf-ground-controller',['cmd/#'],['telemetry/#','state','availability','settings/reported','capabilities','ack/#'])
section('rdf-ground-viewer',[],['telemetry/#','state','availability','settings/reported','capabilities','ack/#'])
atomic_write(tmp/'acl',('\n'.join(acl)+'\n').encode())
for name,user in [('uav-control.json',node+'-control'),('uav-bulk.json',node+'-bulk')]:
 atomic_write(out/name,json.dumps({'username':user,'password':users[user]}).encode())
atomic_write(out/'ca.crt',(tmp/'ca.crt').read_bytes())
atomic_write(out/'bundle.json',json.dumps({'node_id':node,'host':'10.90.0.1','port':8883}).encode())
ground=pathlib.Path('/etc/rdf-ground'); creds=ground/'credentials'; creds.mkdir(mode=0o750,exist_ok=True)
gid=grp.getgrnam('rdf-ground').gr_gid;os.chown(creds,0,gid)
for name,content in [('ca.crt',(tmp/'ca.crt').read_bytes()),('controller.json',json.dumps({'username':'rdf-ground-controller','password':users['rdf-ground-controller']}).encode()),('viewer.json',json.dumps({'username':'rdf-ground-viewer','password':users['rdf-ground-viewer']}).encode())]:
 atomic_write(creds/name,content,0o640);os.chown(creds/name,0,gid)
cfg['mqtt'].update(enabled=True,host='127.0.0.1',port=8883,tls=True,ca_file=str(creds/'ca.crt'),control_credentials_file=str(creds/'controller.json'),bulk_credentials_file=str(creds/'controller.json'))
save_config(ground/'config.yaml',cfg);os.chown(ground/'config.yaml',0,gid)
PY
mosquitto_passwd -U "$TMP/passwd"
install -m 0640 -o root -g mosquitto "$TMP/passwd" "$DIR/passwd"
install -m 0640 -o root -g mosquitto "$TMP/acl" "$DIR/acl"
cat > "$DIR/mosquitto.conf" <<EOF
allow_anonymous false
password_file $DIR/passwd
acl_file $DIR/acl
persistence true
persistence_location /var/lib/rdf-ground-mqtt/
max_packet_size 16384
max_queued_messages 16
max_queued_bytes 32768
queue_qos0_messages false
log_type error
log_type warning
log_dest stderr
listener 8883 10.90.0.1
certfile $DIR/server.crt
keyfile $DIR/server.key
cafile $DIR/ca.crt
require_certificate false
listener 8883 127.0.0.1
certfile $DIR/server.crt
keyfile $DIR/server.key
cafile $DIR/ca.crt
require_certificate false
EOF
chown root:mosquitto "$DIR/mosquitto.conf";chmod 0640 "$DIR/mosquitto.conf"
cat > /etc/systemd/system/rdf-ground-mqtt.service <<'EOF'
[Unit]
Description=Isolated RDF TLS MQTT broker
After=network.target
StartLimitIntervalSec=0
[Service]
Type=exec
User=mosquitto
Group=mosquitto
ExecStartPre=/usr/bin/python3 /opt/rdf-node/current/scripts/wait-address.py 10.90.0.1 60
ExecStart=/usr/sbin/mosquitto -c /etc/rdf-ground-mqtt/mosquitto.conf
Restart=on-failure
RestartSec=5
TimeoutStartSec=75
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/rdf-ground-mqtt
[Install]
WantedBy=multi-user.target
EOF
# A private bundle is only generated on YOUR Ubuntu, never embedded in the release.
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then chown -R "$SUDO_USER:$(id -gn "$SUDO_USER")" "$OUT"; fi
systemctl daemon-reload
systemctl enable --now rdf-ground-mqtt.service rdf-ground.service
printf '\nGround companion: http://127.0.0.1:8791\nUAV credential bundle: %s\n' "$OUT"
echo 'Transfer that DIRECTORY securely to Raspberry, then: sudo rdf-node import-bundle /path/to/uav-bundle'
echo 'Do not upload the credential bundle or CA private key to a repository/chat.'
