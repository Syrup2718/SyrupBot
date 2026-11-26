"""簡單的網頁後台:看各頻道的上下文、回覆判斷和別的 bot 訊息有沒有被記下。

用 discord.py 本來就依賴的 aiohttp 架一個小網頁伺服器,不另加套件。
預設只聽 127.0.0.1;放到主機上時用 SSH 通道連,或設 DASHBOARD_TOKEN。
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Callable
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

_PAGE = Path(__file__).with_name("dashboard.html")


class Dashboard:
    """包裝 aiohttp 伺服器的啟動與關閉。"""

    def __init__(self, host: str, port: int, token: str = "") -> None:
        self._host = host
        self._port = port
        self._token = token
        self._runner: web.AppRunner | None = None

    def build_app(self, state: Callable[[], dict]) -> web.Application:
        """建立網頁應用;state 每次被呼叫時回傳目前狀態。"""

        @web.middleware
        async def require_token(request: web.Request, handler):
            if self._token:
                given = request.query.get("token") or request.headers.get("X-Token", "")
                if not hmac.compare_digest(given, self._token):
                    raise web.HTTPUnauthorized(text="需要正確的 token")
            return await handler(request)

        async def page(_: web.Request) -> web.Response:
            return web.Response(
                text=_PAGE.read_text(encoding="utf-8"), content_type="text/html"
            )

        async def api_state(_: web.Request) -> web.Response:
            return web.json_response(
                state(), dumps=lambda data: json.dumps(data, ensure_ascii=False)
            )

        app = web.Application(middlewares=[require_token])
        app.router.add_get("/", page)
        app.router.add_get("/api/state", api_state)
        return app

    async def start(self, state: Callable[[], dict]) -> None:
        self._runner = web.AppRunner(self.build_app(state), access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, self._host, self._port).start()
        logger.info("後台已啟動:http://%s:%s/", self._host, self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
