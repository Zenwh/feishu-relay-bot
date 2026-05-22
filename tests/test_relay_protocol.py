"""relay_protocol v2 协议测试。"""
import pytest

from feishu_relay_bot.relay_protocol import (
    PROTOCOL_VERSION,
    make_error_response,
    make_heartbeat,
    make_native_success_response,
    make_success_response,
    parse_message,
    parse_request,
)


class TestParseRequest:
    def test_v2_request(self):
        data = {"_relay_v": 2, "type": "req", "req_id": "abc123", "model": "gpt-4"}
        result = parse_request(data)
        assert result is not None
        assert result["req_id"] == "abc123"

    def test_v1_backward_compat(self):
        data = {"_relay_v": 1, "req_id": "abc123", "model": "gpt-4"}
        result = parse_request(data)
        assert result is not None

    def test_missing_relay_v(self):
        assert parse_request({"req_id": "abc"}) is None

    def test_wrong_version(self):
        assert parse_request({"_relay_v": 99, "req_id": "abc"}) is None

    def test_missing_req_id(self):
        assert parse_request({"_relay_v": 2, "type": "req"}) is None

    def test_not_a_dict(self):
        assert parse_request("string") is None
        assert parse_request([1, 2, 3]) is None
        assert parse_request(None) is None


class TestParseMessage:
    def test_heartbeat(self):
        data = {"_relay_v": 2, "type": "heartbeat", "node_id": "n1"}
        result = parse_message(data)
        assert result is not None
        assert result["type"] == "heartbeat"

    def test_ctrl(self):
        data = {"_relay_v": 2, "type": "ctrl", "action": "restart"}
        result = parse_message(data)
        assert result is not None

    def test_v1_rejected(self):
        assert parse_message({"_relay_v": 1, "type": "req"}) is None

    def test_missing_type(self):
        assert parse_message({"_relay_v": 2}) is None


class TestMakeSuccessResponse:
    def test_structure(self):
        resp = make_success_response(
            req_id="r1",
            node_id="n1",
            content="hello",
            usage={"prompt_tokens": 10, "completion_tokens": 20},
            finish_reason="stop",
        )
        assert resp["_relay_v"] == PROTOCOL_VERSION
        assert resp["type"] == "resp"
        assert resp["ok"] is True
        assert resp["req_id"] == "r1"
        assert resp["node_id"] == "n1"
        assert resp["content"] == "hello"
        assert resp["finish_reason"] == "stop"


class TestMakeNativeSuccessResponse:
    def test_structure(self):
        raw = {"id": "msg_xxx", "content": [{"type": "text", "text": "hi"}]}
        resp = make_native_success_response("r1", "n1", raw)
        assert resp["mode"] == "messages_native"
        assert resp["raw_anthropic"] == raw
        assert resp["ok"] is True


class TestMakeErrorResponse:
    def test_structure(self):
        resp = make_error_response("r1", "n1", 429, "rate_limited", "too many requests")
        assert resp["ok"] is False
        assert resp["status"] == 429
        assert resp["error"] == "rate_limited"
        assert resp["node_id"] == "n1"


class TestMakeHeartbeat:
    def test_basic(self):
        hb = make_heartbeat("n1", version="0.2.0", hostname="srv1")
        assert hb["_relay_v"] == PROTOCOL_VERSION
        assert hb["type"] == "heartbeat"
        assert hb["node_id"] == "n1"
        assert hb["version"] == "0.2.0"
