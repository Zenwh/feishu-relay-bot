"""relay_codec 编解码测试。"""
import json
import pytest

from feishu_relay_bot.relay_codec import (
    COMPRESS_THRESHOLD,
    FEISHU_LIMIT,
    PayloadTooLargeError,
    decode,
    encode,
)


class TestEncode:
    def test_small_payload_no_compression(self):
        payload = {"hello": "world", "num": 42}
        result = encode(payload)
        assert result == json.dumps(payload, ensure_ascii=False)
        assert not result.startswith("__zb64__")

    def test_large_payload_compressed(self):
        payload = {"data": "x" * (COMPRESS_THRESHOLD + 1000)}
        result = encode(payload)
        assert result.startswith("__zb64__")

    def test_allow_compress_false(self):
        payload = {"data": "x" * (COMPRESS_THRESHOLD + 1000)}
        result = encode(payload, allow_compress=False)
        assert not result.startswith("__zb64__")
        assert json.loads(result) == payload

    def test_too_large_uncompressed_raises(self):
        payload = {"data": "x" * (FEISHU_LIMIT + 1000)}
        with pytest.raises(PayloadTooLargeError) as exc_info:
            encode(payload, allow_compress=False)
        assert exc_info.value.raw_size > FEISHU_LIMIT

    def test_chinese_content(self):
        payload = {"msg": "你好世界" * 100}
        result = encode(payload)
        decoded = decode(result)
        assert decoded == payload

    def test_roundtrip_small(self):
        payload = {"key": "value", "nested": {"a": 1}}
        assert decode(encode(payload)) == payload

    def test_roundtrip_large(self):
        payload = {"content": "a" * (COMPRESS_THRESHOLD * 2)}
        encoded = encode(payload)
        assert encoded.startswith("__zb64__")
        assert decode(encoded) == payload


class TestDecode:
    def test_plain_json(self):
        text = '{"foo": "bar"}'
        assert decode(text) == {"foo": "bar"}

    def test_compressed(self):
        payload = {"data": "test" * 20000}
        encoded = encode(payload)
        assert decode(encoded) == payload

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            decode("not json")

    def test_invalid_zb64_raises(self):
        with pytest.raises(Exception):
            decode("__zb64__not-valid-base64!!!")


class TestPayloadTooLargeError:
    def test_attributes(self):
        err = PayloadTooLargeError(200000, 160000)
        assert err.raw_size == 200000
        assert err.encoded_size == 160000
        assert err.size_kb == pytest.approx(200000 / 1024, rel=0.01)

    def test_default_encoded_size(self):
        err = PayloadTooLargeError(150000)
        assert err.encoded_size == 0
