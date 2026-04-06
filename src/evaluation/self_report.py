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
    NARRATIVE_SYSTEM_PROMPT_TEMPLATE,
    build_seq_user_prompt, build_sus_user_prompt, build_narrative_user_prompt,
    merge_scrolls,
)
from src.log import get_logger

logger = get_logger(__name__)

SUS_ACTION_CUMULATIVE_THRESHOLD = 400


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
        actions: List[Dict[str, Any]],
        outcome_mismatches: List[str],
        task_outcome: str,
        persona_context: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Collect Single Ease Question (SEQ) score.

        Args:
            task_description: The task the agent was asked to complete.
            actions: Full list of action records (action_type + reasoning). Will be scroll-merged internally.
            outcome_mismatches: List of mismatch strings recorded during execution.
            task_outcome: Persona-voice label describing how the task ended.
            persona_context: The persona narrative.

        Returns:
            Dict with rating (float 1-7) and reasoning (str), or None on failure.
        """
        self._ensure_client()

        merged_actions = merge_scrolls(actions)

        system_prompt = SEQ_SYSTEM_PROMPT_TEMPLATE.format(persona_context=persona_context)
        user_prompt = build_seq_user_prompt(
            task_description=task_description,
            actions=merged_actions,
            outcome_mismatches=outcome_mismatches,
            task_outcome=task_outcome,
        )

        if self._is_qwen():
            user_prompt += SEQ_JSON_INSTRUCTION

        try:
            kwargs = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": self.temperature,
                "max_tokens": 512,
            }

            if not self._is_qwen():
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "seq_response",
                        "strict": True,
                        "schema": SEQ_SCHEMA,
                    },
                }

            response = await self._client.chat.completions.create(**kwargs)

            content = response.choices[0].message.content
            content = self._clean_json_response(content)
            result = json.loads(content)

            logger.debug("SEQ rating: %s/7", result["rating"])
            logger.debug("SEQ reasoning: %s", result["reasoning"][:100])

            return {
                "rating": float(result["rating"]),
                "reasoning": result["reasoning"],
            }

        except Exception as e:
            logger.debug("SEQ collection failed: %s", e)
            return None

    async def _generate_task_narrative(
        self,
        task_description: str,
        actions: List[Dict[str, Any]],
        outcome_mismatches: List[str],
        task_outcome: str,
        steps_taken: int,
        persona_context: str,
    ) -> str:
        """Generate a ~200-300 word first-person narrative of a task for use in the SUS prompt."""
        system_prompt = NARRATIVE_SYSTEM_PROMPT_TEMPLATE.format(persona_context=persona_context)
        user_prompt = build_narrative_user_prompt(
            task_description=task_description,
            actions=actions,
            outcome_mismatches=outcome_mismatches,
            task_outcome=task_outcome,
            steps_taken=steps_taken,
        )

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            max_tokens=512,
        )
        return response.choices[0].message.content.strip()

    async def collect_sus(
        self,
        persona_context: str,
        task_summaries: List[Dict[str, Any]],
        cumulative_threshold: int = SUS_ACTION_CUMULATIVE_THRESHOLD,
    ) -> Optional[Dict[str, Any]]:
        """
        Collect System Usability Scale (SUS) score.

        Each task summary must contain:
          - description: str
          - task_outcome: str
          - steps_taken: int
          - seq_score: float | None
          - actions: list[dict]  (raw action records, scroll-merged internally)
          - outcome_mismatches: list[str]

        When the total merged action count across all tasks exceeds cumulative_threshold,
        tasks whose merged action count exceeds (cumulative_threshold / num_tasks) are
        replaced with a concise LLM-generated narrative in persona voice.

        Returns:
            Dict with sus_score (float 0-100), ratings (list[int]), reasoning (str),
            or None on failure.
        """
        self._ensure_client()

        # Merge scrolls for every task up front
        merged_summaries = [
            {**t, "actions": merge_scrolls(t.get("actions") or [])}
            for t in task_summaries
        ]

        total_merged = sum(len(t["actions"]) for t in merged_summaries)
        num_tasks = len(merged_summaries)

        if total_merged > cumulative_threshold and num_tasks > 0:
            per_task_budget = cumulative_threshold / num_tasks
            final_summaries = []
            for task in merged_summaries:
                if len(task["actions"]) > per_task_budget:
                    logger.debug(
                        "Task '%s...' has %d merged actions (budget %.0f) — generating narrative",
                        task["description"][:40],
                        len(task["actions"]),
                        per_task_budget,
                    )
                    try:
                        narrative = await self._generate_task_narrative(
                            task_description=task["description"],
                            actions=task["actions"],
                            outcome_mismatches=task.get("outcome_mismatches") or [],
                            task_outcome=task["task_outcome"],
                            steps_taken=task["steps_taken"],
                            persona_context=persona_context,
                        )
                        final_summaries.append({**task, "narrative": narrative, "actions": None})
                    except Exception as e:
                        logger.warning(
                            "Narrative generation failed for task '%s...' (using raw actions): %s",
                            task["description"][:40],
                            e,
                        )
                        final_summaries.append(task)
                else:
                    final_summaries.append(task)
        else:
            final_summaries = merged_summaries

        system_prompt = SUS_SYSTEM_PROMPT_TEMPLATE.format(persona_context=persona_context)
        user_prompt = build_sus_user_prompt(final_summaries)

        if self._is_qwen():
            user_prompt += SUS_JSON_INSTRUCTION

        try:
            kwargs = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
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
                        "schema": SUS_SCHEMA,
                    },
                }

            response = await self._client.chat.completions.create(**kwargs)

            content = response.choices[0].message.content
            content = self._clean_json_response(content)
            result = json.loads(content)

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
                "reasoning": result["reasoning"],
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
