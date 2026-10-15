# Contoh konfigurasi T900/PPP (pasang manual)

> **Hanya contoh.** File di folder ini **tidak** dipasang oleh `install.sh` (Edge maupun
> `--ground`). File yang Anda pasang manual dari sini juga tidak dihapus atau dikembalikan oleh
> `rollback.sh`/`uninstall.sh`. RDF Node tidak memiliki PPP, udev, atau radio T900. Setelah
> install, salinannya hanya ikut tersimpan di `/opt/rdf-node/current/deploy/examples/t900/`
> sebagai referensi.

Penjelasan, alasan, dan prosedur pra-terbang ada di [docs/T900_PPP.md](../../../docs/T900_PPP.md).

| File | Untuk | Tujuan setelah disesuaikan |
|---|---|---|
| `99-t900-pi.rules` | Raspberry/Edge | `/etc/udev/rules.d/99-t900-pi.rules` (alias `/dev/t900`) |
| `99-t900-ground.rules` | Ubuntu/Ground | `/etc/udev/rules.d/99-t900-ground.rules` (alias `/dev/t900-ground`) |
| `ppp-options.example` | Kedua sisi | Bandingkan/gabungkan ke file opsi pppd yang sudah dipakai unit, mis. `/etc/ppp/peers/<nama-site>` |
| `t900-ppp.service.d-10-recovery.conf` | Kedua sisi | Pi: `/etc/systemd/system/t900-ppp.service.d/10-recovery.conf`; Ubuntu: `/etc/systemd/system/<unit-PPP-Ground>.service.d/10-recovery.conf` |

Semua `GANTI_...` adalah placeholder yang wajib diganti. Placeholder yang tertinggal sengaja
membuat aturan udev tidak cocok atau pppd menolak start, sehingga tidak ada perubahan diam-diam.
Nama unit PPP Ubuntu milik site (catatan proyek memakai `t900-ground-ppp.service`); periksa
dengan `systemctl list-units --all '*ppp*'`.

## Sebelum mulai

- Kerjakan dalam jendela maintenance dengan akses lokal atau LAN cadangan. SSH lewat
  `10.90.0.2`/`10.90.0.1` ikut putus saat PPP direstart.
- Simpan salinan konfigurasi lama:
  `sudo cp -a /etc/ppp /root/ppp-backup-$(date +%Y%m%d)` dan
  `systemctl cat t900-ppp.service > /root/t900-ppp.unit.txt` (Ubuntu: unit Ground).
- Terapkan perubahan pppd yang sama (echo, MTU/MRU, baud, flow control) di **kedua** sisi.

## 1. Mencari ID_PATH untuk udev

Colok T900 ke port USB yang akan dipakai permanen, lalu:

```bash
ls -l /dev/serial/by-path/ /dev/serial/by-id/
udevadm info --query=property --name=/dev/ttyUSB0 \
  | grep -E '^(ID_PATH|ID_VENDOR_ID|ID_MODEL_ID|ID_SERIAL_SHORT)='
```

Ganti `/dev/ttyUSB0` dengan device yang muncul untuk T900 (cabut lalu colok, kemudian lihat
`journalctl -k -n 20 --no-pager`). Salin nilai `ID_PATH=` ke placeholder `GANTI_ID_PATH_PI`
atau `GANTI_ID_PATH_GROUND`. `ID_PATH` mengikuti **port fisik**, jadi radio harus selalu dicolok
ke port yang sama. Pakai opsi nomor seri hanya bila `ID_SERIAL_SHORT` ada dan unik; adapter CH340
umumnya tidak memilikinya.

Di Raspberry, nilai yang sama harus tercatat di `/etc/rdf-node/config.yaml`:

```yaml
link:
  serial_alias: /dev/t900
  expected_vid: '1a86'
  expected_pid: '7523'
  expected_usb_path: <ID_PATH yang sama>
```

## 2. Memasang aturan udev

```bash
sudo install -m 0644 99-t900-pi.rules /etc/udev/rules.d/99-t900-pi.rules   # Ubuntu: 99-t900-ground.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty --action=add
ls -l /dev/t900 && readlink -f /dev/t900                                    # Ubuntu: /dev/t900-ground
udevadm info --query=property --name=/dev/t900 | grep -E '^(ID_PATH|ID_VENDOR_ID|ID_MODEL_ID)='
```

Di panel Edge, tab Link harus menampilkan `T900 USB` = `PRESENT`. `IDENTITY_MISMATCH` berarti
VID/PID/ID_PATH tidak sama dengan `link.expected_*`; `MISSING` berarti alias belum ada.

## 3. Opsi pppd

Jangan menimpa file pppd yang sudah berjalan. Bandingkan dulu:

```bash
systemctl cat t900-ppp.service
ps -o args= -C pppd
sudo cat /etc/ppp/options /etc/ppp/peers/*
```

Gabungkan baris yang relevan dari `ppp-options.example`. Hal yang wajib sama di kedua sisi: baud
serial, `lcp-echo-interval`/`lcp-echo-failure`, `mtu`/`mru`, dan pilihan `crtscts`/`nocrtscts`.
Ganti device, alamat IP, dan `GANTI_BAUD` sesuai sisi. Pastikan `nodetach` cocok dengan `Type=`
unit. Lalu, dalam jendela maintenance:

```bash
sudo systemctl restart t900-ppp.service          # Ubuntu: unit PPP Ground milik site
journalctl -u t900-ppp.service -n 40 --no-pager -o short-precise
```

## 4. Drop-in systemd

```bash
sudo install -d /etc/systemd/system/t900-ppp.service.d
sudo install -m 0644 t900-ppp.service.d-10-recovery.conf /etc/systemd/system/t900-ppp.service.d/10-recovery.conf
sudo systemctl daemon-reload
systemctl show t900-ppp.service -p Restart -p RestartUSec -p NRestarts
```

Di Ubuntu, ganti `t900-ppp.service` dengan nama unit PPP Ground. Alternatif:
`sudo systemctl edit <unit>` lalu tempel isinya.

## Membatalkan

```bash
sudo rm /etc/udev/rules.d/99-t900-pi.rules && sudo udevadm control --reload-rules
sudo rm /etc/systemd/system/t900-ppp.service.d/10-recovery.conf && sudo systemctl daemon-reload
```

Kembalikan file pppd dari salinan backup, lalu restart unit PPP dalam jendela maintenance.
