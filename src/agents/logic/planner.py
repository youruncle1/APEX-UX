import json
from typing import Optional

from src.agents.models.data_models import PlannerAction
from src.agents.llm_client import get_planner_client
from src.agents.prompts import (
    PLANNER_ACTION_SCHEMA,
    SYSTEM_PROMPT_TEMPLATE,
    USER_PROMPT_TEMPLATE,
    HISTORY_WITH_SUMMARY_TEMPLATE,
    HISTORY_RECENT_ONLY_TEMPLATE,
    FIRST_STEP_TEMPLATE,
    OUTCOME_MISMATCHES_TEMPLATE,
)
from src.config import get_settings
from src.log import get_logger

logger = get_logger(__name__)


class PlannerAgent:
    """
    Planner Agent that supports multiple backends:
    - OpenAI GPT-5.4 with structured JSON schema outputs
    - Self-hosted Qwen-3-VL via llama-server (OpenAI-compatible API)

    Backend selection via PLANNER_BACKEND environment variable.
    Both backends use the OpenAI client - Qwen just points at llama-server.
    """

    def __init__(
        self,
        persona_context: str = "",
        backend: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ):
        settings = get_settings()
        self.backend = (backend or settings.planner_backend).lower()

        if self.backend not in ("openai", "local"):
            raise ValueError(
                f"Unknown planner backend: {self.backend!r}. Use 'openai' or 'local'."
            )

        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            persona_context=persona_context
        )
        self.temperature = (
            temperature if temperature is not None else settings.planner_temperature
        )
        self.client, self.model = get_planner_client(backend=self.backend, model=model)

        logger.info("Backend: %s, Model: %s", self.backend, self.model)

    async def plan(
        self,
        task_description: str,
        screenshot_base64: str,
        action_history: list[dict],
        action_summary: Optional[str],
        outcome_mismatches: list[str],
    ) -> Optional[PlannerAction]:
        """Decide the next action based on current context."""

        user_message = self._build_user_message(
            task_description=task_description,
            action_history=action_history,
            action_summary=action_summary,
            outcome_mismatches=outcome_mismatches,
        )

        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_message},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{screenshot_base64}"
                        },
                    },
                ],
            },
        ]

        content = None
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_completion_tokens": 2048,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "action_response",
                        "schema": PLANNER_ACTION_SCHEMA,
                    },
                },
            }

            response = await self.client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content

            if not content:
                logger.error("Empty response from model")
                return None

            content = content.strip()
            if content.startswith("```"):
                lines = [
                    line
                    for line in content.split("\n")
                    if not line.strip().startswith("```")
                ]
                content = "\n".join(lines).strip()

            action_data, _ = json.JSONDecoder().raw_decode(content)

            return PlannerAction(
                action_type=action_data["action_type"],
                target_description=action_data.get("target_description") or None,
                input_value=action_data.get("input_value") or None,
                reasoning=action_data.get("reasoning", ""),
                outcome_mismatch=action_data.get("outcome_mismatch") or None,
            )

        except json.JSONDecodeError as e:
            logger.error("JSON decode error: %s", e)
            logger.debug("Raw content: %s", content[:300] if content else "None")
            return None
        except KeyError as e:
            logger.error("Missing required field: %s", e)
            return None
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            return None

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _build_user_message(
        self,
        task_description: str,
        action_history: list[dict],
        action_summary: Optional[str],
        outcome_mismatches: list[str],
    ) -> str:
        history_section = self._build_history_section(action_history, action_summary)

        if outcome_mismatches:
            mismatches_text = "\n".join(f"- {m}" for m in outcome_mismatches)
            history_section += OUTCOME_MISMATCHES_TEMPLATE.format(
                outcome_mismatches=mismatches_text
            )

        prompt_question = (
            "Examine the screen carefully. What is your first action?"
            if not action_history and not action_summary
            else "Based on the current state and your history, what is your next action?"
        )

        return USER_PROMPT_TEMPLATE.format(
            task_description=task_description,
            history_section=history_section,
            prompt_question=prompt_question,
        )

    def _build_history_section(
        self,
        action_history: list[dict],
        action_summary: Optional[str],
    ) -> str:
        if not action_history and not action_summary:
            return FIRST_STEP_TEMPLATE

        if action_summary and action_history:
            return HISTORY_WITH_SUMMARY_TEMPLATE.format(
                action_summary=action_summary,
                recent_actions=self._format_action_history(action_history),
            )

        if action_history:
            return HISTORY_RECENT_ONLY_TEMPLATE.format(
                recent_actions=self._format_action_history(action_history)
            )

        # action_summary only
        return f"# PREVIOUS ACTIONS\n{action_summary}"

    @staticmethod
    def _format_action_history(action_history: list[dict]) -> str:
        lines = []
        for i, action in enumerate(action_history):
            target = action.get("target_description")
            target_str = f" on [{target}]" if target else ""
            lines.append(
                f"Step {i + 1}: {action['action_type']}{target_str} - {action['reasoning']}"
            )
        return "\n".join(lines)
