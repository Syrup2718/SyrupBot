"""載入角色設定(personas/<name>.toml),並組出 system prompt。

人設與程式碼分離:改個性只要編輯 TOML,不必動程式。要做多角色時,
新增一個 TOML 檔、把 PERSONA 環境變數指過去即可。
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

# personas/ 目錄位於專案根目錄(本檔在 src/syrupbot/persona.py)。
_PERSONA_DIR = Path(__file__).resolve().parents[2] / "personas"


@dataclass(frozen=True)
class GifSticker:
    """單張 GIF 與適用情境。"""

    label: str
    keywords: tuple[str, ...]
    url: str


@dataclass(frozen=True)
class GifTheme:
    """一個可依名稱指定的 GIF 系列。"""

    label: str
    keywords: tuple[str, ...]
    stickers: tuple[GifSticker, ...]


def _is_gif_url(url: str) -> bool:
    if any(char.isspace() for char in url):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.path.lower().endswith(".gif")
    )


def _probability(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        raise ValueError("alt_style_probability 必須是 0 到 1 之間的數字")
    return float(value)


@dataclass(frozen=True)
class Persona:
    """一個角色的完整設定。"""

    name: str
    aliases: tuple[str, ...]
    identity: str
    traits: tuple[str, ...]
    speaking_style: str
    particles: tuple[str, ...]
    interests: tuple[str, ...]
    examples: tuple[tuple[str, str], ...]  # (對方說的, 角色回的)
    forbidden: tuple[str, ...]
    # 表情回應:關鍵字→表情的對應,以及對不到關鍵字時的通用表情池。
    reaction_keywords: tuple[tuple[str, str], ...] = ()
    reaction_emojis: tuple[str, ...] = ()
    _system_prompt: str = field(default="", compare=False)
    gif_themes: tuple[GifTheme, ...] = ()
    # 備用表達風格:每次回覆有一定機率抽一個,只影響那一次(參考 MaiBot)。
    alt_styles: tuple[str, ...] = ()
    alt_style_probability: float = 0.0

    @classmethod
    def load(cls, name: str) -> "Persona":
        """從 personas/<name>.toml 載入角色。"""
        path = _PERSONA_DIR / f"{name}.toml"
        if not path.exists():
            raise FileNotFoundError(f"找不到角色設定檔:{path}")

        with path.open("rb") as fh:
            data = tomllib.load(fh)

        examples = tuple(
            (ex["user"], ex["syrup"]) for ex in data.get("examples", [])
        )
        reactions = data.get("reactions", {})
        reaction_keywords = tuple(reactions.get("keywords", {}).items())
        reaction_emojis = tuple(reactions.get("emojis", []))
        gif_themes = []
        for label, theme in data.get("gifs", {}).items():
            keywords = tuple(theme.get("keywords", []))
            if "stickers" in theme:
                stickers = tuple(
                    GifSticker(item["label"], tuple(item.get("keywords", [])), item["url"])
                    for item in theme["stickers"]
                )
            else:
                # 舊人設仍可使用分類共用的 urls 與 keywords。
                stickers = tuple(
                    GifSticker(label, keywords, url) for url in theme.get("urls", [])
                )
            if not stickers or not all(
                isinstance(sticker.url, str) and _is_gif_url(sticker.url)
                for sticker in stickers
            ):
                raise ValueError(f"角色 GIF 分類 {label!r} 必須提供 HTTPS .gif 網址")
            gif_themes.append(GifTheme(label, keywords, stickers))
        return cls(
            name=data["name"],
            aliases=tuple(data.get("aliases", [])),
            identity=data.get("identity", "").strip(),
            traits=tuple(data.get("traits", [])),
            speaking_style=data.get("speaking_style", "").strip(),
            particles=tuple(data.get("particles", [])),
            interests=tuple(data.get("interests", [])),
            examples=examples,
            forbidden=tuple(data.get("forbidden", [])),
            reaction_keywords=reaction_keywords,
            reaction_emojis=reaction_emojis,
            gif_themes=tuple(gif_themes),
            alt_styles=tuple(data.get("alt_styles", [])),
            alt_style_probability=_probability(data.get("alt_style_probability", 0.0)),
        )

    @property
    def system_prompt(self) -> str:
        """把角色設定組成給 LLM 的 system prompt。"""
        parts: list[str] = [f"你是「{self.name}」,{self.identity}".rstrip("。") + "。"]

        if self.traits:
            traits = "、".join(self.traits)
            parts.append(f"你的個性:{traits}。")

        if self.speaking_style:
            parts.append("你說話的方式:\n" + self.speaking_style)

        if self.particles:
            particles = "、".join(self.particles)
            parts.append(
                f"你可以偶爾用這些語助詞讓語氣更可愛自然:{particles};"
                "但不要每句都用,自然就好,太多會很刻意、很假。"
            )

        if self.examples:
            lines = ["你平常聊天的感覺(範例,模仿這個語氣和長度):"]
            for user_says, syrup_says in self.examples:
                lines.append(f"  對方:{user_says}")
                lines.append(f"  你:{syrup_says}")
            parts.append("\n".join(lines))

        if self.forbidden:
            lines = ["你絕對不要做的事:"]
            lines.extend(f"  - {rule}" for rule in self.forbidden)
            parts.append("\n".join(lines))

        parts.append(
            "記住:你是群組裡的一個普通成員,不是助理。回覆要短、像隨手打字,"
            "看情況回一兩句就好,不用什麼都解釋。"
        )
        return "\n\n".join(parts)

    def prompt_for_turn(self, random: Callable[[], float]) -> str:
        """取得這一次回覆用的 system prompt,可能附上一個隨機的備用風格。"""
        if not self.alt_styles or random() >= self.alt_style_probability:
            return self.system_prompt
        style = self.alt_styles[int(random() * len(self.alt_styles)) % len(self.alt_styles)]
        return f"{self.system_prompt}\n\n只限這一次回覆:{style}"

    def mentioned_by_name(self, text: str) -> bool:
        """文字裡是否有人用名字或暱稱在叫這個角色。"""
        lowered = text.lower()
        return any(alias.lower() in lowered for alias in (self.name, *self.aliases))

    def matches_interest(self, text: str) -> bool:
        """文字是否命中角色的興趣關鍵字。"""
        return any(keyword in text for keyword in self.interests)

    def reaction_for(self, text: str) -> str | None:
        """依訊息內容挑一個最貼切的表情;對不到任何關鍵字時回 None。

        (對不到時要不要改用通用表情池,交給上層 ReactionEngine 決定。)
        """
        for keyword, emoji in self.reaction_keywords:
            if keyword in text:
                return emoji
        return None
