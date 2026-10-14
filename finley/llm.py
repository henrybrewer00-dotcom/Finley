"""Model gateway. Every Claude call goes through here so cost lands in the event log.

The user's own Anthropic key is read from ANTHROPIC_API_KEY (or an `ant auth login` profile).
Finley never ships or proxies a shared key.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Protocol

from .db import DB
from .pricing import cost_usd

# Models with server-side refusal fallback. Haiku 5.5 has none, so it is left out.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


class RefusalError(RuntimeError):
    pass


class LLMClient(Protocol):
    def json(self, task: str, *, model: str, system: str, user: str, schema: dict,
             effort: str | None = None, db: DB | None = None, **ids: Any) -> dict: ...


class AnthropicLLM:
    """Real client. Structured JSON output, a cached system prompt, cost logged per call."""

    def __init__(self, api_key: str | None = None):
        import anthropic  # imported lazily so tests and dry runs need no key

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def json(self, task: str, *, model: str, system: str, user: str, schema: dict,
             effort: str | None = None, db: DB | None = None, **ids: Any) -> dict:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if effort:
            output_config["effort"] = effort
        params: dict[str, Any] = dict(
            model=model,
            max_tokens=16000,
            # Stable instructions first and cached; the per-lead details go in the user turn.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )
        if model in FALLBACK_MODELS:
            response = self.client.beta.messages.create(
                **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        else:
            response = self.client.messages.create(**params)

        usage = response.usage
        served_by = getattr(response, "model", model) or model
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        if db is not None:
            db.log_event(
                "llm_call", model=served_by,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                cache_read_tokens=cache_read,
                cost_usd=cost_usd(served_by, usage.input_tokens, usage.output_tokens, cache_read),
                data={"task": task, "stop_reason": response.stop_reason}, **ids,
            )
        if response.stop_reason == "refusal":
            raise RefusalError(f"{task}: model declined the request")
        if response.stop_reason == "max_tokens":
            raise RuntimeError(f"{task}: output hit max_tokens before the JSON finished")
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)


class FakeLLM:
    """Deterministic stand-in for tests and `--dry-run`. Costs nothing, calls nothing.

    `responders` maps a task name to a function(user_prompt) -> dict.
    """

    def __init__(self, responders: dict[str, Callable[[str], dict]] | None = None):
        self.responders = {**DEFAULT_FAKES, **(responders or {})}
        self.calls: list[tuple[str, str]] = []

    def json(self, task: str, *, model: str, system: str, user: str, schema: dict,
             effort: str | None = None, db: DB | None = None, **ids: Any) -> dict:
        self.calls.append((task, user))
        if db is not None:
            db.log_event("llm_call", model=model, data={"task": task, "fake": True}, **ids)
        return self.responders[task](user)


def _fake_score(user: str) -> dict:
    return {
        "fit": 4, "need": 3, "timing": 3, "reachability": 4,
        "reasons": {"fit": "Matches the target industry and size.",
                    "need": "Likely has the problem the offer solves.",
                    "timing": "No fresh trigger found.",
                    "reachability": "Has a direct work email."},
        "disqualified": False, "disqualify_reason": "",
    }


def _fake_research(user: str) -> dict:
    return {"facts": [
        {"id": "f1", "text": "The company's website lists 24/7 emergency service.",
         "source": "website"},
    ], "trigger": "", "summary": "Local service business with an emergency line."}


def _fake_pick(user: str) -> dict:
    return {"format_keys": ["quick_question", "observation"], "angle": "after-hours calls",
            "why": "Little research found."}


def _fake_write(user: str) -> dict:
    m = re.search(r'"first_name": "([^"]+)"', user)
    return {
        "subject": "after-hours calls",
        "body": f"Hi {m.group(1) if m else 'there'},\n\nSaw that you run 24/7 emergency service. Who answers when a call comes in at 2am?\n\nWe help shops like yours book those jobs instead of losing them to voicemail.\n\nWorth a quick look?\n\nAlex",
        "claims": [{"text": "you run 24/7 emergency service", "fact_id": "f1"}],
    }


def _fake_judge(user: str) -> dict:
    return {"relevance": 4, "specificity": 4, "clarity": 5, "tone": 4,
            "issues": [], "verdict": "send"}


def _fake_mine(user: str) -> dict:
    return {"formats": [{
        "key": "two_line_case", "name": "Two-line case study",
        "parts": ["A peer's result in one line", "Ask if they want the same"],
        "words": [20, 45], "cta": "Interest check", "use_when": "A strong, short case study.",
        "needs": ["proof"], "step": "first", "principle": "Proof in one breath is easy to act on.",
        "example": "Hi Jo,\n\nA shop like yours booked 40 more jobs last month from calls they used to miss.\n\nWant the same for yours?\n\nSam",
    }]}


DEFAULT_FAKES: dict[str, Callable[[str], dict]] = {
    "score": _fake_score,
    "research": _fake_research,
    "pick_format": _fake_pick,
    "write": _fake_write,
    "judge": _fake_judge,
    "mine_formats": _fake_mine,
}


def make_llm(dry_run: bool = False, api_key: str | None = None) -> LLMClient:
    if dry_run or os.environ.get("FINLEY_FAKE_LLM") == "1":
        return FakeLLM()
    return AnthropicLLM(api_key=api_key or None)
