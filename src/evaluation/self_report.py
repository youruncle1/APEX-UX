"""
Agent self-report collection for SEQ and SUS scores.
"""

import os
import json
from typing import Dict, Any, Optional, List

from src.agents.llm_client import get_utility_client
from src.evaluation.prompts import (
    SEQ_SCHEMA, SUS_SCHEMA,
    SEQ_JSON_INSTRUCTION, SUS_JSON_INSTRUCTION,
    SEQ_SYSTEM_PROMPT_TEMPLATE, SUS_SYSTEM_PROMPT_TEMPLATE,
    build_seq_user_prompt, build_sus_user_prompt,
)
from src.log import get_logger

logger = get_logger(__name__)


class SelfReportCollector:
    """Collect SEQ and SUS scores via agent self-assessment"""

    def __init__(self, temperature: float = 0.3):
        self.temperature = temperature
        self._client = None
        self._model = None
        self._backend = None

    def _ensure_client(self):
        """Lazy-initialize the LLM client."""
        if self._client is None:
            self._client, self._model = get_utility_client()
            self._backend = os.getenv("UTILITY_BACKEND", "openai").lower()

    def _is_qwen(self) -> bool:
        return self._backend == "qwen"

    def _clean_json_response(self, content: str) -> str:
        """Strip markdown fences and whitespace from model response."""
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            content = "\n".join(lines).strip()
        return content

    async def collect_seq(
        self,
        task_description: str,
        memory_state: Dict[str, Any],
        persona_context: str
    ) -> Optional[Dict[str, Any]]:
        """
        Collect Single Ease Question (SEQ) score.

        Returns:
            Dict with rating (float 1-7) and reasoning (str), or None on failure.
        """
        self._ensure_client()

        system_prompt = SEQ_SYSTEM_PROMPT_TEMPLATE.format(persona_context=persona_context)
        user_prompt = build_seq_user_prompt(task_description, memory_state)

        # Append JSON instruction for Qwen
        if self._is_qwen():
            user_prompt += SEQ_JSON_INSTRUCTION

        try:
            kwargs = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": self.temperature,
                "max_tokens": 512,
            }

            # Only OpenAI supports structured outputs
            if not self._is_qwen():
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "seq_response",
                        "strict": True,
                        "schema": SEQ_SCHEMA
                    }
                }

            response = await self._client.chat.completions.create(**kwargs)

            content = response.choices[0].message.content
            content = self._clean_json_response(content)
            result = json.loads(content)

            logger.debug("SEQ rating: %s/7", result["rating"])
            logger.debug("SEQ reasoning: %s", result["reasoning"][:100])

            return {
                "rating": float(result["rating"]),
                "reasoning": result["reasoning"]
            }

        except Exception as e:
            logger.debug("SEQ collection failed: %s", e)
            return None

    async def collect_sus(
        self,
        persona_context: str,
        task_summaries: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Collect System Usability Scale (SUS) score.

        Returns:
            Dict with sus_score (float 0-100), ratings (list[int]), reasoning (str),
            or None on failure.
        """
        self._ensure_client()

        system_prompt = SUS_SYSTEM_PROMPT_TEMPLATE.format(persona_context=persona_context)
        user_prompt = build_sus_user_prompt(task_summaries)

        # Append JSON instruction for Qwen
        if self._is_qwen():
            user_prompt += SUS_JSON_INSTRUCTION

        try:
            kwargs = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": self.temperature,
                "max_tokens": 1024,
            }

            if not self._is_qwen():
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "sus_response",
                        "strict": True,
                        "schema": SUS_SCHEMA
                    }
                }

            response = await self._client.chat.completions.create(**kwargs)

            content = response.choices[0].message.content
            content = self._clean_json_response(content)
            result = json.loads(content)

            # Validate ratings
            ratings = result["ratings"]
            if len(ratings) != 10 or not all(1 <= r <= 5 for r in ratings):
                logger.debug("Invalid SUS ratings: %s", ratings)
                return None

            sus_score = self._calculate_sus_score(ratings)

            logger.debug("SUS score: %.1f/100", sus_score)
            logger.debug("SUS reasoning: %s", result["reasoning"][:100])

            return {
                "sus_score": sus_score,
                "ratings": ratings,
                "reasoning": result["reasoning"]
            }

        except Exception as e:
            logger.debug("SUS collection failed: %s", e)
            return None

    def _calculate_sus_score(self, ratings: List[int]) -> float:
        """
        Calculate SUS score using standard formula.
        Odd items (1,3,5,7,9): contribution = (rating - 1)
        Even items (2,4,6,8,10): contribution = (5 - rating)
        SUS score = sum * 2.5
        """
        total = 0
        for i, rating in enumerate(ratings):
            item_num = i + 1
            if item_num % 2 == 1:
                total += (rating - 1)
            else:
                total += (5 - rating)

        return round(total * 2.5, 1)
