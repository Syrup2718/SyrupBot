"""載入角色設定(personas/<name>.toml),並組出 system prompt。

人設與程式碼分離:改個性只要編輯 TOML,不必動程式。要做多角色時,
新增一個 TOML 檔、把 PERSONA 環境變數指過去即可。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# personas/ 目錄位於專案根目錄(本檔在 src/syrupbot/persona.py)。
_PERSONA_DIR = Path(__file__).resolve().parents[2] / "personas"


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
    _system_prompt: str = field(default="", compare=False)

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
        return cls(
            name=data["name"],
            aliases=tuple(data.get("aliases", [])),
            identity=data.get("identity", ""),
            traits=tuple(data.get("traits", [])),
            speaking_style=data.get("speaking_style", "").strip(),
            particles=tuple(data.get("particles", [])),
            interests=tuple(data.get("interests", [])),
            examples=examples,
            forbidden=tuple(data.get("forbidden", [])),
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

    def mentioned_by_name(self, text: str) -> bool:
        """文字裡是否有人用名字或暱稱在叫這個角色。"""
        lowered = text.lower()
        return any(alias.lower() in lowered for alias in (self.name, *self.aliases))

    def matches_interest(self, text: str) -> bool:
        """文字是否命中角色的興趣關鍵字。"""
        return any(keyword in text for keyword in self.interests)
