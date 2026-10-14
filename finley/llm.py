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
from .pricing import WEB_SEARCH_USD, cost_usd

# Models with server-side refusal fallback. Haiku 5.5 has none, so it is left out.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


class RefusalError(RuntimeError):
    pass


class LLMClient(Protocol):
    def json(self, task: str, *, model: str, system: str, user: str, schema: dict,
             effort: str | None = None, db: DB | None = None, **ids: Any) -> dict: ...

    def search(self, task: str, *, model: str, system: str, user: str, max_uses: int = 2,
               blocked_domains: list[str] | None = None, db: DB | None = None, **ids: Any) -> dict:
        """Run a prompt with web search. Returns {"text", "results": [{"url", "title"}]}."""
        ...


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

    def search(self, task: str, *, model: str, system: str, user: str, max_uses: int = 2,
               blocked_domains: list[str] | None = None, db: DB | None = None, **ids: Any) -> dict:
        tool: dict[str, Any] = {"name": "web_search", "max_uses": max_uses,
                                # Haiku takes the basic tool; Sonnet and Opus get dynamic filtering.
                                "type": "web_search_20250305" if "haiku" in model else "web_search_20260209"}
        if blocked_domains:
            tool["blocked_domains"] = blocked_domains[:64]
        messages: list[Any] = [{"role": "user", "content": user}]
        texts: list[str] = []
        results: list[dict] = []
        for _ in range(4):  # resume if the server-side search loop pauses (and one model switch)
            try:
                response = self.client.messages.create(
                    model=model, max_tokens=8000, messages=messages, tools=[tool],
                    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    output_config={"effort": "low"},
                )
            except self._anthropic.BadRequestError:
                if "haiku" not in model:
                    raise
                # This Haiku can't take the web search tool on this account: use Sonnet instead.
                model, tool["type"] = "claude-sonnet-5-5", "web_search_20260209"
                continue
            usage = response.usage
            searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            served_by = getattr(response, "model", model) or model
            if db is not None:
                db.log_event(
                    "llm_call", model=served_by, input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens, cache_read_tokens=cache_read,
                    cost_usd=cost_usd(served_by, usage.input_tokens, usage.output_tokens, cache_read)
                    + searches * WEB_SEARCH_USD,
                    data={"task": task, "stop_reason": response.stop_reason, "web_searches": searches}, **ids,
                )
            for block in response.content:
                if block.type == "text":
                    texts.append(block.text)
                elif block.type == "web_search_tool_result" and isinstance(block.content, list):
                    results += [{"url": r.url, "title": r.title} for r in block.content]
            if response.stop_reason == "refusal":
                raise RefusalError(f"{task}: model declined the request")
            if response.stop_reason != "pause_turn":
                break
            messages = [*messages, {"role": "assistant", "content": response.content}]
        return {"text": "\n".join(texts), "results": results}


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

    def search(self, task: str, *, model: str, system: str, user: str, max_uses: int = 2,
               blocked_domains: list[str] | None = None, db: DB | None = None, **ids: Any) -> dict:
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


def _fake_source_plan(user: str) -> dict:
    return {"queries": ["family-owned plumbing company Austin TX", "emergency plumber Round Rock TX"],
            "target_titles": ["Owner", "General Manager"], "company_type": "local plumbing company",
            "exclusions": ["national franchises"],
            "local": {"is_local": False, "area": "", "osm_tags": []}}


def _fake_source_search(user: str) -> dict:
    return {"text": "Rivera Plumbing | riveraplumbing.com | family-owned Austin plumber\n"
                    "Lone Star Drains | lonestardrains.com | 24/7 emergency plumbing in Round Rock",
            "results": [{"url": "https://riveraplumbing.com/", "title": "Rivera Plumbing"},
                        {"url": "https://www.lonestardrains.com/about", "title": "Lone Star Drains"}]}


def _fake_source_pick(user: str) -> dict:
    return {"companies": [
        {"name": "Rivera Plumbing", "domain": "riveraplumbing.com", "why_fit": "Family-owned Austin plumber."},
        {"name": "Lone Star Drains", "domain": "lonestardrains.com", "why_fit": "Emergency plumbing in Round Rock."},
    ]}


def _fake_source_contact(user: str) -> dict:
    m = re.search(r'Emails found: \["([^"]+)"', user)
    return {"email": m.group(1) if m else "", "first_name": "Sam", "last_name": "Rivera",
            "title": "Owner", "company": "", "fits": True, "fit_reason": "Local plumbing company."}


def _fake_mine(user: str) -> dict:
    return {"formats": [{
        "key": "two_line_case", "name": "Two-line case study",
        "parts": ["A peer's result in one line", "Ask if they want the same"],
        "words": [20, 45], "cta": "Interest check", "use_when": "A strong, short case study.",
        "needs": ["proof"], "step": "first", "principle": "Proof in one breath is easy to act on.",
        "example": "Hi Jo,\n\nA shop like yours booked 40 more jobs last month from calls they used to miss.\n\nWant the same for yours?\n\nSam",
    }]}


DEFAULT_FAKES: dict[str, Callable[[str], dict]] = {
    "source_plan": _fake_source_plan,
    "source_search": _fake_source_search,
    "source_pick": _fake_source_pick,
    "source_contact": _fake_source_contact,
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
