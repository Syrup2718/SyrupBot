"""管理選用的 llama-server 子程序，並等待模型就緒。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

from .config import Config

logger = logging.getLogger(__name__)
_STARTUP_TIMEOUT = 120
_PROBE_TIMEOUT = 4


def _server_ready(base_url: str, model: str) -> bool | None:
    """回傳就緒狀態；沒有服務監聽時回傳 None。"""
    opener = build_opener(ProxyHandler({}))
    try:
        health_url = f"{base_url.removesuffix('/v1')}/health"
        with opener.open(health_url, timeout=_PROBE_TIMEOUT) as health:
            if json.load(health).get("status") != "ok":
                raise RuntimeError("llama-server 健康檢查回傳非預期結果")
        with opener.open(f"{base_url}/models", timeout=_PROBE_TIMEOUT) as response:
            models = json.load(response)["data"]
    except HTTPError as exc:
        if exc.code == 503:
            exc.close()
            return False
        raise
    except TimeoutError:
        return False
    except URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return None
        raise

    if not any(item["id"] == model or model in item.get("aliases", []) for item in models):
        raise RuntimeError(f"llama-server 已就緒，但找不到模型 {model!r}")
    return True


@contextmanager
def local_model(config: Config) -> Iterator[None]:
    """按設定啟動本機模型，只清理由 bot 啟動的程序。"""
    if not config.llama_server_exe:
        yield
        return

    base_url = config.deepseek_base_url.rstrip("/")
    url = urlsplit(base_url)
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or url.port is None
        or url.path != "/v1"
        or url.query or url.fragment or url.username is not None
    ):
        raise RuntimeError("自動啟動需要 DEEPSEEK_BASE_URL=http://127.0.0.1:<port>/v1")

    ready = _server_ready(base_url, config.model)
    process = None
    try:
        if ready is None:
            exe = Path(config.llama_server_exe).resolve(strict=True)
            command = [
                str(exe), *config.llama_server_args,
                "--alias", config.model, "--host", url.hostname, "--port", str(url.port),
            ]
            # 只傳系統環境給子程序，避免帶入 bot 的憑證。
            env = os.environ.copy()
            for key in ("DISCORD_TOKEN", "DEEPSEEK_API_KEY"):
                env.pop(key, None)
            logger.info("啟動 llama-server（%s）", config.model)
            process = subprocess.Popen(
                command, cwd=exe.parent, env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
        else:
            logger.info("使用已啟動的 llama-server（%s）", base_url)

        deadline = time.monotonic() + _STARTUP_TIMEOUT
        while not ready:
            if process is not None and process.poll() is not None:
                raise RuntimeError(f"llama-server 提前結束（結束碼 {process.returncode}）")
            if time.monotonic() >= deadline:
                raise RuntimeError(f"等待 llama-server 就緒逾時（{base_url}）")
            time.sleep(0.5)
            ready = _server_ready(base_url, config.model)
        logger.info("本機模型已就緒（%s）", config.model)
        yield
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
