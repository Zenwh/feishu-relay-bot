"""
Bot Manager：一进程多 bot 协调 + 心跳上报中心。
"""
from __future__ import annotations

import logging
import signal
import time
from typing import List, Optional

from . import __version__
from .bot import Bot
from .config import Config
from .heartbeat import HeartbeatClient
from .models import ModelRegistry
from .upstream import UpstreamClient

logger = logging.getLogger("manager")


class BotManager:
    """启动 / 监控所有 bot，捕获 SIGTERM / SIGINT 优雅退出。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._registry = ModelRegistry(cfg.models)
        self._bots: List[Bot] = []
        self._heartbeat: Optional[HeartbeatClient] = None
        self._stopping = False
        self._build_bots()
        self._build_heartbeat()

    def _build_bots(self) -> None:
        enabled = [b for b in self.cfg.bots if b.enabled]
        if not enabled:
            raise RuntimeError("no enabled bot in config")

        for bot_cfg in enabled:
            up_cfg = self.cfg.effective_upstream_for(bot_cfg)
            client = UpstreamClient(up_cfg, self._registry)
            self._bots.append(Bot(
                bot_cfg, client,
                worker_threads=self.cfg.runtime.worker_threads,
            ))

        logger.info(
            "configured %d bot(s): %s",
            len(self._bots),
            ", ".join(b.cfg.name for b in self._bots),
        )

    def _build_heartbeat(self) -> None:
        """如果配了 center.url 且 enabled=true，启心跳客户端。"""
        c = self.cfg.center
        if not c.enabled:
            logger.info("center heartbeat disabled by config")
            return
        if not c.url:
            logger.info("center.url not set, heartbeat disabled")
            return

        node_id = c.node_id if c.node_id and c.node_id != "auto" else None
        self._heartbeat = HeartbeatClient(
            center_url=c.url,
            node_id=node_id,
            payload_fn=self._build_heartbeat_payload,
            interval_s=c.interval_s,
            shared_secret=c.shared_secret,
            version=__version__,
        )

    def _build_heartbeat_payload(self) -> dict:
        """收集所有 bot 的 stats 汇总成单次心跳 payload。"""
        # bots 数组
        bots_info = []
        agg = {
            "requests_total": 0,
            "requests_ok": 0,
            "requests_failed": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "last_request_at": "",
        }
        for b in self._bots:
            s = b.stats_snapshot()
            bots_info.append({
                "name": b.cfg.name,
                "app_id": b.cfg.app_id,
                "alive": b.is_alive(),
                "stats": s,
            })
            agg["requests_total"] += s.get("requests_total", 0)
            agg["requests_ok"] += s.get("requests_ok", 0)
            agg["requests_failed"] += s.get("requests_failed", 0)
            agg["tokens_in"] += s.get("tokens_in", 0)
            agg["tokens_out"] += s.get("tokens_out", 0)
            la = s.get("last_request_at") or ""
            if la and la > agg["last_request_at"]:
                agg["last_request_at"] = la

        # 模型清单（去重）
        models = sorted(set(m.public for m in self.cfg.models))

        # 上游配置
        upstream_info = {
            "base_url": self.cfg.upstream.base_url,
        }

        return {
            "bots": bots_info,
            "models": models,
            "upstream": upstream_info,
            "stats": agg,
        }

    def run_forever(self) -> None:
        """启动所有 bot + heartbeat，主线程阻塞等信号。"""
        for bot in self._bots:
            bot.start()

        if self._heartbeat:
            self._heartbeat.start()

        logger.info("all bots started, press Ctrl+C to stop")

        # 注册信号
        def handler(signum, frame):
            logger.info("received signal %d, exiting...", signum)
            self._stopping = True
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

        # 主线程存活检查
        try:
            while not self._stopping:
                time.sleep(5)
                dead = [b.cfg.name for b in self._bots if not b.is_alive()]
                if dead:
                    logger.warning("bot(s) not alive: %s", dead)
        finally:
            if self._heartbeat:
                logger.info("stopping heartbeat, sending offline...")
                self._heartbeat.stop(send_offline=True)
            logger.info("manager stopped")
