from typing import Optional

from src.agents.llm_client import get_utility_client
from src.config import get_settings


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

    async def maybe_compress(self):
        """Compress history if it exceeds the threshold."""
        if len(self.action_history) <= self.compression_threshold:
            return

        self._ensure_client()

        keep_count = self.compression_threshold // 2
        to_compress = self.action_history[:-keep_count]
        self.action_history = self.action_history[-keep_count:]

        actions_text = "\n".join(
            f"Step {i + 1}: {action['action_type']} - {action['reasoning']}"
            for i, action in enumerate(to_compress)
        )

        if self.action_summary:
            compress_input = f"Previous summary:\n{self.action_summary}\n\nNew actions:\n{actions_text}"
        else:
            compress_input = f"Actions:\n{actions_text}"

        system_content = (
            "You compress action histories into concise summaries. "
            "Focus on navigation milestones and key decisions. Be brief."
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": compress_input},
                ],
                temperature=0.3,
                max_tokens=200,
            )
            self.action_summary = response.choices[0].message.content.strip()
            print(f"[Memory] Compressed {len(to_compress)} actions into summary")

        except Exception as e:
            print(f"[Memory] Compression failed: {e}")

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