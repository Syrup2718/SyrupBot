"""從人設的 GIF 清單挑選動圖，不呼叫外部搜尋服務。"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from random import Random
from time import monotonic

from .config import Config
from .decision import IncomingMessage
from .persona import GifSticker, GifTheme, Persona

_GIF_WORD = r"(?:gif(?![a-z])|動圖|動態圖|貼圖|表情包)"
_REQUEST_ACTION = r"(?:發|丟|貼|傳|來|給我|想看)"
_REQUEST = re.compile(rf"{_REQUEST_ACTION}.{{0,24}}?{_GIF_WORD}", re.I)
_NO_GIF = re.compile(rf"(?:不要|不用|不想看|別).{{0,12}}?{_GIF_WORD}", re.I)
_NO_REQUEST = re.compile(rf"(?:不要|不用|別).{{0,4}}?{_REQUEST_ACTION}|不想看")
_SERIOUS = ("難過", "受傷", "不舒服", "心情不好", "想哭", "討厭我", "討厭你", "別笑")


@dataclass(frozen=True)
class GifSelection:
    label: str
    url: str


class GifPicker:
    def __init__(
        self, config: Config, persona: Persona, *, rng: Random | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._config = config
        self._persona = persona
        self._rng = rng or Random()
        self._clock = clock
        self._last_sent: dict[int, float] = {}

    def for_request(self, msg: IncomingMessage) -> GifSelection | None:
        if not self._config.gifs_enabled or not self._persona.gif_themes:
            return None
        text = msg.content.strip()
        addressed = (
            msg.mentions_bot or msg.is_reply_to_bot or self._persona.mentioned_by_name(text)
        )
        if not addressed or msg.addresses_others or _NO_GIF.search(text):
            return None
        theme = self._named_theme(text)
        if theme is not None and _NO_REQUEST.search(text):
            return None
        named_request = theme is not None and (
            re.search(_REQUEST_ACTION, text) is not None
            or text.casefold() in self._names(theme)
        )
        if not (named_request or _REQUEST.search(text) or re.fullmatch(_GIF_WORD, text, re.I)):
            return None
        themes = (theme,) if theme is not None else self._persona.gif_themes
        return self._choose(text, themes, fallback=True)

    def for_reply(self, msg: IncomingMessage) -> GifSelection | None:
        if not self._config.gifs_enabled or msg.addresses_others:
            return None
        last = self._last_sent.get(msg.channel_id, float("-inf"))
        if (
            self._clock() - last < self._config.gif_cooldown_seconds
            or _NO_GIF.search(msg.content)
            or _NO_REQUEST.search(msg.content)
            or any(word in msg.content for word in _SERIOUS)
        ):
            return None
        if self._rng.random() < self._config.gif_reply_probability:
            theme = self._named_theme(msg.content)
            themes = (theme,) if theme is not None else self._persona.gif_themes
            return self._choose(msg.content, themes, fallback=False)
        return None

    def record_sent(self, channel_id: int) -> None:
        self._last_sent[channel_id] = self._clock()

    @staticmethod
    def _names(theme: GifTheme) -> tuple[str, ...]:
        return tuple(name.casefold() for name in (theme.label, *theme.keywords) if name)

    def _named_theme(self, text: str) -> GifTheme | None:
        lowered = text.casefold()
        for theme in self._persona.gif_themes:
            if any(name in lowered for name in self._names(theme)):
                return theme
        return None

    def _choose(
        self, text: str, themes: tuple[GifTheme, ...], *, fallback: bool
    ) -> GifSelection | None:
        lowered = text.casefold()
        scored: list[tuple[int, GifTheme, GifSticker]] = []
        for theme in themes:
            for sticker in theme.stickers:
                matched = (
                    len(keyword) for keyword in sticker.keywords
                    if keyword and keyword.casefold() in lowered
                )
                scored.append((max(matched, default=0), theme, sticker))
        best = max((score for score, _, _ in scored), default=0)
        if not scored or (best == 0 and not fallback):
            return None
        theme, sticker = self._rng.choice([
            (theme, sticker) for score, theme, sticker in scored if score == best
        ])
        label = theme.label if theme.label == sticker.label else f"{theme.label}・{sticker.label}"
        return GifSelection(label, sticker.url)
