"""
NVIDIA Nemotron AI Service with Automatic Fallback Provider.
Primary: Nemotron 3.5 Lightning (30B-A3B)
Fallback: Nemotron 3.5 Bolt E4B
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional
import httpx
from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError

from app.config import Settings, get_settings

logger = logging.getLogger("ai_vms_generator.ai_service")


class AIServiceError(Exception):
    pass


class AIServiceAuthError(AIServiceError):
    pass


class AIServiceRateLimitError(AIServiceError):
    pass


class AIServiceConnectionError(AIServiceError):
    pass


class AIServiceTimeoutError(AIServiceError):
    pass


class AIService:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._primary_client: Optional[AsyncOpenAI] = None
        self._fallback_client: Optional[AsyncOpenAI] = None
        self.last_model_used: str = self.settings.NVIDIA_MODEL
        self.is_fallback_active: bool = False

    def get_client(self, is_fallback: bool = False) -> AsyncOpenAI:
        if is_fallback:
            if not self.settings.is_fallback_configured:
                raise AIServiceAuthError("Fallback AI provider is not configured.")
            if self._fallback_client is None:
                self._fallback_client = AsyncOpenAI(
                    api_key=self.settings.active_fallback_api_key,
                    base_url=self.settings.FALLBACK_BASE_URL,
                    timeout=httpx.Timeout(self.settings.TIMEOUT_SECONDS, connect=15.0),
                    max_retries=0
                )
            return self._fallback_client

        if not self.settings.is_api_key_configured:
            raise AIServiceAuthError(
                "NVIDIA_API_KEY is not configured. "
                "Please configure a valid key from https://build.nvidia.com in your .env file."
            )
        if self._primary_client is None:
            self._primary_client = AsyncOpenAI(
                api_key=self.settings.NVIDIA_API_KEY,
                base_url=self.settings.NVIDIA_BASE_URL,
                timeout=httpx.Timeout(self.settings.TIMEOUT_SECONDS, connect=15.0),
                max_retries=0
            )
        return self._primary_client

    async def _call_model(
        self,
        client: AsyncOpenAI,
        model_name: str,
        messages: List[Dict[str, str]],
        temperature: float,
        tokens: int
    ) -> str:
        """Invokes a specific model through the provided client."""
        try:
            response = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=tokens,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}}
            )
        except Exception as body_err:
            logger.debug("Request with enable_thinking=False failed (%s), retrying standard request", body_err)
            response = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=tokens,
            )

        choice = response.choices[0]
        content = choice.message.content

        # Fallback: if content is empty but reasoning_content exists, use it
        if not content and hasattr(choice.message, "reasoning_content") and choice.message.reasoning_content:
            content = choice.message.reasoning_content

        if not content:
            raise AIServiceError(f"Received empty response from model: {model_name}")

        return content.strip()

    async def generate_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Sends completion request to primary model (Nemotron 3.5 Lightning).
        If primary model is rate-limited, unavailable, or encounters errors,
        automatically switches to secondary fallback model (Nemotron 3.5 Bolt E4B)
        without interrupting user workflow.
        """
        temp = temperature if temperature is not None else self.settings.TEMPERATURE
        tokens = max_tokens if max_tokens is not None else self.settings.MAX_TOKENS

        self.last_model_used = self.settings.NVIDIA_MODEL
        self.is_fallback_active = False

        primary_error: Optional[Exception] = None

        # 1. Attempt generation with Primary Model
        try:
            client = self.get_client(is_fallback=False)
            max_attempts = self.settings.MAX_RETRIES + 1

            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info("Calling Primary Model: %s (attempt %d/%d)", self.settings.NVIDIA_MODEL, attempt, max_attempts)
                    result = await self._call_model(client, self.settings.NVIDIA_MODEL, messages, temp, tokens)
                    self.last_model_used = self.settings.NVIDIA_MODEL
                    self.is_fallback_active = False
                    return result

                except RateLimitError as e:
                    primary_error = e
                    if attempt < max_attempts:
                        await asyncio.sleep(1.5 ** attempt)
                        continue
                    break

                except (APITimeoutError, APIConnectionError, httpx.ConnectError, httpx.NetworkError) as e:
                    primary_error = e
                    if attempt < max_attempts:
                        await asyncio.sleep(1.0 * attempt)
                        continue
                    break

                except Exception as e:
                    primary_error = e
                    break

        except Exception as e:
            primary_error = e

        # 2. Seamless Automatic Fallback: Nemotron 3.5 Bolt E4B
        if self.settings.is_fallback_configured:
            logger.warning(
                "Primary model (%s) unavailable or rate-limited (%s). "
                "Switching automatically to fallback provider (%s)...",
                self.settings.NVIDIA_MODEL,
                str(primary_error),
                self.settings.FALLBACK_MODEL
            )

            try:
                fallback_client = self.get_client(is_fallback=True)
                logger.info("Executing request on Fallback Model: %s", self.settings.FALLBACK_MODEL)
                fallback_result = await self._call_model(
                    fallback_client,
                    self.settings.FALLBACK_MODEL,
                    messages,
                    temp,
                    tokens
                )
                self.last_model_used = self.settings.FALLBACK_MODEL
                self.is_fallback_active = True
                logger.info("Successfully generated response using fallback provider: %s", self.settings.FALLBACK_MODEL)
                return fallback_result

            except Exception as fallback_err:
                logger.error("Fallback provider (%s) also failed: %s", self.settings.FALLBACK_MODEL, str(fallback_err))
                raise AIServiceError(
                    f"Both primary model ({self.settings.NVIDIA_MODEL}) and fallback ({self.settings.FALLBACK_MODEL}) failed. "
                    f"Primary: {str(primary_error)} | Fallback: {str(fallback_err)}"
                ) from fallback_err

        # If fallback is not configured or disabled, raise appropriate error
        if isinstance(primary_error, AuthenticationError):
            raise AIServiceAuthError(f"Authentication failed with NVIDIA NIM API: {str(primary_error)}") from primary_error
        if isinstance(primary_error, RateLimitError):
            raise AIServiceRateLimitError("NVIDIA API rate limit exceeded. Please try again shortly.") from primary_error
        if isinstance(primary_error, APITimeoutError):
            raise AIServiceTimeoutError(f"NVIDIA API request timed out after {self.settings.TIMEOUT_SECONDS}s.") from primary_error
        if isinstance(primary_error, (APIConnectionError, httpx.ConnectError, httpx.NetworkError)):
            raise AIServiceConnectionError(f"Failed to connect to NVIDIA NIM endpoint: {str(primary_error)}") from primary_error
        if isinstance(primary_error, APIStatusError):
            raise AIServiceError(f"NVIDIA NIM API status error ({primary_error.status_code}): {primary_error.message}") from primary_error

        raise AIServiceError(f"Primary model generation failed: {str(primary_error)}")

    @staticmethod
    def extract_json(raw_text: str) -> Dict[str, Any]:
        text = raw_text.strip()
        if "```json" in text:
            match = re.search(r"```json\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
            if match:
                text = match.group(1).strip()
        elif "```" in text:
            match = re.search(r"```\s*([\s\S]*?)\s*```", text)
            if match:
                text = match.group(1).strip()

        if not (text.startswith("{") and text.endswith("}")):
            start_idx = text.find("{")
            end_idx = text.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                text = text[start_idx:end_idx + 1]

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise AIServiceError(f"Invalid JSON returned by AI model: {str(e)}") from e
