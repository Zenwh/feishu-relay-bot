"""
Heartbeat client：节点 bot 定期向中心运营上报状态。

中心通过这个机制看到「谁部署了脚本、在不在线、各自能力清单、累计使用量」。
"""
from __future__ import annotations

import logging
import socket
import threading
import time
import uuid
from datetime import datetime
from typing import Callable, Optional

import httpx

logger = logging.getLogger("heartbeat")


def _local_ip() -> str:
    """获取本机内网 IP（best effort）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ""


def _auto_node_id() -> str:
    """生成自动 node_id：hostname-shortuuid。"""
    try:
        host = socket.gethostname().split(".")[0]
    except Exception:
        host = "unknown"
    return f"{host}-{uuid.uuid4().hex[:6]}"


class HeartbeatClient:
    """
    后台线程定期 POST 心跳给中心。

    用法：
        hb = HeartbeatClient(center_url, node_id, payload_fn)
        hb.start()       # 后台线程开跑
        # ... 进程跑 ...
        hb.stop()        # 退出前调（会主动 POST /agent/offline）
    """

    def __init__(
        self,
        center_url: str,
        node_id: Optional[str] = None,
        payload_fn: Optional[Callable[[], dict]] = None,
        interval_s: int = 30,
        shared_secret: Optional[str] = None,
        version: str = "",
    ):
        self.center_url = center_url.rstrip("/")
        self.node_id = node_id or _auto_node_id()
        self.payload_fn = payload_fn or (lambda: {})
        self.interval_s = max(5, interval_s)
        self.shared_secret = shared_secret or ""
        self.version = version

        self._started_at = datetime.now().isoformat(timespec="seconds")
        self._hostname = ""
        self._ip = ""
        try:
            self._hostname = socket.gethostname()
        except Exception:
            pass
        self._ip = _local_ip()

        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

    # ---- 启停 ---------------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.warning("heartbeat already running")
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._loop, name="heartbeat", daemon=True,
        )
        self._thread.start()
        logger.info(
            "heartbeat started: node=%s center=%s interval=%ds",
            self.node_id, self.center_url, self.interval_s,
        )

    def stop(self, send_offline: bool = True) -> None:
        """停止心跳。send_offline=True 时主动发一次 offline 通知中心。"""
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=3)
        if send_offline:
            try:
                self._post_offline()
            except Exception as e:
                logger.warning("send offline failed: %s", e)

    # ---- 循环 ---------------------------------------------------------------

    def _loop(self) -> None:
        # 启动立刻发一次
        self._safe_send()
        while not self._stop_evt.is_set():
            # 用 wait 等同步，可以被 stop_evt 中断
            if self._stop_evt.wait(timeout=self.interval_s):
                break
            self._safe_send()

    def _safe_send(self) -> None:
        try:
            self._post_heartbeat()
        except Exception as e:
            logger.warning("heartbeat post failed: %s", e)

    # ---- HTTP --------------------------------------------------------------

    def _build_payload(self) -> dict:
        body = {
            "node_id": self.node_id,
            "version": self.version,
            "hostname": self._hostname,
            "ip": self._ip,
            "started_at": self._started_at,
            "status": "online",
            "capabilities": ["zlib"],
        }
        try:
            extra = self.payload_fn() or {}
            body.update(extra)
        except Exception as e:
            logger.warning("payload_fn raised: %s", e)
        return body

    def _post_heartbeat(self) -> None:
        url = f"{self.center_url}/agent/heartbeat"
        body = self._build_payload()
        headers = {"Content-Type": "application/json"}
        if self.shared_secret:
            headers["X-Agent-Secret"] = self.shared_secret
        with httpx.Client(timeout=10) as cli:
            r = cli.post(url, json=body, headers=headers)
        if r.status_code != 200:
            logger.warning(
                "heartbeat non-200: %d %.200s",
                r.status_code, r.text,
            )
        else:
            logger.debug("heartbeat OK")

    def _post_offline(self) -> None:
        url = f"{self.center_url}/agent/offline"
        with httpx.Client(timeout=5) as cli:
            r = cli.post(url, json={"node_id": self.node_id})
        logger.info("offline notify: HTTP %d", r.status_code)
