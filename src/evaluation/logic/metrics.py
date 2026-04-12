import math
from typing import Dict, List, Any, Optional


class MetricsCalculator:
    """Calculate usability metrics from task execution results"""

    @staticmethod
    def calculate_basic_metrics(
        result: Dict[str, Any],
        action_records: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Calculate basic metrics from task execution result.
        """

        steps_taken = result.get("steps_taken", 0)

        # Count failed actions (execution_success = False)
        failed_action_count = sum(
            1 for action in action_records
            if not action.get("execution_success", False)
        )

        # Count outcome mismatches
        memory_state = result.get("memory_state", {})
        outcome_mismatches = memory_state.get("outcome_mismatches", [])
        outcome_mismatch_count = len(outcome_mismatches)

        # Count backtracking (BACK actions)
        backtracking_count = sum(
            1 for action in action_records
            if action.get("action_type") == "BACK"
        )

        return {
            "steps_taken": steps_taken,
            "failed_action_count": failed_action_count,
            "outcome_mismatch_count": outcome_mismatch_count,
            "backtracking_count": backtracking_count
        }

    @staticmethod
    def calculate_lostness(N: int, n: int, R: int) -> Optional[float]:
        """
        Calculate Lostness score (Smith & Mosier, 1986).

        Args:
            N: Total page visits (counting revisits)
            n: Number of unique pages visited
            R: Total pages in the site (user-provided)

        Returns:
            Lostness score (0.0 = perfect navigation), or None if inputs are invalid.
        """
        if R <= 0 or n <= 0 or N <= 0:
            return None
        lostness = math.sqrt(((N / n) - 1) ** 2 + ((n / R) - 1) ** 2)
        return round(lostness, 4)