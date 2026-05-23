"""
单个飞书 Bot 实例：一个 ws 连接 + 一个上游 client。
收到 relay 协议消息 → 路由到 upstream → 把响应打包发回飞书。
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

from .config import BotConfig
from .ctrl import handle_ctrl
from .relay_codec import PayloadTooLargeError, encode as codec_encode, decode as codec_decode
from .relay_protocol import (
    parse_request,
    make_success_response,
    make_native_success_response,
    make_error_response,
)
from .upstream import UpstreamClient


class Bot:
    """一个飞书 bot：维护 ws 连接 + 处理消息 + 调上游。"""

    def __init__(
        self,
        cfg: BotConfig,
        upstream: UpstreamClient,
        worker_threads: int = 32,
        node_id: str = "",
    ):
        self.cfg = cfg
        self.upstream = upstream
        self.node_id = node_id or cfg.name
        self.logger = logging.getLogger(f"bot.{cfg.name}")
        self._executor = ThreadPoolExecutor(
            max_workers=worker_threads,
            thread_name_prefix=f"bot-{cfg.name}",
        )

        # 本地 stats（累计计数；进程级，不持久化）
        import threading as _t
        self._stats_lock = _t.Lock()
        self._stats = {
            "requests_total": 0,
            "requests_ok": 0,
            "requests_failed": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "last_request_at": "",
            "last_ok_at": "",
        }

        # lark 客户端（用来发消息）
        self._lark_client = lark.Client.builder() \
            .app_id(cfg.app_id) \
            .app_secret(cfg.app_secret) \
            .log_level(lark.LogLevel.WARNING) \
            .build()

        # ws 事件 handler
        handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._on_message) \
            .build()

        self._ws_client = lark.ws.Client(
            cfg.app_id,
            cfg.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )

        self._thread: Optional[threading.Thread] = None
        self._reconnect_enabled = True
        self._max_backoff_s = 60
        self._chat_id: Optional[str] = cfg.chat_id or None

    # ---- 启停 ---------------------------------------------------------------

    def start(self) -> None:
        """启动 ws，子线程阻塞跑（带自动重连）。"""
        if self._thread and self._thread.is_alive():
            self.logger.warning("already running")
            return
        self.logger.info("starting bot %s app_id=%s", self.cfg.name, self.cfg.app_id)
        self._reconnect_enabled = True
        self._thread = threading.Thread(
            target=self._ws_loop,
            name=f"ws-{self.cfg.name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """停止 bot，禁止重连。"""
        self._reconnect_enabled = False

    def _ws_loop(self) -> None:
        """ws 连接循环：断线后指数退避重连。"""
        import time
        backoff = 5
        while self._reconnect_enabled:
            try:
                self.logger.info("ws connecting...")
                self._ws_client.start()
            except Exception as e:
                self.logger.warning("ws connection error: %s", e)
            if not self._reconnect_enabled:
                break
            self.logger.warning("ws disconnected, reconnecting in %ds...", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, self._max_backoff_s)
        self.logger.info("ws loop exited")

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---- stats --------------------------------------------------------------

    def stats_snapshot(self) -> dict:
        """获取当前 stats 副本（用于上报）。"""
        with self._stats_lock:
            return dict(self._stats)

    def _record_request(self, ok: bool, tokens_in: int = 0, tokens_out: int = 0) -> None:
        """每次处理完一次请求，更新 stats。"""
        import datetime as _dt
        now = _dt.datetime.now().isoformat(timespec="seconds")
        with self._stats_lock:
            self._stats["requests_total"] += 1
            if ok:
                self._stats["requests_ok"] += 1
                self._stats["tokens_in"] += tokens_in
                self._stats["tokens_out"] += tokens_out
                self._stats["last_ok_at"] = now
            else:
                self._stats["requests_failed"] += 1
            self._stats["last_request_at"] = now

    # ---- 事件处理 -----------------------------------------------------------

    def _on_message(self, data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        event = data.event
        msg = event.message

        if msg.message_type != "text":
            self.logger.debug("ignore non-text msg: type=%s", msg.message_type)
            return

        try:
            content = json.loads(msg.content)
        except Exception:
            return
        raw_text = content.get("text", "")
        chat_id = msg.chat_id

        # 记住 chat_id（后续心跳上报用）
        if chat_id and not self._chat_id:
            self._chat_id = chat_id
            self.logger.info("discovered chat_id=%s", chat_id)

        # 先尝试 codec 解码（兼容压缩和非压缩格式）
        try:
            decoded = codec_decode(raw_text) if raw_text else None
        except Exception:
            decoded = None

        if not isinstance(decoded, dict) or decoded.get("_relay_v") is None:
            self.logger.debug("ignore non-relay msg: %.60s", raw_text)
            return
        req = decoded

        # 管控指令路由
        if req.get("type") == "ctrl":
            handle_ctrl(self, req)
            return

        req_id = req["req_id"]
        mode = req.get("mode", "openai_chat")
        model = req.get("model", "")
        msgs = req.get("messages", [])
        self.logger.info(
            "← req_id=%s mode=%s model=%s msgs=%d",
            req_id, mode, model, len(msgs),
        )

        # 异步处理避免阻塞 ws 线程（飞书 ACK 限 3s）
        self._executor.submit(self._handle_request, req, chat_id)

    def _handle_request(self, req: dict, chat_id: str) -> None:
        """实际处理一条 relay 请求：调上游 → 发回飞书。"""
        req_id = req["req_id"]
        mode = req.get("mode", "openai_chat")
        try:
            if mode == "messages_native":
                self._handle_native(req, chat_id)
            else:
                self._handle_chat(req, chat_id)
        except Exception as e:
            self.logger.exception("处理 req_id=%s 异常", req_id)
            self._reply_json(chat_id, make_error_response(
                req_id, self.node_id, 500, "bot_exception", f"{type(e).__name__}: {e}",
            ))

    def _handle_chat(self, req: dict, chat_id: str) -> None:
        """OpenAI Chat 模式：归一化输出。"""
        req_id = req["req_id"]
        model = req.get("model", "")
        messages = req.get("messages", [])

        if not self.upstream.models.is_supported(model):
            self._reply_json(chat_id, make_error_response(
                req_id, self.node_id, 400, "unsupported_model",
                f"unsupported model: {model}",
            ))
            return

        # 构建完整 payload，透传客户端所有参数
        payload = {
            "model": model,
            "messages": messages,
        }
        for k in ("max_tokens", "temperature", "top_p", "frequency_penalty",
                  "presence_penalty", "tools", "tool_choice", "response_format",
                  "stream", "n", "stop", "seed", "logprobs", "logit_bias"):
            if k in req and req[k] is not None:
                payload[k] = req[k]

        status, resp = self.upstream.call_openai_chat_mode(model, payload)

        if status == 200:
            usage = resp.get("usage") or {}
            self._record_request(
                ok=True,
                tokens_in=usage.get("prompt_tokens", 0),
                tokens_out=usage.get("completion_tokens", 0),
            )
            self._reply_json(chat_id, make_success_response(
                req_id,
                node_id=self.node_id,
                content=resp["content"],
                usage=resp["usage"],
                finish_reason=resp["finish_reason"],
            ))
        else:
            self._record_request(ok=False)
            err_msg = (
                resp.get("error") if isinstance(resp.get("error"), str)
                else (resp.get("msg") or str(resp)[:300])
            )
            self._reply_json(chat_id, make_error_response(
                req_id,
                self.node_id,
                status if status >= 400 else 502,
                "upstream_error",
                err_msg,
            ))

    def _handle_native(self, req: dict, chat_id: str) -> None:
        """Anthropic 原生透传模式。"""
        req_id = req["req_id"]
        # 把 relay 协议字段拿掉，剩下就是 Anthropic body
        payload = {
            k: v for k, v in req.items()
            if k not in ("_relay_v", "req_id", "mode", "type", "endpoint")
        }

        status, resp = self.upstream.call_messages_native(payload)

        if status == 200 and "content" in resp:
            usage = resp.get("usage") or {}
            self._record_request(
                ok=True,
                tokens_in=usage.get("input_tokens", 0),
                tokens_out=usage.get("output_tokens", 0),
            )
            self._reply_json(chat_id, make_native_success_response(req_id, self.node_id, resp))
        else:
            self._record_request(ok=False)
            err_msg = (
                resp.get("error", {}).get("message")
                if isinstance(resp.get("error"), dict)
                else resp.get("msg") or str(resp)[:300]
            )
            self._reply_json(chat_id, make_error_response(
                req_id,
                self.node_id,
                status if status >= 400 else 502,
                "upstream_error",
                err_msg,
            ))

    # ---- 发飞书消息 ---------------------------------------------------------

    def _reply_json(self, chat_id: str, payload: dict) -> None:
        try:
            text = codec_encode(payload)
        except PayloadTooLargeError:
            payload.pop("raw_anthropic", None)
            if "content" in payload and isinstance(payload["content"], str):
                payload["content"] = payload["content"][:8000] + "\n...[truncated]"
                payload["finish_reason"] = "length"
            text = codec_encode(payload, allow_compress=False)
        try:
            req = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": text}, ensure_ascii=False))
                    .build()
                ).build()
            resp = self._lark_client.im.v1.message.create(req)
            if not resp.success():
                self.logger.error("回复失败: code=%s msg=%s", resp.code, resp.msg)
            else:
                self.logger.info(
                    "→ req_id=%s ok=%s len=%d",
                    payload.get("req_id"), payload.get("ok"), len(text),
                )
        except Exception as e:
            self.logger.exception("发送飞书消息异常: %s", e)
