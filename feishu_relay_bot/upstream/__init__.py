"""
Upstream LLM client.

把 bot 收到的 relay 协议请求路由到 xpage / MP 等上游的对应端点。
支持三种端点 + 一个 native 透传模式：
  - chat       → /v1/chat/completions   (OpenAI Chat 格式)
  - responses  → /v1/responses          (OpenAI Responses 格式)
  - messages   → /v1/messages           (Anthropic Messages 格式，结果归一化为 OpenAI)
  - native     → /v1/messages 透传      (用于 messages_native mode)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import httpx

from ..config import UpstreamConfig
from ..models import ModelEntry, ModelRegistry

logger = logging.getLogger("upstream")


class UpstreamClient:
    """
    单个上游网关 client。
    Bot 实例持有一个，多 bot 可以共享或各自独立（看 bot.upstream 是否覆盖）。
    """

    def __init__(self, cfg: UpstreamConfig, models: ModelRegistry):
        self._cfg = cfg
        self._models = models

    @property
    def models(self) -> ModelRegistry:
        return self._models

    @property
    def default_max_tokens(self) -> int:
        return self._cfg.default_max_tokens

    # ---- 通用 POST -----------------------------------------------------------

    def _post(self, path: str, payload: dict, extra_headers: Optional[dict] = None) -> Tuple[int, dict]:
        url = f"{self._cfg.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        with httpx.Client(timeout=self._cfg.timeout_s) as cli:
            r = cli.post(url, json=payload, headers=headers)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text[:500]}
        return r.status_code, data

    # ---- OpenAI Chat 模式：按模型路由 ----------------------------------------

    def call_openai_chat_mode(
        self,
        public_model: str,
        messages: list,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Tuple[int, dict]:
        """
        客户端走 /v1/chat/completions（即 relay 协议默认 mode），
        bot 端按模型 endpoint 类型路由到 xpage 三个端点之一，最后归一化为 OpenAI 格式。

        返回 (http_status, normalized_dict)。normalized_dict 形如：
          {"content": str, "finish_reason": str, "usage": {...}}
        """
        entry = self._models.get(public_model)
        if not entry:
            return 400, {
                "error": "unknown_model",
                "message": f"unsupported model: {public_model}",
            }

        upstream_model = entry.upstream
        endpoint = entry.endpoint

        if endpoint == "messages":
            return self._call_messages(
                upstream_model, messages,
                max_tokens or self._cfg.default_max_tokens, temperature,
            )
        if endpoint == "responses":
            return self._call_responses(
                upstream_model, messages,
                max_tokens or self._cfg.default_max_tokens, temperature,
            )
        if endpoint == "chat":
            return self._call_chat(upstream_model, messages, max_tokens, temperature)

        return 500, {"error": "bad_routing", "message": f"unknown endpoint: {endpoint}"}

    # ---- 三个 endpoint adapter ----------------------------------------------

    def _call_chat(
        self, upstream_model: str, messages: list,
        max_tokens: Optional[int], temperature: Optional[float],
    ) -> Tuple[int, dict]:
        payload: Dict[str, Any] = {
            "model": upstream_model,
            "messages": messages,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature

        status, data = self._post("/v1/chat/completions", payload)
        if status != 200:
            return status, data

        choices = data.get("choices") or []
        if not choices:
            return 502, {"error": "no_choices", "message": "empty response"}
        choice = choices[0]
        content = choice.get("message", {}).get("content", "")
        finish = choice.get("finish_reason", "stop")
        return 200, _normalize(content, finish, data.get("usage") or {})

    def _call_responses(
        self, upstream_model: str, messages: list,
        max_tokens: int, temperature: Optional[float],
    ) -> Tuple[int, dict]:
        system, rest = _split_system(messages)
        if len(rest) == 1 and rest[0].get("role") == "user":
            input_val: Any = rest[0].get("content", "")
        else:
            input_val = rest

        payload: Dict[str, Any] = {
            "model": upstream_model,
            "input": input_val,
            "max_output_tokens": max_tokens,
        }
        if system:
            payload["instructions"] = system
        if temperature is not None:
            payload["temperature"] = temperature

        status, data = self._post("/v1/responses", payload)
        if status != 200:
            return status, data

        content_parts = []
        for item in data.get("output", []):
            if item.get("type") == "message":
                for blk in item.get("content", []):
                    if blk.get("type") == "output_text":
                        content_parts.append(blk.get("text", ""))
        content = "".join(content_parts)
        finish = "stop"
        for item in data.get("output", []):
            if item.get("status") == "incomplete":
                finish = "length"
                break
        return 200, _normalize(content, finish, data.get("usage") or {})

    def _call_messages(
        self, upstream_model: str, messages: list,
        max_tokens: int, temperature: Optional[float],
    ) -> Tuple[int, dict]:
        system, rest = _split_system(messages)
        payload: Dict[str, Any] = {
            "model": upstream_model,
            "messages": rest,
            "max_tokens": max_tokens,
        }
        if system:
            payload["system"] = system
        if temperature is not None:
            payload["temperature"] = temperature

        status, data = self._post(
            "/v1/messages", payload,
            extra_headers={"anthropic-version": "2023-06-01"},
        )
        if status != 200:
            return status, data

        content_parts = []
        for blk in data.get("content", []):
            if blk.get("type") == "text":
                content_parts.append(blk.get("text", ""))
        content = "".join(content_parts)
        finish = data.get("stop_reason", "end_turn")
        if finish == "end_turn":
            finish = "stop"
        elif finish == "max_tokens":
            finish = "length"
        return 200, _normalize(content, finish, data.get("usage") or {})

    # ---- Anthropic 原生透传 -------------------------------------------------

    def call_messages_native(self, payload: dict) -> Tuple[int, dict]:
        """
        messages_native 模式：客户端走 Anthropic /v1/messages，bot 透传给上游。

        payload 已经是 Anthropic 原生格式（含 messages, system, tools 等），
        只需要把对外模型名替换成上游模型名后转发。

        返回 (http_status, raw_anthropic_response)。
        """
        public_model = payload.get("model", "")
        upstream_model = self._models.to_upstream(public_model)
        if not upstream_model:
            return 400, {
                "error": "unknown_model",
                "message": f"unsupported model: {public_model}",
            }

        body = dict(payload)
        body["model"] = upstream_model
        body.pop("stream", None)  # bot 端永远不流，由 relay 端伪流

        if "max_tokens" not in body or not body["max_tokens"]:
            body["max_tokens"] = self._cfg.default_max_tokens

        return self._post(
            "/v1/messages", body,
            extra_headers={"anthropic-version": "2023-06-01"},
        )


# ============================================================================
# helpers
# ============================================================================


def _split_system(messages: list) -> Tuple[str, list]:
    """从 OpenAI messages 数组拆出 system 文本 + 剩余 messages。"""
    sys_parts = []
    rest = []
    for m in messages:
        if m.get("role") == "system":
            sys_parts.append(m.get("content", ""))
        else:
            rest.append(m)
    return "\n\n".join(sys_parts), rest


def _normalize(content: str, finish_reason: str, usage: dict) -> dict:
    """归一化为 OpenAI 风格的 {content, finish_reason, usage}。"""
    p = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    c = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    t = usage.get("total_tokens", 0) or (p + c)
    return {
        "content": content,
        "finish_reason": finish_reason,
        "usage": {
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": t,
        },
    }
