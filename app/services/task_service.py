"""
Task Service Layer: Clarification screening, AI orchestration, and candidate options.
"""

import logging
import re
from typing import List, Literal, Optional
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.services.ai_service import AIService, AIServiceError
from app.services import prompt_service

logger = logging.getLogger("ai_vms_generator.task_service")


class TaskOption(BaseModel):
    id: int = Field(description="Option sequence number")
    title: str = Field(description="TASK TITLE / ISSUE: Meaningful sentence using the provided information")
    description: str = Field(description="DESCRIPTION: Meaningful sentences explaining the task or issue")
    style_or_focus: Optional[str] = Field(default=None, description="Optional style description")
    action_points: Optional[List[str]] = Field(default=None, description="Optional action points list")


class TaskGenerationRequest(BaseModel):
    raw_input: str = Field(
        ...,
        min_length=2,
        max_length=4000,
        description="Rough, incomplete, or poorly written task notes from the user."
    )
    num_options: int = Field(
        default=5,
        ge=4,
        le=5,
        description="Number of candidate task options to generate (4 or 5)."
    )


class TaskGenerationResponse(BaseModel):
    status: Literal["success", "clarification_needed"]
    message: str
    is_clarification_needed: bool = False
    clarification_question: Optional[str] = None
    options: List[TaskOption] = Field(default_factory=list)
    total_options: int = 0
    model: str
    is_fallback_used: bool = False


class TaskService:
    VAGUE_TERMS = {
        "bug", "fix", "help", "error", "issue", "problem", "test", "idk",
        "broken", "failed", "crash", "asdf", "hello", "hi", "do", "task"
    }

    def __init__(self, settings: Optional[Settings] = None, ai_service: Optional[AIService] = None):
        self.settings = settings or get_settings()
        self.ai_service = ai_service or AIService(self.settings)

    def screen_for_clarification(self, raw_input: str) -> Optional[str]:
        cleaned = raw_input.strip()

        # Minimum alphanumeric check
        alphanumeric = re.findall(r"[a-zA-Z0-9]", cleaned)
        if len(alphanumeric) < 3:
            return (
                "The input details are too minimal to construct a task. "
                "Please provide a brief description of the task or issue."
            )

        # Repetitive characters (e.g. 'aaaaa', '??????', '11111')
        unique_chars = set(cleaned.replace(" ", "").lower())
        if len(unique_chars) <= 2 and len(cleaned) > 4:
            return (
                "The input appears to be repetitive characters without context. "
                "Please provide a brief description of the task or issue."
            )

        # Single vague word without context (e.g. 'fix', 'bug', 'test')
        words = re.findall(r"[a-zA-Z0-9]+", cleaned.lower())
        if len(words) == 1 and words[0] in self.VAGUE_TERMS:
            return (
                f"You entered '{cleaned}', which is too vague. "
                "Could you please specify what component is affected and what behavior you expected?"
            )

        # Keyboard smash patterns (e.g. 'asdfghjkl', 'qwertyuiop')
        lower_cleaned = cleaned.lower().replace(" ", "")
        keyboard_patterns = ["qwerty", "asdfgh", "zxcvb", "ghjkl", "dfghjk", "qwer", "asdf"]
        if any(p in lower_cleaned for p in keyboard_patterns) and len(words) <= 2:
            return (
                "The input appears to be random keyboard characters. "
                "Please describe the specific issue or task you need to create."
            )

        # Non-words / no vowels
        has_vowels = bool(re.search(r"[aeiouyAEIOUY]", cleaned))
        if len(cleaned) >= 6 and not has_vowels:
            return (
                "The input does not appear to contain recognizable terms. "
                "Please describe the specific issue, feature request, or task details."
            )

        return None

    async def generate_tasks(self, request: TaskGenerationRequest) -> TaskGenerationResponse:
        raw_text = request.raw_input.strip()
        num_options = request.num_options

        # 1. Fast heuristic screening for obvious non-words
        clarification_msg = self.screen_for_clarification(raw_text)
        if clarification_msg:
            return TaskGenerationResponse(
                status="clarification_needed",
                message="The provided input is unclear or lacks sufficient context to generate a meaningful task.",
                is_clarification_needed=True,
                clarification_question=clarification_msg,
                options=[],
                total_options=0,
                model=self.settings.NVIDIA_MODEL,
                is_fallback_used=False
            )

        # 2. Call AI Service (Primary with automatic fallback to Nemotron 3.5 Bolt E4B)
        messages = prompt_service.build_messages(raw_input=raw_text, num_options=num_options)
        ai_raw_output = await self.ai_service.generate_completion(messages=messages)
        model_used = getattr(self.ai_service, "last_model_used", self.settings.NVIDIA_MODEL)
        is_fallback = getattr(self.ai_service, "is_fallback_active", False)

        data = self.ai_service.extract_json(ai_raw_output)

        # 3. Handle model-level clarification
        if data.get("needs_clarification", False):
            question = data.get("clarification_question") or (
                "The input lacks sufficient detail to formulate a task. "
                "Could you please provide additional context regarding the issue?"
            )
            return TaskGenerationResponse(
                status="clarification_needed",
                message="Additional information is required before a professional task can be generated.",
                is_clarification_needed=True,
                clarification_question=question,
                options=[],
                total_options=0,
                model=model_used,
                is_fallback_used=is_fallback
            )

        # 4. Parse options
        raw_options = data.get("options", [])
        if not isinstance(raw_options, list) or not raw_options:
            raise AIServiceError("AI model returned an empty or invalid options list.")

        validated_options: List[TaskOption] = []
        for idx, opt in enumerate(raw_options[:num_options], start=1):
            title = str(opt.get("title", f"Task Option {idx}")).strip()
            description = str(opt.get("description", "No description provided.")).strip()

            validated_options.append(
                TaskOption(
                    id=idx,
                    title=title,
                    description=description,
                    style_or_focus=opt.get("style_or_focus"),
                    action_points=opt.get("action_points")
                )
            )

        msg = f"Successfully generated {len(validated_options)} task options."
        if is_fallback:
            msg += f" (Served via fallback: {model_used})"

        return TaskGenerationResponse(
            status="success",
            message=msg,
            is_clarification_needed=False,
            clarification_question=None,
            options=validated_options,
            total_options=len(validated_options),
            model=model_used,
            is_fallback_used=is_fallback
        )
