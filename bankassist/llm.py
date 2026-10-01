"""Thin REST client for Mistral models, recording latency, tokens and cost of every call."""

import json
import time
from dataclasses import dataclass

import httpx

from . import config


@dataclass
class Usage:
    model: str
    tokens_in: int
    tokens_out: int
    ms: float

    @property
    def cost_usd(self):
        price_in, price_out = config.PRICES.get(self.model, (0.0, 0.0))
        return (self.tokens_in * price_in + self.tokens_out * price_out) / 1e6


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, base_url=config.LLM_BASE_URL, api_key=config.API_KEY, timeout=30.0):
        self.http = httpx.Client(base_url=base_url, timeout=timeout,
                                 headers={"Authorization": f"Bearer {api_key}"})

    def _post(self, path, payload, model):
        start = time.perf_counter()
        for attempt in range(3):
            resp = self.http.post(path, json=payload)
            if resp.status_code not in (429, 500, 502, 503) or attempt == 2:
                break
            time.sleep(0.5 * 2 ** attempt)  # rate limit or transient error: back off and retry
        if resp.status_code != 200:
            raise LLMError(f"{path} {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        u = body.get("usage") or {}
        usage = Usage(model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                      (time.perf_counter() - start) * 1000)
        return body, usage

    def chat(self, model, messages, tools=None, schema=None, temperature=0.0, max_tokens=600, tool_choice="auto"):
        """One chat completion. Returns (assistant message dict, Usage)."""
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        if schema:
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": "output", "strict": True, "schema": schema}}
        body, usage = self._post("/chat/completions", payload, model)
        return body["choices"][0]["message"], usage

    def chat_json(self, model, messages, schema):
        message, usage = self.chat(model, messages, schema=schema)
        return json.loads(message["content"]), usage

    def embed(self, texts):
        body, usage = self._post("/embeddings", {"model": config.EMBED_MODEL, "input": texts}, config.EMBED_MODEL)
        return [d["embedding"] for d in body["data"]], usage

    def moderate(self, messages):
        """Conversation moderation (Mistral Moderation 2: includes jailbreak detection)."""
        body, usage = self._post("/chat/moderations", {"model": config.MODERATION_MODEL, "input": [messages]},
                                 config.MODERATION_MODEL)
        return body["results"][0], usage
