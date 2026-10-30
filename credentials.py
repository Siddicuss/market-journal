"""Kalshi API credentials, protected with Windows DPAPI.

The journal starts with no key. The user enters one in Settings, and it is
saved here for their Windows login only. Nothing is ever written in plain text.
"""
import base64
import ctypes
from ctypes import wintypes
import json
import os
import tempfile
from pathlib import Path


def app_dir():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'MarketJournal'


def migrate_old_data():
    """Move data from the app's old name (Kalshi Journal) the first time Market Journal runs."""
    old, new = app_dir().parent / 'KalshiJournal', app_dir()
    if old.is_dir() and not new.exists():
        try:
            old.rename(new)
        except OSError:
            pass


def journal_credentials_path():
    return app_dir() / 'credentials.json'


def protect(data, decrypt=False):
    if os.name != 'nt':
        raise ValueError('Saving credentials requires Windows encryption.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = api.CryptUnprotectData if decrypt else api.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('Windows could not unlock or protect the saved credentials.')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def _decode(encoded):
    key, pem = json.loads(protect(base64.b64decode(encoded), decrypt=True))
    return key, pem


def validate(key, pem):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
    try:
        if not key.strip() or not isinstance(load_pem_private_key(pem.encode(), password=None), RSAPrivateKey):
            raise ValueError()
    except Exception:
        raise ValueError('Enter your API key ID and a valid, unencrypted RSA private key file.') from None


def save(key, pem, path=None):
    validate(key, pem)
    path = path or journal_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = base64.b64encode(protect(json.dumps([key.strip(), pem]).encode())).decode()
    fd, name = tempfile.mkstemp(prefix='credentials-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'production': encoded}, stream)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def forget(path=None):
    (path or journal_credentials_path()).unlink(missing_ok=True)


def load(path=None):
    """Return (key_id, pem, 'journal') or None when no key has been entered."""
    try:
        encoded = json.loads(Path(path or journal_credentials_path()).read_text(encoding='utf-8')).get('production')
        if not encoded:
            return None
        key, pem = _decode(encoded)
    except Exception:
        return None
    return key, pem, 'journal'
