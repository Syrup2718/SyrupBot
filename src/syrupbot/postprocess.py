"""把 LLM 的輸出「去 AI 化」,讓它更像真人在群裡隨手打字。

Prompt 會「漏」,所以這裡用程式做最後把關:砍掉客服腔的開頭/結尾、
去掉條列與標題格式、限制長度、必要時切成多則短訊。全部是純函式,好測試。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

# Discord 單則訊息的硬性長度上限。
DISCORD_MAX_LEN = 2000
_CHAT_SPLIT_THRESHOLD = 48
_CHAT_CHUNK_MIN = 22
_CHAT_TAIL_MIN = 8
_CHAT_SOFT_CUTS = 2
_CHAT_BREAK = re.compile(r"\n+|[。！？…]+[」』”’）)]*|[.!?]+(?=[ \t\n])")
_SPEAKER_LINE = re.compile(r"^(?:>\s*)?\*{0,2}([^:：]+?)[：:]\s*\S")
_REPLAY_NOISE = re.compile(r"[^\w]+")
_MIN_REPLAY_CHARS = 14

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
_ASIDE = re.compile(r"（([^（）\n]+)）|\(([^()\n]+)\)|(?<!\*)\*([^*\n]+)\*(?!\*)")
_STAGE_ACTION = re.compile(
    r"(?:輕輕|默默|小聲|偷偷)?(?:頂(?:了?頂)?(?:一下)?呆毛|呆毛(?:翹起|一翹)|"
    r"(?:摸(?:了?摸)?|扶(?:了?扶)?)(?:一下)?(?:星星|四角星|漿糖)?髮夾|"
    r"歪(?:了?歪)?頭|眨(?:了?眨)?眼(?:睛)?|點(?:了?點)?頭|搖(?:了?搖)?頭|"
    r"偷笑|攤手|低頭|探頭|笑(?:了?笑)?|嘆(?:了?嘆)?氣|揉眼睛)[。！…]*"
)


def humanize(text: str) -> str:
    """把回覆修得更口語、更不像 AI。"""
    result = text.strip()

    # 去掉外層包覆的引號(模型有時會把整句用「」或 "" 包起來)。
    result = _strip_wrapping_quotes(result)

    # 只移除辨識得出的動作旁白，保留正常括號補充和 Markdown 強調。
    result = _ASIDE.sub(
        lambda match: "" if _STAGE_ACTION.fullmatch(
            (match[1] or match[2] or match[3]).strip()
        ) else match[0],
        result,
    ).strip()

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


def is_chat_replay(
    text: str, *, speakers: set[str], previous_replies: Iterable[str]
) -> bool:
    """偵測帶發話者標籤的逐字稿，以及近期較長的重覆回覆。"""
    labels = {name.strip().casefold() for name in speakers}
    labels.update(("user", "assistant", "使用者", "對方"))
    for line in text.splitlines():
        match = _SPEAKER_LINE.match(line.strip())
        if match and match[1].strip(" *").casefold() in labels:
            return True

    normalized = _REPLAY_NOISE.sub("", text).casefold()
    if len(normalized) < _MIN_REPLAY_CHARS:
        return False
    for previous in previous_replies:
        old = _REPLAY_NOISE.sub("", previous).casefold()
        if len(old) < _MIN_REPLAY_CHARS:
            continue
        if old in normalized:
            return True
        match = SequenceMatcher(None, old[:DISCORD_MAX_LEN], normalized[:DISCORD_MAX_LEN])
        # 帶回一大段舊台詞，或整則只是換字重述時，讓模型重試。
        if match.find_longest_match().size >= 10 or match.ratio() >= 0.62:
            return True
    return False


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


def split_chat_reply(text: str) -> list[str]:
    """較長的口語回覆沿自然停頓拆開，網址和程式碼保留原有格式。"""
    text = text.strip()
    if len(text) < _CHAT_SPLIT_THRESHOLD or re.search(r"https?://|`", text):
        return split_message(text)

    chunks: list[str] = []
    start = 0
    for boundary in _CHAT_BREAK.finditer(text):
        end = boundary.end()
        if len(text) - end < _CHAT_TAIL_MIN:
            break
        chunk = text[start:end].strip()
        if len(chunk) < _CHAT_CHUNK_MIN:
            continue
        chunks.append(chunk)
        start = end
        if len(chunks) == _CHAT_SOFT_CUTS:
            break

    chunks.append(text[start:].strip())
    return [message for chunk in chunks for message in split_message(chunk)]


def _strip_wrapping_quotes(text: str) -> str:
    pairs = (("「", "」"), ("『", "』"), ('"', '"'), ("'", "'"), ("“", "”"))
    for open_q, close_q in pairs:
        if text.startswith(open_q) and text.endswith(close_q) and len(text) >= 2:
            inner = text[1:-1]
            # 只有在內部沒有同款引號時才剝,避免破壞正常內容。
            if open_q not in inner and close_q not in inner:
                return inner.strip()
    return text


def link_mentions(text: str, members: dict[str, int]) -> str:
    """把模型寫的「@名字」換成真正的 Discord tag(<@ID>)。

    只換 members 裡的人(最近在頻道講過話的),名字長的先換,避免「小明」
    吃掉「小明明」。全形＠也接受。對不到的 @ 原樣保留。
    """
    for name in sorted(members, key=len, reverse=True):
        if not name:
            continue
        pattern = re.compile(r"[@＠]" + re.escape(name))
        text = pattern.sub(f"<@{members[name]}>", text)
    return text


def unlink_mentions(text: str, members: dict[str, int]) -> str:
    """把 <@ID> 換回「@名字」,存進上下文時給模型看得懂的版本。"""
    names = {user_id: name for name, user_id in members.items()}

    def replace(match: re.Match[str]) -> str:
        user_id = int(match.group(1))
        return f"@{names[user_id]}" if user_id in names else match.group(0)

    return re.sub(r"<@!?(\d+)>", replace, text)
