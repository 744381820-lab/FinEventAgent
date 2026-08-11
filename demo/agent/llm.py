from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .config import settings

# 默认熔断时长；实际以 analysis.json → llm.circuit_seconds 为准
_DEFAULT_CIRCUIT = 600


class LLMClient:
    """OpenAI-compatible chat client。配置统一来自 analysis.json + LLM_API_KEY。"""

    def __init__(self) -> None:
        self._broken_until: float = 0.0

    @property
    def base_url(self) -> str:
        return settings.llm_base_url.rstrip("/")

    @property
    def api_key(self) -> str:
        return settings.llm_api_key

    @property
    def model(self) -> str:
        return settings.llm_model

    @property
    def timeout(self) -> float:
        return float(settings.llm_timeout)

    @property
    def circuit_seconds(self) -> float:
        return float(settings.llm_cfg.get("circuit_seconds") or _DEFAULT_CIRCUIT)

    @property
    def available(self) -> bool:
        return bool(settings.llm_enabled)

    @property
    def circuit_open(self) -> bool:
        return time.monotonic() < self._broken_until

    @property
    def usable(self) -> bool:
        """总开关开启、有 Key、且熔断器未跳闸。"""
        return self.available and not self.circuit_open

    def feature_enabled(self, name: str) -> bool:
        return self.usable and settings.llm_feature(name)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        response_json: bool = False,
    ) -> str:
        if not settings.llm_cfg["enabled"]:
            raise RuntimeError("LLM 已在 analysis.json 中关闭（llm.enabled=false）")
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY 未配置（密钥只放环境变量，不写 analysis.json）")
        if self.circuit_open:
            raise RuntimeError("LLM 熔断中（此前调用失败，跳过）")

        payload: dict[str, Any] = {
            "model": self.model,
            "enable_thinking": False,
            "stream": False,
            "temperature": temperature,
            "messages": messages,
        }
        if response_json:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception:
            self._broken_until = time.monotonic() + self.circuit_seconds
            raise

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        if temperature is None:
            temperature = float(settings.llm_cfg.get("temperature_extract") or 0.1)
        text = await self.chat(messages, temperature=temperature, response_json=True)
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        return json.loads(text)


llm_client = LLMClient()
