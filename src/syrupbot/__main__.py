"""進入點:組裝各層並啟動 bot(python -m syrupbot)。"""

from __future__ import annotations

import logging
from pathlib import Path

from .bot import SyrupBot
from .channels import ChannelPolicy
from .config import Config
from .context import ConversationStore
from .decision import DecisionEngine
from .gifs import GifPicker
from .local_model import local_model
from .llm import LLMClient
from .lull import LullWatcher
from .persona import Persona
from .pipeline import ChatPipeline
from .reaction import ReactionEngine


def main() -> None:
    config = Config.from_env()
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 由下而上組裝各層,再注入到 bot。
    persona = Persona.load(config.persona)
    context = ConversationStore(config.max_context_messages)
    llm = LLMClient(config)
    # 頻道政策是共用的可變狀態:decision、reaction、bot 指令都指向同一份。
    policy = ChannelPolicy(
        initial=config.allowed_channel_ids,
        path=Path(config.channel_policy_path),
    )
    decision = DecisionEngine(config, persona, llm, context, policy=policy)
    reaction = ReactionEngine(config, persona, policy=policy)
    gifs = GifPicker(config, persona)
    pipeline = ChatPipeline(
        persona, context, decision, llm, reaction, gifs, lull=LullWatcher(config)
    )

    bot = SyrupBot(pipeline, policy, turn_window_seconds=config.turn_window_seconds)
    with local_model(config):
        bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
