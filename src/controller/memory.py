from typing import Optional

from src.agents.llm_client import get_utility_client
from src.config import get_settings
from src.log import get_logger

logger = get_logger(__name__)


class MemoryManager:
    """Manages action history, summaries, and outcome mismatches."""

    def __init__(self, compression_threshold: Optional[int] = None):
        self.compression_threshold = compression_threshold if compression_threshold is not None else get_settings().memory_compression_threshold
        self.action_history: list[dict] = []
        self.action_summary: Optional[str] = None
        self.outcome_mismatches: list[dict] = []  # [{"step": int, "mismatch": str}]
        self.current_step = 0

        # Lazy init
        self._client = None
        self._model = None

    def _ensure_client(self):
        """Lazy-initialise the LLM client on first use."""
        if self._client is None:
            self._client, self._model = get_utility_client()

    def add_action(self, action: dict):
        """Add action to history and increment step counter."""
        self.current_step += 1
        self.action_history.append(action)

    def add_outcome_mismatch(self, mismatch: str):
        """Add outcome mismatch tied to current step."""
        if mismatch:
            self.outcome_mismatches.append({
                "step": self.current_step,
                "mismatch": mismatch,
            })

    async def maybe_compress(self, task_description: str = ""):
        """Compress history if it exceeds the threshold."""
        if len(self.action_history) <= self.compression_threshold:
            return

        self._ensure_client()

        keep_count = self.compression_threshold // 2
        to_compress = self.action_history[:-keep_count]
        self.action_history = self.action_history[-keep_count:]

        logger.debug(
            "Compressing %d actions into summary (threshold: %d)",
            len(to_compress),
            self.compression_threshold,
        )

        actions_text = "\n".join(
            f"Step {i + 1}: {action['action_type']} - {action['reasoning']}"
            for i, action in enumerate(to_compress)
        )

        if self.action_summary:
            compress_input = f"Previous summary:\n{self.action_summary}\n\nNew actions:\n{actions_text}"
        else:
            compress_input = f"Actions:\n{actions_text}"

        task_line = f"Task: {task_description}\n\n" if task_description else ""
        system_content = (
            f"{task_line}"
            "Summarize the following action history into a brief, factual chronological summary. "
            "Focus on where navigation went, what was tried, what succeeded or failed, and any dead ends. "
            "Be concise. Omit filler."
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": compress_input},
                ],
                temperature=0.3,
                max_completion_tokens=512,
            )
            self.action_summary = response.choices[0].message.content.strip()
            logger.debug("Compressed %d actions into summary", len(to_compress))

        except Exception as e:
            logger.warning("Compression failed: %s", e)

    def get_state(self) -> dict:
        """Return current memory state for database storage."""
        return {
            "action_history": self.action_history,
            "action_summary": self.action_summary,
            "outcome_mismatches": self.outcome_mismatches,
        }

    def get_outcome_mismatches_for_planner(self) -> list[str]:
        """Return outcome mismatches as plain strings for the planner prompt."""
        return [item["mismatch"] for item in self.outcome_mismatches]
