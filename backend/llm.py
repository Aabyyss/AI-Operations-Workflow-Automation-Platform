"""LLM gateway.

Two interchangeable implementations behind one tiny interface:

* MockLLM      - deterministic, rule-based. No API key, no network, no cost.
                 The demo pipeline runs fully offline and CI-friendly.
* OpenAILLM    - any OpenAI-compatible chat endpoint (OpenAI, Azure,
                 OpenRouter, local vLLM...). Used when AIOPS_MODE=live.

The agents never know which one they are talking to. Every call returns a
typed response with token accounting, so cost tracking works in both modes.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any

from . import config
from .models import UsageRecord


class LLMResponse(dict):
    """Loose dict: {"text": str, "tokens_in": int, "tokens_out": int, "model": str}"""


class BaseLLM(ABC):
    name: str = "base"

    @abstractmethod
    def chat(self, system: str, user: str, temperature: float = 0.2) -> LLMResponse:
        """Return a chat completion with token accounting."""


# ---------------------------------------------------------------------------
# Mock — deterministic heuristics keyed off the prompt content.
# The mock parses a tiny "task:" hint the agents put into their prompts so it
# can behave like a smart model without any network access.
# ---------------------------------------------------------------------------
class MockLLM(BaseLLM):
    name = "mock"

    def chat(self, system: str, user: str, temperature: float = 0.2) -> LLMResponse:
        task = ""
        m = json.loads(user.split("MOCK_TASK:", 1)[1].split("\n", 1)[0]) if "MOCK_TASK:" in user else {}
        task = m.get("task", "generic")

        if task == "intake":
            text = self._intake(user)
        elif task == "draft":
            text = self._draft(user)
        elif task == "quality":
            text = self._quality(user)
        elif task == "analyze_process":
            text = self._analyze_process(user)
        else:
            text = "ok"

        # Rough token estimate: 4 chars per token, deterministic.
        tokens_in = (len(system) + len(user)) // 4
        tokens_out = len(text) // 4
        return LLMResponse(text=text, tokens_in=tokens_in, tokens_out=tokens_out, model="mock-1")

    # -- task implementations ------------------------------------------------
    def _intake(self, user: str) -> str:
        from .mock_ai import classify_intake
        return json.dumps(classify_intake(user))

    def _draft(self, user: str) -> str:
        from .mock_ai import draft_reply
        return json.dumps(draft_reply(user))

    def _quality(self, user: str) -> str:
        from .mock_ai import quality_check
        return json.dumps(quality_check(user))

    def _analyze_process(self, user: str) -> str:
        from .mock_ai import analyze_process_notes
        return json.dumps(analyze_process_notes(user))


# ---------------------------------------------------------------------------
# Live — OpenAI-compatible chat completions via plain HTTP (no SDK needed).
# ---------------------------------------------------------------------------
class OpenAILLM(BaseLLM):
    name = "live"

    def chat(self, system: str, user: str, temperature: float = 0.2) -> LLMResponse:
        try:
            from openai import OpenAI  # optional dependency
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "AIOPS_MODE=live requires the openai package: pip install openai"
            ) from exc

        client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
        )
        resp = client.chat.completions.create(
            model=config.CHAT_MODEL,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=resp.choices[0].message.content or "",
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            model=config.CHAT_MODEL,
        )


def get_llm() -> BaseLLM:
    if config.is_live():
        return OpenAILLM()
    return MockLLM()


def cost_of(model: str, tokens_in: int, tokens_out: int) -> float:
    """USD cost for a call. Unknown models price at the cheapest tier."""
    pin, pout = config.TOKEN_PRICING.get(model, config.TOKEN_PRICING["gpt-4o-mini"])
    return tokens_in / 1e6 * pin + tokens_out / 1e6 * pout


def record_usage(llm: BaseLLM, model: str, tokens_in: int, tokens_out: int,
                 agent: str, ticket_id: str | None = None) -> UsageRecord:
    return UsageRecord(
        agent=agent,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=round(cost_of(model, tokens_in, tokens_out), 6),
        mode="live" if llm.name == "live" else "mock",
        ticket_id=ticket_id,
    )
