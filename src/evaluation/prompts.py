from typing import Any, Dict, List, Optional


# ============================================================================
# SUS / SEQ CONSTANTS
# ============================================================================

SUS_ITEMS = [
    "I think that I would like to use this website frequently.",
    "I found the website unnecessarily complex.",
    "I thought the website was easy to use.",
    "I think that I would need the support of a technical person to be able to use this website.",
    "I found the various functions in this website were well integrated.",
    "I thought there was too much inconsistency in this website.",
    "I would imagine that most people would learn to use this website very quickly.",
    "I found the website very cumbersome to use.",
    "I felt very confident using the website.",
    "I needed to learn a lot of things before I could get going with this website."
]

SEQ_QUESTION = "Overall, this task was:"
SEQ_SCALE = "1 (Very Difficult) to 7 (Very Easy)"
SUS_SCALE = "1 (Strongly Disagree) to 5 (Strongly Agree)"


# ============================================================================
# STRUCTURED OUTPUT SCHEMAS (OpenAI JSON schema mode)
# ============================================================================

SEQ_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "rating": {
            "type": "number",
            "minimum": 1,
            "maximum": 7,
            "description": "Task difficulty rating from 1 (very difficult) to 7 (very easy)"
        },
        "reasoning": {
            "type": "string",
            "description": "Detailed explanation of why you gave this rating"
        }
    },
    "required": ["rating", "reasoning"],
    "additionalProperties": False
}

SUS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "ratings": {
            "type": "array",
            "items": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5
            },
            "minItems": 10,
            "maxItems": 10,
            "description": "Array of 10 ratings (1-5) for each SUS item in order"
        },
        "reasoning": {
            "type": "string",
            "description": "Detailed overall impression explaining your ratings"
        }
    },
    "required": ["ratings", "reasoning"],
    "additionalProperties": False
}



# ============================================================================
# SYSTEM PROMPT TEMPLATES
# ============================================================================

SEQ_SYSTEM_PROMPT_TEMPLATE = """{persona_context}

You have just finished a task on a website and are filling out a short survey about how easy or difficult it was.

# SINGLE EASE QUESTION (SEQ)
Rate the difficulty of the task on a scale from 1 to 7:
- 1 = Very Difficult
- 2 = Difficult
- 3 = Somewhat Difficult
- 4 = Neutral
- 5 = Somewhat Easy
- 6 = Easy
- 7 = Very Easy

Consider:
- How many steps did you need?
- Did you encounter errors or unexpected outcomes?
- Did you need to backtrack or try alternative approaches?
- How confident were you in your actions?
- Whether you actually finished the task or not

Be honest and specific."""

SUS_SYSTEM_PROMPT_TEMPLATE = """{persona_context}

You have just finished using a website and are filling out a usability questionnaire about your overall experience.

# SYSTEM USABILITY SCALE (SUS)
You will rate 10 statements about the website on a scale from 1 to 5:
- 1 = Strongly Disagree
- 2 = Disagree
- 3 = Neutral
- 4 = Agree
- 5 = Strongly Agree

Be honest and consider your complete experience across all tasks. Connect your ratings to specific things that happened."""

NARRATIVE_SYSTEM_PROMPT_TEMPLATE = """{persona_context}

Describe your experience completing the task below. Write in first person."""


# ============================================================================
# ACTION PREPROCESSING
# ============================================================================

def merge_scrolls(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse consecutive same-direction scrolls into a single entry."""
    if not actions:
        return actions

    merged = []
    i = 0
    while i < len(actions):
        action = actions[i]
        if action["action_type"] in ("SCROLL_UP", "SCROLL_DOWN"):
            direction = action["action_type"]
            count = 1
            j = i + 1
            while j < len(actions) and actions[j]["action_type"] == direction:
                count += 1
                j += 1
            label = "down" if direction == "SCROLL_DOWN" else "up"
            reasoning = f"scrolled {label} {count}x" if count > 1 else action.get("reasoning", "")
            merged.append({"action_type": direction, "reasoning": reasoning})
            i = j
        else:
            merged.append(action)
            i += 1
    return merged


# ============================================================================
# PROMPT BUILDERS
# ============================================================================

def _mismatches_by_step(outcome_mismatches: List[Any]) -> Dict[int, str]:
    """Index outcome mismatches by step number. Entries may be dicts {step, mismatch} or plain strings."""
    index: Dict[int, str] = {}
    for item in outcome_mismatches:
        if isinstance(item, dict) and "step" in item:
            index[item["step"]] = item["mismatch"]
    return index


def _render_actions_with_mismatches(
    actions: List[Dict[str, Any]],
    outcome_mismatches: List[Any],
) -> List[str]:
    """Render a numbered action list with outcome mismatches inlined after the step they occurred at.

    Uses the original step_number from each action dict for mismatch matching,
    so scroll-merging (which changes list indices) does not misalign mismatches.
    """
    mismatch_index = _mismatches_by_step(outcome_mismatches)
    lines = []
    for i, action in enumerate(actions, 1):
        lines.append(f"{i}. {action['action_type']}: {action['reasoning']}")
        step_num = action.get("step_number", i)
        if step_num in mismatch_index:
            lines.append(f"ISSUE ENCOUNTERD AFTER THIS: {mismatch_index[step_num]}")
    return lines


def build_seq_user_prompt(
    task_description: str,
    actions: List[Dict[str, Any]],
    outcome_mismatches: List[Any],
    task_outcome: str,
) -> str:
    """Build user prompt for SEQ collection."""
    prompt_parts = []
    prompt_parts.append(f"# TASK YOU COMPLETED\n{task_description}")
    prompt_parts.append(f"\n# OUTCOME\n{task_outcome}")

    if actions:
        prompt_parts.append("\n# WHAT YOU DID")
        prompt_parts.extend(_render_actions_with_mismatches(actions, outcome_mismatches))
    else:
        prompt_parts.append("\n# WHAT YOU DID\nNo actions were recorded.")

    prompt_parts.append(
        "\n# YOUR ASSESSMENT\n"
        "Based on your experience completing this task, how easy or difficult was it? "
        "Rate from 1 (very difficult) to 7 (very easy) and provide detailed reasoning."
    )

    return "\n".join(prompt_parts)


def build_narrative_user_prompt(
    task_description: str,
    actions: List[Dict[str, Any]],
    outcome_mismatches: List[str],
    task_outcome: str,
    steps_taken: int,
) -> str:
    """Build user prompt for per-task narrative generation (used in SUS)."""
    prompt_parts = []
    prompt_parts.append(f"# TASK\n{task_description}")
    prompt_parts.append(f"\n# OUTCOME\n{task_outcome}")
    prompt_parts.append(f"\n# STEPS TAKEN\n{steps_taken}")

    if actions:
        prompt_parts.append("\n# YOUR ACTIONS")
        prompt_parts.extend(_render_actions_with_mismatches(actions, outcome_mismatches))
    else:
        prompt_parts.append("\n# YOUR ACTIONS\nNo actions recorded.")

    prompt_parts.append(
        "\n# YOUR TASK\n"
        "Write a narrative of 200-300 words in first person, as your persona, describing your experience "
        "completing this task. Cover what you tried chronologically, where you struggled or felt confused, "
        "any usability issues you noticed, and how the task concluded. Be specific."
    )

    return "\n".join(prompt_parts)


def build_sus_user_prompt(task_summaries: List[Dict[str, Any]]) -> str:
    """Build user prompt for SUS collection.

    Each task summary must have:
      - description: str
      - task_outcome: str  (persona-voice outcome label)
      - steps_taken: int
      - seq_score: float | None
      - outcome_mismatches: list[str]
      - actions: list[dict] | None  (scroll-merged raw list, used when no narrative)
      - narrative: str | None  (LLM-generated summary, used when actions list is too long)
    """
    prompt_parts = []
    prompt_parts.append("# YOUR EXPERIENCE")
    prompt_parts.append(f"You completed {len(task_summaries)} tasks on this website. Here is what happened:\n")

    for i, task in enumerate(task_summaries, 1):
        prompt_parts.append(f"\n## Task {i}: {task['description']}")

        seq_info = f" | Ease rating: {task['seq_score']:.1f}/7" if task.get("seq_score") else ""
        prompt_parts.append(
            f"Outcome: {task['task_outcome']} | Steps taken: {task['steps_taken']}{seq_info}"
        )

        if task.get("narrative"):
            prompt_parts.append(f"\nYour experience:\n{task['narrative']}")
        elif task.get("actions"):
            prompt_parts.append("\nWhat you did:")
            for line in _render_actions_with_mismatches(task["actions"], task.get("outcome_mismatches") or []):
                prompt_parts.append(f"  {line}")

    prompt_parts.append("\n# SYSTEM USABILITY SCALE")
    prompt_parts.append("Rate your agreement with each statement from 1 (strongly disagree) to 5 (strongly agree):\n")

    for i, item in enumerate(SUS_ITEMS, 1):
        prompt_parts.append(f"{i}. {item}")

    prompt_parts.append("\n# YOUR RESPONSE")
    prompt_parts.append("Provide an array of 10 ratings (in order) and detailed reasoning explaining your ratings.")

    return "\n".join(prompt_parts)
