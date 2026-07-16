import unittest

from script.wechat_media_decrypt import Isaac64, WechatDecryptor


class WechatMediaDecryptTest(unittest.TestCase):
    def test_isaac64_known_vector_for_key_one(self):
        rng = Isaac64(1)

        self.assertEqual(
            [rng.random() for _ in range(4)],
            [
                0xE19ED5D2CA98AF2D,
                0xA7A18D07CAB39B52,
                0xA0AB0232D180AF14,
                0x72B36DCF1AF5E46F,
            ],
        )

    def test_streaming_boundaries_produce_identical_output(self):
        payload = bytes((index * 37) % 256 for index in range(140_000))

        whole = WechatDecryptor(987654321).transform(payload)

        single = WechatDecryptor(987654321)
        one_byte = b"".join(single.transform(bytes([value])) for value in payload)

        irregular = WechatDecryptor(987654321)
        chunks = []
        offset = 0
        sizes = [3, 8191, 7, 65536, 11, 1024]
        while offset < len(payload):
            size = sizes[len(chunks) % len(sizes)]
            chunks.append(irregular.transform(payload[offset : offset + size]))
            offset += size

        self.assertEqual(one_byte, whole)
        self.assertEqual(b"".join(chunks), whole)

    def test_transform_is_symmetric_and_stops_after_encrypted_limit(self):
        payload = bytes((index * 11) % 256 for index in range(140_000))
        encrypted = WechatDecryptor(42).transform(payload)
        decrypted = WechatDecryptor(42).transform(encrypted)

        self.assertEqual(decrypted, payload)
        self.assertEqual(encrypted[131072:], payload[131072:])


if __name__ == "__main__":
    unittest.main()
