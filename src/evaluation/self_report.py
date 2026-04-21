import json
from typing import Dict, Any, Optional, List

from src.agents.llm_client import get_utility_client
from src.evaluation.models.api_models import SEQResponse, SUSCollectionResult
from src.evaluation.prompts import (
    SEQ_SCHEMA,
    SUS_SCHEMA,
    SEQ_SYSTEM_PROMPT_TEMPLATE,
    SUS_SYSTEM_PROMPT_TEMPLATE,
    NARRATIVE_SYSTEM_PROMPT_TEMPLATE,
    build_seq_user_prompt,
    build_sus_user_prompt,
    build_narrative_user_prompt,
    merge_scrolls,
)
from src.config import get_settings
from src.log import get_logger

logger = get_logger(__name__)


class SelfReportCollector:
    """
    Collect SEQ and SUS scores via agent self-assessment.
    """

    def __init__(self, temperature: Optional[float] = None):
        if temperature is not None:
            self.temperature = temperature
        else:
            self.temperature = get_settings().planner_temperature
        self._client = None
        self._model = None

    def _ensure_client(self):
        """
        Lazy-initialize the LLM client only on first call.
        """
        if self._client is None:
            self._client, self._model = get_utility_client()

    def _clean_json_response(self, content: str) -> str:
        """
        Strip markdown fences and whitespace from model response (sanitization).
        """
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            content = "\n".join(lines).strip()
        return content

    async def collect_seq(
        self,
        task_description: str,
        actions: List[Dict[str, Any]],
        outcome_mismatches: List[Any],
        task_outcome: str,
        persona_context: str,
    ) -> Optional[SEQResponse]:
        """
        Collect Single Ease Question (SEQ) score from an agent after a task.

        Args:
            task_description: The task description.
            actions: Full list of action records (action_type + reasoning).
            outcome_mismatches: List of {step, mismatch} dicts from memory state.
            task_outcome: Label narrative describing how the task ended.
            persona_context: Persona narrative.

        Returns:
            SEQResponse class or None if fails.
        """
        # init LLM client
        self._ensure_client()

        # Merge consecituve scroll events of the same type into one to lower context
        merged_actions = merge_scrolls(actions)

        # Inject persona narrative into seq system prompt template
        system_prompt = SEQ_SYSTEM_PROMPT_TEMPLATE.format(
            persona_context=persona_context
        )
        # Build user prompt with task desc., actions (merged), outcome  mms and task success label
        user_prompt = build_seq_user_prompt(
            task_description=task_description,
            actions=merged_actions,
            outcome_mismatches=outcome_mismatches,
            task_outcome=task_outcome,
        )

        # Get SEQ assessment from LLM (3 attempts)
        kwargs = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_completion_tokens": 2048,
            "response_format": {
                "type": "json_schema",  # enforce json schema in LLM response
                "json_schema": {
                    "name": "seq_response",
                    "schema": SEQ_SCHEMA,
                },
            },
        }

        for attempt in range(1, 4):
            try:
                response = await self._client.chat.completions.create(**kwargs)

                content = response.choices[0].message.content
                content = self._clean_json_response(content)  # sanitize response
                result, _ = json.JSONDecoder().raw_decode(content)
                seq = SEQResponse.model_validate(
                    result
                )  # validate JSON response schema

                logger.debug("SEQ rating: %s/7", seq.rating)
                logger.debug("SEQ reasoning: %s", seq.reasoning[:100])

                return seq

            except Exception as e:
                logger.warning("SEQ collection failed (attempt %d/3): %s", attempt, e)

        return None

    async def _generate_task_narrative(
        self,
        task_description: str,
        actions: List[Dict[str, Any]],
        outcome_mismatches: List[Any],
        task_outcome: str,
        steps_taken: int,
        persona_context: str,
    ) -> str:
        """
        Helper for collect_sus.
        Generates a ~200-300 word first-person narrative of a task for use in the SUS prompt.
        Used to reduce context window when tasks' total step(action) count exceeds threshold.
        """
        # Inject persona narrative into system prompt template
        system_prompt = NARRATIVE_SYSTEM_PROMPT_TEMPLATE.format(
            persona_context=persona_context
        )
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
            max_completion_tokens=2048,
        )
        return response.choices[0].message.content.strip()

    async def collect_sus(
        self,
        persona_context: str,
        task_summaries: List[Dict[str, Any]],
        cumulative_threshold: int = -1,
    ) -> Optional[SUSCollectionResult]:
        """
        Collect System Usability Scale (SUS) score from an agent after all tasks.

        When the total merged action count across all tasks exceeds cumulative_threshold,
        tasks whose merged action count exceeds (cumulative_threshold / num_tasks) are
        replaced with a ~200-300 word LLM-generated summarization narrative in persona voice.

        Args:
            persona_context: Persona narrative
            task_summaries: List of task summary dicts (see fields above).
            cumulative_threshold: Max total merged actions before narrative compression kicks in.
                Defaults to settings value when -1.

        Returns:
            SUSCollectionResult class or None if fail.
        """
        # Fall back to settings value if caller did not pass a threshold
        if cumulative_threshold == -1:
            cumulative_threshold = get_settings().sus_action_cumulative_threshold
        # init LLM client
        self._ensure_client()

        # Merge consecutive scroll actions of the same type into one to lower context
        merged_summaries = [
            {**t, "actions": merge_scrolls(t.get("actions") or [])}
            for t in task_summaries
        ]

        total_merged = sum(len(t["actions"]) for t in merged_summaries)
        num_tasks = len(merged_summaries)

        # Replace overly long tasks with a summarised narrative
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
                        # Summarize task into a short first-person narrative
                        narrative = await self._generate_task_narrative(
                            task_description=task["description"],
                            actions=task["actions"],
                            outcome_mismatches=task.get("outcome_mismatches") or [],
                            task_outcome=task["task_outcome"],
                            steps_taken=task["steps_taken"],
                            persona_context=persona_context,
                        )
                        final_summaries.append(
                            {**task, "narrative": narrative, "actions": None}
                        )
                    except Exception as e:
                        # Narrative generation failed, fall back to raw actions
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

        # Inject persona narrative into sus system prompt template
        system_prompt = SUS_SYSTEM_PROMPT_TEMPLATE.format(
            persona_context=persona_context
        )
        # Build user prompt with all task summaries (or narratives)
        user_prompt = build_sus_user_prompt(final_summaries)

        # Get SUS assessment from LLM (3 attempts)
        kwargs = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_completion_tokens": 2048,
            "response_format": {
                "type": "json_schema",  # enforce json schema in LLM response
                "json_schema": {
                    "name": "sus_response",
                    "schema": SUS_SCHEMA,
                },
            },
        }

        for attempt in range(1, 4):
            try:
                response = await self._client.chat.completions.create(**kwargs)

                content = response.choices[0].message.content
                content = self._clean_json_response(content)  # sanitize response
                result, _ = json.JSONDecoder().raw_decode(content)

                ratings = result["ratings"]
                # SUS requires exactly 10 items each rated 1-5
                if len(ratings) != 10 or not all(1 <= r <= 5 for r in ratings):
                    logger.warning(
                        "Invalid SUS ratings (attempt %d/3): %s", attempt, ratings
                    )
                    continue

                sus_score = self._calculate_sus_score(ratings)
                sus = (
                    SUSCollectionResult.model_validate(  # validate JSON response schema
                        {
                            "sus_score": sus_score,
                            "ratings": ratings,
                            "reasoning": result["reasoning"],
                        }
                    )
                )

                logger.debug("SUS score: %.1f/100", sus.sus_score)
                logger.debug("SUS reasoning: %s", sus.reasoning[:100])

                return sus

            except Exception as e:
                logger.warning("SUS collection failed (attempt %d/3): %s", attempt, e)

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
                total += rating - 1
            else:
                total += 5 - rating

        return round(total * 2.5, 1)
