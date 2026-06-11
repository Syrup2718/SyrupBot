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

from openai import AsyncOpenAI

from .config import Config

logger = logging.getLogger(__name__)

# 要模型「接下一句」的指示(heuristic 策略:已決定要回)。
_REPLY_INSTRUCTION = (
    "\n\n以上是群組最近的對話。請以你的身分,自然地接下一句話。"
    "只輸出你要說的內容,不要加上你的名字或任何前綴。"
)

# 要模型「自己決定要不要插話」的指示(llm 策略),要求回傳 JSON。
_JUDGE_INSTRUCTION = (
    "\n\n以上是群組最近的對話。你是這個群組的成員之一,不需要每句話都回。"
    "請判斷現在這個時機你想不想加入對話。"
    "用 JSON 回答,格式為 {\"should_reply\": true/false, \"reply\": \"...\"}。"
    "如果你覺得不需要說話,should_reply 設為 false、reply 留空字串。"
    "如果要說,should_reply 設為 true,reply 放你要說的內容"
    "(自然、簡短,不要加名字前綴)。只輸出 JSON,不要有其他文字。"
)


class LLMClient:
    """針對 DeepSeek 聊天補完的精簡非同步用戶端。"""

    def __init__(self, config: Config) -> None:
        self._model = config.model
        self._decision_model = config.decision_model
        self._temperature = config.temperature
        self._client = AsyncOpenAI(
            api_key=config.deepseek_api_key,
            base_url=config.deepseek_base_url,
        )

    async def generate_reply(self, system_prompt: str, transcript: str) -> str:
        """已決定要回覆時,根據上下文產生一句回覆。"""
        response = await self._client.chat.completions.create(
            model=self._model,
            temperature=self._temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcript + _REPLY_INSTRUCTION},
            ],
        )
        return response.choices[0].message.content or ""

    async def judge_and_reply(
        self, system_prompt: str, transcript: str
    ) -> tuple[bool, str]:
        """讓模型決定要不要插話;要的話一併回傳回覆內容。

        回傳 ``(should_reply, reply)``。解析失敗時保守地當作「不回」。
        """
        response = await self._client.chat.completions.create(
            model=self._decision_model,
            temperature=0.6,  # 判斷時用較低溫度,行為穩定一點。
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcript + _JUDGE_INSTRUCTION},
            ],
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
