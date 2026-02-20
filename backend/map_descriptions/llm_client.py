# Doc: Natural_Language_Code/map_descriptions/info_map_descriptions.md
"""
LLM API wrapper for map descriptions (provider-agnostic via litellm).

Copied from component_discovery pattern. Handles API calls with retry logic,
JSON response parsing, and token estimation.
"""

import json
import time

import litellm

DEFAULT_MODEL = "anthropic/claude-sonnet-4-20250514"
MAX_RETRIES = 3
RETRY_DELAY = 2.0
RATE_LIMIT_DELAY = 60.0


class LLMClient:
    """Provider-agnostic LLM client for structured JSON responses."""

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None):
        self.model = model
        self.api_key = api_key
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def query(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> dict:
        """
        Send a prompt and get a structured JSON response.

        Retries on rate limits and API errors.
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        for attempt in range(MAX_RETRIES):
            try:
                response = litellm.completion(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    api_key=self.api_key,
                )

                self.total_input_tokens += response.usage.prompt_tokens
                self.total_output_tokens += response.usage.completion_tokens

                text = response.choices[0].message.content
                return self._parse_json_response(text)

            except litellm.RateLimitError:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RATE_LIMIT_DELAY)
                    continue
                raise

            except litellm.APIError:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
                    continue
                raise

    def _parse_json_response(self, text: str) -> dict:
        """Parse JSON from LLM response, handling code blocks and wrapping."""
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                try:
                    return json.loads(text[start:end].strip())
                except json.JSONDecodeError:
                    pass

        if "```" in text:
            start = text.find("```") + 3
            newline = text.find("\n", start)
            if newline > start:
                start = newline + 1
            end = text.find("```", start)
            if end > start:
                try:
                    return json.loads(text[start:end].strip())
                except json.JSONDecodeError:
                    pass

        if "{" in text and "}" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Could not parse JSON from response: {text[:200]}...")

    def get_usage_stats(self) -> dict:
        return {
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
        }


def estimate_tokens(text: str) -> int:
    """Estimate token count (~4 chars per token for code)."""
    return len(text) // 4
