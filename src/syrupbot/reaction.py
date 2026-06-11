"""表情符號回應的決策 —— 「要不要按、按哪個」的純邏輯。

兩種行為,對應使用者要的兩件事:
  - 自己決定要不要按(decide_self_reaction):看到一則訊息時,依機率決定要不要
    貼表情;要貼的話,優先用人設裡關鍵字對應的表情,對不到就從通用表情池隨機挑。
  - 跟著別人一起按(should_follow_reaction):別人對某訊息按了表情時,依機率決定
    要不要跟著按同一個。實際的訊息抓取與「已經有幾人按、自己按過沒」屬於 I/O,
    留在 bot.py 處理;這裡只做不碰網路的判斷,方便單獨測試。

機率與冷卻一樣用可注入的 rng 讓測試可預測(見 tests/test_reaction.py)。
"""

from __future__ import annotations

import logging
from random import Random

from .channels import ChannelPolicy
from .config import Config
from .decision import IncomingMessage
from .persona import Persona

logger = logging.getLogger(__name__)


class ReactionEngine:
    """決定漿糖要不要、以及用哪個表情回應。"""

    def __init__(
        self,
        config: Config,
        persona: Persona,
        *,
        policy: ChannelPolicy | None = None,
        rng: Random | None = None,
    ) -> None:
        self._config = config
        self._persona = persona
        self._policy = policy or ChannelPolicy(initial=config.allowed_channel_ids)
        self._rng = rng or Random()

    def decide_self_reaction(self, msg: IncomingMessage) -> str | None:
        """看到一則訊息,回傳要貼的表情;不貼則回 None。"""
        if not self._config.reactions_enabled:
            return None
        if msg.author_is_bot:
            return None
        if not self._channel_allowed(msg.channel_id):
            return None
        if not msg.content.strip():
            return None
        # 先擲骰子決定「要不要按」,沒中就不花力氣挑表情。
        if self._rng.random() >= self._config.self_reaction_probability:
            return None

        # 命中關鍵字優先;對不到就從通用表情池隨機挑一個。
        emoji = self._persona.reaction_for(msg.content)
        if emoji is not None:
            return emoji
        if self._persona.reaction_emojis:
            return self._rng.choice(self._persona.reaction_emojis)
        return None

    def should_follow_reaction(self, channel_id: int) -> bool:
        """別人按了表情,是否要(機率上)跟著按。

        這只是第一關過濾(開關、頻道、機率);還要再確認「已有足夠的人按過、
        自己沒按過」才真的按,但那需要抓訊息,交給 bot.py。
        """
        if not self._config.reactions_enabled:
            return False
        if not self._channel_allowed(channel_id):
            return False
        return self._rng.random() < self._config.follow_reaction_probability

    @property
    def follow_min_count(self) -> int:
        """至少要有幾個人按過同一個表情,漿糖才會跟著按。"""
        return self._config.follow_reaction_min_count

    def _channel_allowed(self, channel_id: int) -> bool:
        return self._policy.is_allowed(channel_id)
