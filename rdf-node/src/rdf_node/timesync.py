"""Clock samples for chrony's SOCK refclock (`refclock SOCK /run/chrony.<name>.sock`).

chrony owns the socket; we send one datagram per sample: the local time it was measured at and the
offset `reference - local` in seconds. chrony filters, combines sources (LoRa, GPS, NTP) and slews the
clock, so nothing here needs root or touches the clock itself.
"""
from __future__ import annotations
import socket
import struct
import time

SOCK_MAGIC=0x534f434b
# struct sock_sample { struct timeval tv; double offset; int pulse; int leap; int _pad; int magic; }
SAMPLE=struct.Struct('=qqdiiii')

def sock_sample(local_s: float, offset_s: float, leap: int = 0) -> bytes:
    sec=int(local_s); usec=int(round((local_s-sec)*1e6))
    if usec>=1000000: sec+=1; usec-=1000000
    return SAMPLE.pack(sec,usec,offset_s,0,leap,0,SOCK_MAGIC)

class ChronySock:
    """Best-effort sender: a missing socket (chrony not running) is counted, never raised."""
    def __init__(self,path):
        self.path=path; self.sent=0; self.errors=0; self.error=None
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) if path else None
    def send(self,offset_s: float, local_s: float | None = None) -> bool:
        if not self.sock: return False
        try:
            self.sock.sendto(sock_sample(time.time() if local_s is None else local_s,offset_s),self.path)
            self.sent+=1; self.error=None; return True
        except OSError as e:
            self.errors+=1; self.error=f'ERRNO_{e.errno}' if e.errno else 'SEND_FAILED'; return False
    def close(self):
        if self.sock: self.sock.close(); self.sock=None
