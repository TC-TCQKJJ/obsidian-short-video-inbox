from __future__ import annotations


MASK64 = 0xFFFFFFFFFFFFFFFF


def _u64(value: int) -> int:
    return value & MASK64


class Isaac64:
    """ISAAC-64 port used by WeChat Channels prefix encryption.

    Derived from Hanson/WechatSphDecrypt via ltaoo/wx_channels_download.
    Copyright (c) 2025 ltaoo, MIT License with Commons Clause condition.
    """

    def __init__(self, key: int) -> None:
        self._count = 255
        self._seed = [0] * 256
        self._memory = [0] * 256
        self._aa = 0
        self._bb = 0
        self._cc = 0
        self._initialize(_u64(key))

    def random(self) -> int:
        result = self._seed[self._count]
        if self._count == 0:
            self._generate()
            self._count = 255
        else:
            self._count -= 1
        return result

    def _initialize(self, key: int) -> None:
        golden = 0x9E3779B97F4A7C13
        values = [golden] * 8
        self._seed[0] = key

        for _ in range(4):
            values = _mix(values)

        for offset in range(0, 256, 8):
            values = [
                _u64(values[index] + self._seed[offset + index])
                for index in range(8)
            ]
            values = _mix(values)
            self._memory[offset : offset + 8] = values

        for offset in range(0, 256, 8):
            values = [
                _u64(values[index] + self._memory[offset + index])
                for index in range(8)
            ]
            values = _mix(values)
            self._memory[offset : offset + 8] = values

        self._generate()

    def _generate(self) -> None:
        self._cc = _u64(self._cc + 1)
        self._bb = _u64(self._bb + self._cc)

        for index in range(256):
            if index % 4 == 0:
                self._aa = _u64(~(self._aa ^ _u64(self._aa << 21)))
            elif index % 4 == 1:
                self._aa = _u64(self._aa ^ (self._aa >> 5))
            elif index % 4 == 2:
                self._aa = _u64(self._aa ^ _u64(self._aa << 12))
            else:
                self._aa = _u64(self._aa ^ (self._aa >> 33))

            self._aa = _u64(self._aa + self._memory[(index + 128) % 256])
            value = self._memory[index]
            mixed = _u64(
                self._memory[(value >> 3) % 256] + self._aa + self._bb
            )
            self._memory[index] = mixed
            self._bb = _u64(self._memory[(mixed >> 11) % 256] + value)
            self._seed[index] = self._bb


def _mix(values: list[int]) -> list[int]:
    a, b, c, d, e, f, g, h = values
    a = _u64(a - e)
    f = _u64(f ^ (h >> 9))
    h = _u64(h + a)
    b = _u64(b - f)
    g = _u64(g ^ _u64(a << 9))
    a = _u64(a + b)
    c = _u64(c - g)
    h = _u64(h ^ (b >> 23))
    b = _u64(b + c)
    d = _u64(d - h)
    a = _u64(a ^ _u64(c << 15))
    c = _u64(c + d)
    e = _u64(e - a)
    b = _u64(b ^ (d >> 14))
    d = _u64(d + e)
    f = _u64(f - b)
    c = _u64(c ^ _u64(e << 20))
    e = _u64(e + f)
    g = _u64(g - c)
    d = _u64(d ^ (f >> 17))
    f = _u64(f + g)
    h = _u64(h - d)
    e = _u64(e ^ _u64(g << 14))
    g = _u64(g + h)
    return [a, b, c, d, e, f, g, h]


class WechatDecryptor:
    def __init__(self, key: int, encrypted_limit: int = 131072) -> None:
        self._rng = Isaac64(key)
        self._encrypted_limit = encrypted_limit
        self._consumed = 0
        self._keystream = b""
        self._keystream_offset = 8

    def transform(self, chunk: bytes) -> bytes:
        output = bytearray(chunk)
        decrypt_count = min(
            len(output),
            max(0, self._encrypted_limit - self._consumed),
        )
        for index in range(decrypt_count):
            if self._keystream_offset >= 8:
                self._keystream = self._rng.random().to_bytes(8, "big")
                self._keystream_offset = 0
            output[index] ^= self._keystream[self._keystream_offset]
            self._keystream_offset += 1
        self._consumed += decrypt_count
        return bytes(output)
