"""
自升级：从 GitHub Releases 下载 wheel 并安装，然后退出进程。
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import urllib.request

logger = logging.getLogger("feishu-relay-bot.upgrade")

GITHUB_REPO = "Zenwh/feishu-relay-bot"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
TAG_PREFIX = "v"


def _resolve_wheel_url(version: str) -> str | None:
    """从 GitHub Releases API 解析 .whl 下载 URL。"""
    if version == "latest":
        url = f"{GITHUB_API}/latest"
    else:
        tag = f"{TAG_PREFIX}{version}"
        url = f"{GITHUB_API}/tags/{tag}"

    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        logger.error("查询 GitHub Release 失败: %s — %s", url, e)
        return None

    for asset in data.get("assets", []):
        if asset["name"].endswith(".whl"):
            return asset["browser_download_url"]

    logger.error("Release 中未找到 .whl 文件: %s", data.get("tag_name", "?"))
    return None


def upgrade_and_exit(target_version: str = "latest"):
    """
    从 GitHub Releases 下载新版本 wheel 安装，然后退出进程。
    systemd/supervisor 会自动拉起新版本。
    """
    logger.info("开始升级: target=%s", target_version)

    wheel_url = _resolve_wheel_url(target_version)
    if not wheel_url:
        return

    logger.info("下载安装: %s", wheel_url)

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--force-reinstall", wheel_url],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("升级成功，准备重启\n%s", result.stdout[-200:] if result.stdout else "")
        else:
            logger.error("升级失败 (code=%d):\n%s", result.returncode, result.stderr[-500:])
            return
    except subprocess.TimeoutExpired:
        logger.error("升级超时（120s）")
        return
    except Exception as e:
        logger.error("升级异常: %s", e)
        return

    logger.info("退出进程，等待 systemd 重启新版本 ...")
    sys.exit(0)
