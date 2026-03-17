from typing import Dict, List, Any


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

        # Calculate failed action rate
        total_actions = len(action_records)
        failed_action_rate = (
            failed_action_count / total_actions
            if total_actions > 0 else 0.0
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
            "failed_action_rate": round(failed_action_rate, 4),
            "outcome_mismatch_count": outcome_mismatch_count,
            "backtracking_count": backtracking_count
        }