"""
Direct LLM client for skill handlers (no MCP sampling).

Implements the same ``LLMClient.complete()`` interface that the original
MCP-based nodes use, but calls the OpenAI-compatible API directly via httpx.
"""

from __future__ import annotations

from typing import Any

import httpx

from open_storyline.utils.logging import get_logger

logger = get_logger(__name__)


class DirectLLMClient:
    """
    LLM client that calls OpenAI-compatible chat completions API directly.

    Matches the ``LLMClient`` protocol used by MCP nodes so skill handlers
    can call ``ctx.llm.complete(...)`` without any code changes.

    Uses a shared httpx.AsyncClient for connection pooling across concurrent
    requests (important for parallel sentence processing).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 300.0,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.extra_body = extra_body or {}
        self._timeout = httpx.Timeout(
            connect=10.0,
            read=timeout,
            write=10.0,
            pool=10.0,
        )
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                ),
            )
        return self._client

    async def complete(
        self,
        *,
        system_prompt: str | None,
        user_prompt: str,
        media: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
        top_p: float = 0.9,
        max_tokens: int = 2048,
        model_preferences: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        stop_sequences: list[str] | None = None,
    ) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }
        # Merge extra_body (e.g. enable_thinking, response_format, etc.)
        payload.update(self.extra_body)

        if stop_sequences:
            payload["stop"] = stop_sequences

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        client = self._get_client()
        resp = await client.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices", [])
        if not choices:
            return ""

        content = choices[0].get("message", {}).get("content", "")
        # Some models (e.g. qwen with thinking) wrap output in <think>...</think> tags
        # Strip the thinking part and return only the final answer
        if "<think>" in content and "</think>" in content:
            idx = content.rfind("</think>")
            content = content[idx + len("</think>"):].strip()
        return content


def build_llm_from_config(config: Any) -> DirectLLMClient | None:
    """
    Create a DirectLLMClient from the project Settings.

    Uses the LLM config (same model the agent uses for chat).
    Reads ``[llm] extra_body`` from config for model-specific settings
    like ``enable_thinking``.
    """
    if config is None:
        return None

    llm_cfg = getattr(config, "llm", None)
    if llm_cfg is None:
        return None

    # config.llm.timeout is typically short (30s) for agent chat latency,
    # but skill LLM calls need much longer.
    cfg_timeout = getattr(llm_cfg, "timeout", 120.0)
    read_timeout = max(cfg_timeout, 300.0)

    # extra_body from config (e.g. {"enable_thinking": false})
    extra_body = getattr(llm_cfg, "extra_body", None)
    if extra_body is not None and not isinstance(extra_body, dict):
        extra_body = None

    return DirectLLMClient(
        base_url=llm_cfg.base_url,
        api_key=llm_cfg.api_key,
        model=llm_cfg.model,
        timeout=read_timeout,
        extra_body=extra_body,
    )
