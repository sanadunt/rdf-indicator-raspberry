from __future__ import annotations
import hashlib
import json
import math
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

def now_ms() -> int:
    return time.time_ns() // 1000000

def compact(value: Any) -> bytes:
    return json.dumps(value, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('utf-8')

def strict_json(data: bytes | str) -> Any:
    def pairs(items):
        d = {}
        for k, v in items:
            if k in d:
                raise ValueError('DUPLICATE_KEY')
            d[k] = v
        return d
    def bad_constant(s):
        raise ValueError('NONFINITE_JSON')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=bad_constant)

def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def finite(v: Any, lo: float = -math.inf, hi: float = math.inf) -> float:
    if isinstance(v, bool):
        raise ValueError('BOOLEAN_AS_NUMBER')
    n = float(v)
    if not math.isfinite(n) or not lo <= n <= hi:
        raise ValueError('NUMBER_OUT_OF_RANGE')
    return n

def integer(v: Any, lo: int = 0, hi: int = (1 << 63)-1) -> int:
    if isinstance(v, bool):
        raise ValueError('BOOLEAN_AS_INTEGER')
    n = int(v)
    if n != float(v) or not lo <= n <= hi:
        raise ValueError('INTEGER_OUT_OF_RANGE')
    return n

def stable_read(path: Path, limit: int = 8192, *, nofollow: bool = False) -> bytes:
    flags = os.O_RDONLY | (getattr(os, 'O_NOFOLLOW', 0) if nofollow else 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError('FILE_TOO_LARGE_OR_NOT_REGULAR')
        with os.fdopen(fd, 'rb', closefd=False) as f:
            data = f.read(limit + 1)
        after = os.fstat(fd)
        current = path.stat()
        if len(data) > limit or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('FILE_CHANGED_DURING_READ')
        if current.st_ino != after.st_ino or current.st_mtime_ns != after.st_mtime_ns:
            raise ValueError('FILE_REPLACED_DURING_READ')
        return data
    finally:
        os.close(fd)

def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def read_boot_id() -> str:
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        import uuid
        return str(uuid.uuid4())
