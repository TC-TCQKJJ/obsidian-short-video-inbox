from __future__ import annotations

import ctypes
import os


CRYPTPROTECT_UI_FORBIDDEN = 0x1


if os.name == "nt":
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]


    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL

    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL

    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL


def protect_bytes(plaintext: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI is only available on Windows")

    input_blob, input_buffer = _blob_from_bytes(plaintext)
    output_blob = DATA_BLOB()
    if not _crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    return _copy_and_free(output_blob)


def unprotect_bytes(ciphertext: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI is only available on Windows")

    input_blob, input_buffer = _blob_from_bytes(ciphertext)
    output_blob = DATA_BLOB()
    if not _crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    return _copy_and_free(output_blob)


def _blob_from_bytes(value: bytes) -> tuple["DATA_BLOB", ctypes.Array[ctypes.c_byte]]:
    size = max(1, len(value))
    buffer = (ctypes.c_byte * size)()
    if value:
        ctypes.memmove(buffer, value, len(value))
    return DATA_BLOB(len(value), buffer), buffer


def _copy_and_free(blob: "DATA_BLOB") -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))
