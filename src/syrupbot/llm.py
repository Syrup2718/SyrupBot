"""DeepSeek 聊天補完的封裝層。

DeepSeek 的 HTTP API 與 OpenAI 相容,因此直接重用官方 ``openai`` 套件,
只是把 base_url 指向 DeepSeek。把這層獨立出來,之後要換供應商、加重試或串流,
都只改這個檔案。

提供兩個入口:
- ``generate_reply``:已經決定要回了,單純產生一句回覆(heuristic 策略用)。
- ``judge_and_reply``:讓模型自己讀上下文決定「要不要插話」,要的話一併給出回覆
  (llm 策略用,一次呼叫同時完成判斷與生成)。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from time import monotonic

from openai import AsyncOpenAI, omit
from openai.types.chat import ChatCompletion

from .config import Config

logger = logging.getLogger(__name__)

# 兩條生成路徑共用的對話原則。對話紀錄另放在 user 訊息。
_CHAT_GUIDANCE = (
    "\n\n回覆群聊時，先回應最後一則成員訊息的重點。把較早的對話當作背景，"
    "如果有「這次要回的訊息」欄位，以該欄和發話者為準。"
    "該欄若有好幾行，把它們當作同一人接著打的幾句話，只回一次。"
    "有人問「你在幹嘛」時，直接回答你在忙什麼。"
    "對方只說早安或打招呼時，回一句招呼就好。"
    "需要理解「你」「剛剛」等指涉時再回頭看。引用前文前確認誰說了什麼，"
    "只引用紀錄中確實出現而且有關的內容。"
    "回覆只寫你這一回合要說的話。舊回覆和人設範例是參考，別照抄、續寫逐字稿或替其他人發言。"
    "分清楚對方是在說『我討厭你』，還是在問『你是不是討厭我』，各自回應那句的意思。"
    "如果對方說你講得很亂，就簡短承認剛剛沒講清楚。別拿前面不相干的綽號或玩笑來解釋。"
    "只有文字紀錄時，別假裝看見對方的氣色、表情或動作，也別猜對方為什麼做某件事。"
    "只寫要發出去的話，省略括號動作、星號旁白和角色表演。"
    "對方認真問你是不是討厭他，或明顯受傷、擔心時，先直接回答，再簡單安撫。"
    "這時收起吐槽、玩梗和表情符號，避免猜對方的動機或要求對方表態。"
    "每次優先回一兩句短句。需要補一句時，可以在自然停頓處換行。話說完就停。"
)

# 已決定要回覆，只要求模型產生聊天文字。
_REPLY_INSTRUCTION = (
    "\n\n請以角色的語氣接話。只輸出要發到群裡的內容，省略名字與前綴。"
)

# 要模型「自己決定要不要插話」的指示(llm 策略),要求回傳 JSON。
_JUDGE_INSTRUCTION = (
    "\n\n下方是群組最近的對話。你是這個群組的成員之一，不需要每句話都回。"
    "請判斷現在這個時機你想不想加入對話。"
    "用 JSON 回答,格式為 {\"should_reply\": true/false, \"reply\": \"...\"}。"
    "如果你覺得不需要說話,should_reply 設為 false、reply 留空字串。"
    "如果要說,should_reply 設為 true,reply 放你要說的內容"
    "(自然、簡短,不要加名字前綴)。只輸出 JSON,不要有其他文字。"
)


@dataclass(frozen=True)
class CompletionStats:
    """單次模型請求的數字摘要，不保存對話內容。"""

    model: str
    judge: bool
    seconds: float
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    prompt_tps: float | None
    generation_tps: float | None


def _tokens_per_second(timings: dict, key: str) -> float | None:
    value = timings.get(key)
    if type(value) in (int, float) and 0 <= value < float("inf"):
        return float(value)
    return None


class LLMClient:
    """OpenAI 相容聊天 API 的非同步用戶端。"""

    def __init__(self, config: Config) -> None:
        self.model = config.model
        self.decision_model = config.decision_model
        self.in_flight = 0
        self._last_stats: dict[int, CompletionStats] = {}
        self._temperature = config.temperature
        self._extra_body = (
            {"chat_template_kwargs": {"enable_thinking": False}}
            if config.disable_thinking
            else None
        )
        self._client = AsyncOpenAI(
            api_key=config.deepseek_api_key,
            base_url=config.deepseek_base_url,
        )

    def last_stats(self, channel_id: int) -> CompletionStats | None:
        return self._last_stats.get(channel_id)

    async def _complete(
        self,
        system_prompt: str,
        transcript: str,
        *,
        channel_id: int | None,
        latest_message: tuple[str, str] | None,
        judge: bool,
    ) -> ChatCompletion:
        model = self.decision_model if judge else self.model
        instruction = _JUDGE_INSTRUCTION if judge else _REPLY_INSTRUCTION
        conversation = transcript
        if latest_message is not None:
            author, content = latest_message
            conversation = json.dumps(
                {
                    "近期群聊紀錄": transcript,
                    "這次要回的訊息": {"發話者": author, "內容": content},
                },
                ensure_ascii=False,
            )
        start = monotonic()
        self.in_flight += 1
        try:
            response = await self._client.chat.completions.create(
                model=model,
                temperature=0.6 if judge else self._temperature,
                response_format={"type": "json_object"} if judge else omit,
                extra_body=self._extra_body,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt + _CHAT_GUIDANCE + instruction,
                    },
                    {"role": "user", "content": conversation},
                ],
            )
        finally:
            self.in_flight -= 1

        if channel_id is not None:
            timings = getattr(response, "timings", None)
            timings = timings if isinstance(timings, dict) else {}
            usage = response.usage
            self._last_stats[channel_id] = CompletionStats(
                model=response.model or model,
                judge=judge,
                seconds=monotonic() - start,
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
                total_tokens=usage.total_tokens if usage else None,
                prompt_tps=_tokens_per_second(timings, "prompt_per_second"),
                generation_tps=_tokens_per_second(timings, "predicted_per_second"),
            )
        return response

    async def generate_reply(
        self,
        system_prompt: str,
        transcript: str,
        *,
        channel_id: int | None = None,
        latest_message: tuple[str, str] | None = None,
    ) -> str:
        """已決定要回覆時,根據上下文產生一句回覆。"""
        response = await self._complete(
            system_prompt, transcript, channel_id=channel_id,
            latest_message=latest_message, judge=False,
        )
        return response.choices[0].message.content or ""

    async def judge_and_reply(
        self,
        system_prompt: str,
        transcript: str,
        *,
        channel_id: int | None = None,
        latest_message: tuple[str, str] | None = None,
    ) -> tuple[bool, str]:
        """讓模型決定要不要插話;要的話一併回傳回覆內容。

        回傳 ``(should_reply, reply)``。解析失敗時保守地當作「不回」。
        """
        response = await self._complete(
            system_prompt, transcript, channel_id=channel_id,
            latest_message=latest_message, judge=True,
        )
        raw = response.choices[0].message.content or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("judge_and_reply 回傳的不是合法 JSON:%r", raw)
            return False, ""

        should_reply = bool(data.get("should_reply"))
        reply = str(data.get("reply") or "")
        return (should_reply and bool(reply.strip())), reply
