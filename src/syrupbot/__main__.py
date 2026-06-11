"""進入點:組裝各層並啟動 bot(python -m syrupbot)。"""

from __future__ import annotations

import logging

from .bot import SyrupBot
from .config import Config
from .context import ConversationStore
from .decision import DecisionEngine
from .llm import LLMClient
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
    decision = DecisionEngine(config, persona, llm, context)
    reaction = ReactionEngine(config, persona)
    pipeline = ChatPipeline(persona, context, decision, llm, reaction)

    bot = SyrupBot(pipeline)
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
