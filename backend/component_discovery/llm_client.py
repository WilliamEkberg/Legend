# Doc: Natural_Language_Code/component_discovery/info_component_discovery.md
"""
LLM API wrapper for component discovery (provider-agnostic via litellm).

Handles API calls with retry logic, JSON response parsing,
and token estimation.
"""

import json
import time

import litellm

DEFAULT_MODEL = "anthropic/claude-sonnet-4-20250514"
MAX_RETRIES = 3
RETRY_DELAY = 2.0

_CREDIT_KEYWORDS = [
    "insufficient",
    "quota",
    "billing",
    "credits",
    "budget",
    "exceeded",
    "payment",
    "balance",
    "plan limit",
    "spending limit",
]


class InsufficientCreditsError(Exception):
    """Raised when the API key has run out of credits or billing quota."""

    pass


def _is_credit_error(exc: Exception) -> bool:
    """Check if an exception indicates exhausted API credits."""
    msg = str(exc).lower()
    return any(kw in msg for kw in _CREDIT_KEYWORDS)


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
        max_tokens: int = 10000,
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

            except litellm.AuthenticationError as e:
                raise InsufficientCreditsError(
                    "API authentication failed. Your API key may be invalid or expired. "
                    "Please check your API key and try again."
                ) from e

            except litellm.RateLimitError as e:
                if _is_credit_error(e):
                    raise InsufficientCreditsError(
                        "You have run out of API credits. "
                        "Please add credits to your account and try again."
                    ) from e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                raise

            except litellm.APIError as e:
                if _is_credit_error(e):
                    raise InsufficientCreditsError(
                        "You have run out of API credits. "
                        "Please add credits to your account and try again."
                    ) from e
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
