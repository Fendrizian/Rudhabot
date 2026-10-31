"""Check the OpenAI setup with one real request:  python -m rudhabot.llm.check ["optional question"]

Uses the same settings and .env loading as the bot. Prints the model, the outcome and the error (if any), never the
key. One request costs a fraction of a cent on small models.
"""
from __future__ import annotations

import asyncio
import os
import sys


async def _main(question: str) -> int:
    from rudhabot.settings import OPENAI_KEY_ENV_VAR, AISettings, load_env_file
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    load_env_file(os.path.join(root, ".env"))
    load_env_file(".env")
    ai = AISettings.from_env()
    key = os.environ.get(OPENAI_KEY_ENV_VAR, "").strip()
    print(f"{OPENAI_KEY_ENV_VAR}: {'set (' + str(len(key)) + ' chars)' if key else 'NOT SET'}")
    print(f"model: {ai.llm_model} · enabled: {ai.llm_enabled}")
    if not key or not ai.llm_enabled:
        print("The assistant is off, so /ask answers from local docs only.")
        return 1
    from rudhabot.llm.assistant import Assistant
    from rudhabot.llm.client import OpenAIResponsesModel
    from rudhabot.llm.knowledge import KnowledgeBase
    from rudhabot.llm.limits import RateLimiter
    model = OpenAIResponsesModel(key, ai.llm_model, timeout=ai.llm_timeout_s, reasoning_effort=ai.llm_reasoning_effort)
    print(f"reasoning effort: {model.reasoning_effort or '(not sent)'}")
    assistant = Assistant(KnowledgeBase(), model, RateLimiter(), ai.llm_max_output_tokens)
    answer = await assistant.ask(0, None, question)
    await model.close()
    print(f"source: {answer.source}")
    print(f"answer: {answer.text}")
    if answer.source != "openai":
        print(f"FAILED: {assistant.last_error}")
        return 2
    print("OK: OpenAI answered.")
    return 0


def main() -> None:
    question = " ".join(sys.argv[1:]) or "What's the mass of the sun?"
    raise SystemExit(asyncio.run(_main(question)))


if __name__ == "__main__":
    main()
