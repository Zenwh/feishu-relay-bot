"""
配置加载层：YAML + env 变量替换 + Pydantic 校验。

加载顺序:
  1. 默认值（dataclass / Pydantic 默认）
  2. config.yaml
  3. env 变量（YAML 里的 ${VAR_NAME:-default} 占位会被替换）

env 也可以直接覆盖某些值（看 ENV_OVERRIDES）。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from .models import DEFAULT_MODELS, ModelEntry

logger = logging.getLogger("config")


# ============================================================================
# 子配置
# ============================================================================


class UpstreamConfig(BaseModel):
    """上游 LLM 网关（xpage / MP / OpenAI 等）配置。"""
    base_url: str = Field(..., description="上游 base URL，例如 http://xpage:8800")
    api_key: str = Field("", description="上游 API key（Bearer token）")
    timeout_s: int = Field(1200, description="HTTP 请求超时（秒），thinking 模型需要 20 分钟")
    default_max_tokens: int = Field(4096, description="客户端不指定时的 max_tokens")

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


class BotConfig(BaseModel):
    """单个飞书 bot 的配置。"""
    name: str = Field(..., description="Bot 名称（日志/监控用）")
    app_id: str = Field(..., description="飞书 app ID")
    app_secret: str = Field(..., description="飞书 app secret")
    enabled: bool = Field(True, description="是否启用此 bot")
    chat_id: str = Field("", description="P2P 会话 ID（可选，省略时从首条消息自动获取）")
    upstream: Optional[UpstreamConfig] = Field(
        None,
        description="可选的 upstream 覆盖。省略则用全局 upstream。",
    )


class LoggingConfig(BaseModel):
    level: str = Field("INFO", pattern="^(DEBUG|INFO|WARNING|ERROR)$")
    format: str = Field("text", pattern="^(text|json)$")
    file: Optional[str] = Field(None, description="日志文件路径，省略则 stdout")


class RuntimeConfig(BaseModel):
    worker_threads: int = Field(32, description="同时处理的 LLM 调用数")
    reconnect_max_retries: int = Field(0, description="0 = 无限重连")
    reconnect_backoff_s: int = 5


class CenterConfig(BaseModel):
    """运营中心上报配置。"""
    enabled: bool = Field(True, description="是否启用心跳上报")
    url: str = Field("", description="中心 base URL，例如 https://offer.yxzrkj.cn/llm/api")
    node_id: str = Field("auto", description="节点唯一 ID。'auto' 时自动生成 (hostname-uuid6)")
    shared_secret: str = Field("", description="可选的共享密钥")
    interval_s: int = Field(30, description="心跳间隔（秒）")


# ============================================================================
# 顶层配置
# ============================================================================


class Config(BaseModel):
    upstream: UpstreamConfig
    models: List[ModelEntry] = Field(default_factory=lambda: list(DEFAULT_MODELS))
    bots: List[BotConfig] = Field(..., min_length=1)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    center: CenterConfig = Field(default_factory=CenterConfig)

    def effective_upstream_for(self, bot: BotConfig) -> UpstreamConfig:
        """获取某个 bot 的实际上游配置（bot.upstream 覆盖 global）。"""
        if bot.upstream is None:
            return self.upstream
        # 用 bot.upstream 覆盖全局
        merged = self.upstream.model_dump()
        merged.update(bot.upstream.model_dump(exclude_unset=True))
        return UpstreamConfig.model_validate(merged)


# ============================================================================
# 加载
# ============================================================================

# YAML 中 ${VAR} 或 ${VAR:-default} 占位
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value):
    """递归替换 dict / list / str 中的 ${VAR} 占位。"""
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, str):
        def replace(m):
            name = m.group(1)
            default = m.group(2)
            return os.environ.get(name, default if default is not None else m.group(0))
        return _ENV_PATTERN.sub(replace, value)
    return value


# CLI / env 也可以直接覆盖几个常用值
ENV_OVERRIDES = {
    "UPSTREAM_BASE_URL": ("upstream", "base_url"),
    "UPSTREAM_API_KEY": ("upstream", "api_key"),
    "LOG_LEVEL": ("logging", "level"),
    "CENTER_URL": ("center", "url"),
    "NODE_ID": ("center", "node_id"),
    "CENTER_SECRET": ("center", "shared_secret"),
}


def _apply_env_overrides(data: dict) -> dict:
    """对几个常用 key，env 直接覆盖（不需要 ${} 占位）。"""
    for env_var, (section, key) in ENV_OVERRIDES.items():
        v = os.environ.get(env_var)
        if v:
            data.setdefault(section, {})[key] = v
    return data


def load_config(path: Optional[str] = None) -> Config:
    """
    从 YAML 文件加载配置。
    - path 为 None 时按顺序找：env FEISHU_RELAY_BOT_CONFIG → ./config.yaml
    - 不存在文件就走纯 env 模式（适合 Docker 简单跑）
    """
    if path is None:
        path = os.environ.get("FEISHU_RELAY_BOT_CONFIG", "config.yaml")

    raw: dict = {}
    if Path(path).exists():
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        logger.info("loaded config from %s", path)
    else:
        logger.warning("config file %s not found, using env-only mode", path)

    raw = _expand_env(raw)
    raw = _apply_env_overrides(raw)
    return Config.model_validate(raw)
