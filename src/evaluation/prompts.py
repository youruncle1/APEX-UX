from typing import Any, Dict, List


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
# JSON INSTRUCTIONS (appended to prompts when backend lacks structured outputs)
# ============================================================================

SEQ_JSON_INSTRUCTION = """

You MUST respond with ONLY a valid JSON object. No markdown, no explanation, no extra text.

Required format:
{"rating": <number 1-7>, "reasoning": "<detailed explanation>"}

Respond with ONLY the JSON object, nothing else."""

SUS_JSON_INSTRUCTION = """

You MUST respond with ONLY a valid JSON object. No markdown, no explanation, no extra text.

Required format:
{"ratings": [<int>, <int>, <int>, <int>, <int>, <int>, <int>, <int>, <int>, <int>], "reasoning": "<detailed overall impression>"}

Rules:
- ratings must be an array of exactly 10 integers, each between 1 and 5
- The ratings correspond to the 10 SUS items in order
- Respond with ONLY the JSON object, nothing else."""


# ============================================================================
# SYSTEM PROMPT TEMPLATES
# ============================================================================

SEQ_SYSTEM_PROMPT_TEMPLATE = """You are reflecting on a website usability task you just completed.

# YOUR PERSONA
{persona_context}

You embodied this persona while completing the task. Now you will assess how easy or difficult the task was from this persona's perspective.

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
- Would this persona find the task straightforward?

Be honest, detailed, and specific in your assessment."""

SUS_SYSTEM_PROMPT_TEMPLATE = """You are reflecting on your overall experience using a website.

# YOUR PERSONA
{persona_context}

You embodied this persona while completing multiple tasks on the website. Now you will evaluate the overall system usability.

# SYSTEM USABILITY SCALE (SUS)
You will rate 10 statements about the website on a scale from 1 to 5:
- 1 = Strongly Disagree
- 2 = Disagree
- 3 = Neutral
- 4 = Agree
- 5 = Strongly Agree

Be honest and consider your complete experience across all tasks. Provide detailed reasoning that connects your ratings to your actual experiences."""


# ============================================================================
# PROMPT BUILDERS
# ============================================================================

def build_seq_user_prompt(
    task_description: str,
    memory_state: Dict[str, Any]
) -> str:
    """Build user prompt for SEQ collection."""

    action_history = memory_state.get("action_history", [])
    action_summary = memory_state.get("action_summary")
    outcome_mismatches = memory_state.get("outcome_mismatches", [])

    prompt_parts = []
    prompt_parts.append(f"# TASK YOU COMPLETED\n{task_description}")

    if action_summary and action_history:
        prompt_parts.append(f"\n# WHAT HAPPENED\n\n## Earlier Actions (Summary)\n{action_summary}")
        prompt_parts.append(f"\n## Recent Actions")
        for i, action in enumerate(action_history):
            prompt_parts.append(f"{i+1}. {action['action_type']}: {action['reasoning']}")
    elif action_history:
        prompt_parts.append(f"\n# WHAT HAPPENED")
        for i, action in enumerate(action_history):
            prompt_parts.append(f"{i+1}. {action['action_type']}: {action['reasoning']}")
    elif action_summary:
        prompt_parts.append(f"\n# WHAT HAPPENED\n{action_summary}")

    if outcome_mismatches:
        prompt_parts.append(f"\n# ISSUES YOU ENCOUNTERED")
        for i, mismatch in enumerate(outcome_mismatches):
            prompt_parts.append(f"{i+1}. {mismatch}")

    prompt_parts.append(
        f"\n# YOUR ASSESSMENT\n"
        f"Based on your experience completing this task, how easy or difficult was it? "
        f"Rate from 1 (very difficult) to 7 (very easy) and provide detailed reasoning."
    )

    return "\n".join(prompt_parts)


def build_sus_user_prompt(task_summaries: List[Dict[str, Any]]) -> str:
    """Build user prompt for SUS collection."""

    prompt_parts = []
    prompt_parts.append("# YOUR EXPERIENCE")
    prompt_parts.append(f"You completed {len(task_summaries)} tasks on this website. Here's what happened:\n")

    for i, task in enumerate(task_summaries, 1):
        status_emoji = "✓" if task["status"] == "VERIFIED" else "✗"
        prompt_parts.append(f"\n## Task {i}: {task['description']}")

        seq_info = f"Ease: {task['seq_score']:.1f}/7" if task.get('seq_score') else ""
        prompt_parts.append(f"Status: {status_emoji} {task['status']} | Steps: {task['steps_taken']} | {seq_info}")

        if task.get('action_history'):
            prompt_parts.append("\nActions you took:")
            for j, action in enumerate(task['action_history'], 1):
                prompt_parts.append(f"  {j}. {action['action_type']}: {action['reasoning']}")

        if task.get('outcome_mismatches'):
            prompt_parts.append("\nIssues encountered:")
            for mismatch in task['outcome_mismatches']:
                prompt_parts.append(f"  - {mismatch}")

    prompt_parts.append("\n# SYSTEM USABILITY SCALE")
    prompt_parts.append("Rate your agreement with each statement from 1 (strongly disagree) to 5 (strongly agree):\n")

    for i, item in enumerate(SUS_ITEMS, 1):
        prompt_parts.append(f"{i}. {item}")

    prompt_parts.append("\n# YOUR RESPONSE")
    prompt_parts.append("Provide an array of 10 ratings (in order) and detailed reasoning explaining your ratings.")

    return "\n".join(prompt_parts)