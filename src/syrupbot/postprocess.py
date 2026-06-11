"""把 LLM 的輸出「去 AI 化」,讓它更像真人在群裡隨手打字。

Prompt 會「漏」,所以這裡用程式做最後把關:砍掉客服腔的開頭/結尾、
去掉條列與標題格式、限制長度、必要時切成多則短訊。全部是純函式,好測試。
"""

from __future__ import annotations

import re

# Discord 單則訊息的硬性長度上限。
DISCORD_MAX_LEN = 2000

# 客服腔/助理腔的開頭,出現在句首就砍掉。
_FILLER_PREFIXES = (
    "好的,", "好的，", "好的 ", "當然,", "當然，", "當然!", "當然！",
    "沒問題,", "沒問題，", "我可以幫你", "我來幫你", "作為",
)

# 助理腔的結尾整句,出現就砍掉。
_FILLER_SUFFIX_PATTERNS = (
    r"(還有|有)?(什麼|甚麼)(我)?(可以|能)(幫|協助)(你|您|忙)?(的)?(嗎|呢)?[?？!！。\s]*$",
    r"希望(這|以上)?(對你|對您)?(有(幫助|用))[!！。\s]*$",
    r"如(果|有)(需要|問題)(隨時|都可以)?(問|找|告訴)我[!！。\s]*$",
)

# Markdown 排版痕跡(條列符號、標題井號)。
_BULLET_PATTERN = re.compile(r"^\s*([-*+]|\d+[.)、])\s+", re.MULTILINE)
_HEADING_PATTERN = re.compile(r"^\s*#{1,6}\s+", re.MULTILINE)


def humanize(text: str) -> str:
    """把回覆修得更口語、更不像 AI。"""
    result = text.strip()

    # 去掉外層包覆的引號(模型有時會把整句用「」或 "" 包起來)。
    result = _strip_wrapping_quotes(result)

    # 砍掉客服腔開頭。
    for prefix in _FILLER_PREFIXES:
        if result.startswith(prefix):
            result = result[len(prefix):].lstrip()
            break

    # 砍掉助理腔結尾。
    for pattern in _FILLER_SUFFIX_PATTERNS:
        result = re.sub(pattern, "", result).rstrip()

    # 去掉條列符號與標題井號,壓回自然句子。
    result = _BULLET_PATTERN.sub("", result)
    result = _HEADING_PATTERN.sub("", result)

    # 收斂多餘空白行。
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result


def split_message(text: str, limit: int = DISCORD_MAX_LEN) -> list[str]:
    """把過長的回覆切成多段,盡量在換行處斷開。"""
    if len(text) <= limit:
        return [text] if text else []

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def _strip_wrapping_quotes(text: str) -> str:
    pairs = (("「", "」"), ("『", "』"), ('"', '"'), ("'", "'"), ("“", "”"))
    for open_q, close_q in pairs:
        if text.startswith(open_q) and text.endswith(close_q) and len(text) >= 2:
            inner = text[1:-1]
            # 只有在內部沒有同款引號時才剝,避免破壞正常內容。
            if open_q not in inner and close_q not in inner:
                return inner.strip()
    return text
