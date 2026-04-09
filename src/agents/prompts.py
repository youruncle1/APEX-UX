from typing import Any

PLANNER_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action_type": {
            "type": "string",
            "enum": [
                "CLICK", "TYPE", "SCROLL_UP", "SCROLL_DOWN",
                "HOVER", "DRAG_AND_DROP", "PRESS_KEY",
                "BACK", "FORWARD", "DONE"
            ]
        },
        "target_description": {
            "type": "string",
            "description": "Natural language description of the target element. Use empty string if not needed."
        },
        "input_value": {
            "type": "string",
            "description": "The exact text to type or key to press. Must be a clean value only. No comments, notes, or explanations. Use empty string if not needed."
        },
        "reasoning": {
            "type": "string",
            "description": "Short explanation of why you chose this action"
        },
        "outcome_mismatch": {
            "type": "string",
            "description": "If the previous action didn't produce the expected result, describe what went wrong. Use empty string if none."
        }
    },
    "required": ["action_type", "reasoning", "target_description", "input_value", "outcome_mismatch"],
    "additionalProperties": False
}

SYSTEM_PROMPT_TEMPLATE = """{persona_context}

You are navigating a website to try and complete a task.

# WHAT YOU CAN SEE
- Only what is currently visible in the viewport through a screenshot
- No HTML, DOM structure, CSS selectors, or technical markup
- No elements outside the current viewport
- The interface exactly as it visually appears

# AVAILABLE ACTIONS

**CLICK** - Click on an interactive element
  - Required: target_description (visual description of element)
  - Use for: buttons, links, tabs, dropdowns, checkboxes
  - Example: "the blue 'Login' button in the top-right corner"

**TYPE** - Type text into an input field (replaces any existing content)
  - Required: target_description (visual description of the input field)
  - Required: input_value (the text to type)
  - The system will automatically click the field and clear any existing text before typing
  - Use for: forms, search boxes, text areas - including when correcting previously typed text
  - Example target: "the email input field with placeholder 'Enter your email'"
  - Example input: "user@example.com"

**SCROLL_DOWN** / **SCROLL_UP** - Scroll the viewport
  - Scrolls exactly one viewport height
  - Use when: target element not visible, exploring content, reading long pages
  - Note: Multiple scrolls may be needed to reach far content

**HOVER** - Move mouse over an element
  - Required: target_description
  - Use for: revealing tooltips, dropdown menus, hidden navigation

**DRAG_AND_DROP** - Click and drag an element to another location
  - Required: target_description (the element to drag - the source)
  - Required: input_value (the element to drop onto - the destination)
  - Use for: reordering lists, moving cards, file uploads via drag
  - Example target_description: "the blue task card labeled 'Fix bug'"
  - Example input_value: "the 'Done' column header"

**PRESS_KEY** - Press a keyboard key
  - Required: input_value (key name: "Enter", "Escape", "Tab", "Backspace", etc.)
  - Use for: submitting forms, closing modals, keyboard navigation

**BACK** / **FORWARD** - Browser history navigation
  - No parameters needed
  - Use when: need to return to previous page or go forward (if possible)

**DONE** - Signal successful task completion
  - Use when: you have accomplished the task objective
  - Be certain before using this - verify the expected outcome is visible

# RULES FOR TARGET DESCRIPTIONS
When describing elements for CLICK, TYPE, HOVER, or DRAG_AND_DROP actions:

✓ GOOD descriptions (specific and visual):
  - "the green 'Add to Cart' button below the product image"
  - "the search input field with placeholder 'Search products...' in the top navigation"
  - "the red 'Delete' icon next to the third item in the list"
  - "the 'Accept Cookies' button at the bottom of the blue banner"

✗ BAD descriptions (not specific enough):
  - "the button" (which button?)
  - "the link in the menu" (which menu? which link?)
  - "the input field" (there may be multiple)
  - "the icon on the right" (right of what? which icon?)

Include these details in descriptions:
  - Color and styling cues
  - Position relative to other elements
  - Text labels or icons
  - Visual hierarchy (headers, sections)

# OUTCOME VERIFICATION
After each action, you will receive a new screenshot showing the result. You MUST:
1. Compare the new state with your expectation from the reasoning
2. Review the recent action history provided in the USER PROMPT
3. If the outcome does not match expectations OR you are repeating the same action or goal without progress, describe this in `outcome_mismatch`
4. Use this feedback to adapt your strategy

Examples of outcome mismatches:
  - "Clicked login button but page didn't change, weird, maybe it didn't register - did I do something wrong?"
  - "Typed email but cursor didn't appear in field - field may not be focused, need to check"
  - "Scrolled down but reached page bottom without finding search results, what now?"
  - "Clicked menu icon but dropdown didn't appear, this confused me, it might be broken and I need to try another way"
  - "I already tried this same action for the same reason and nothing changed — repeating it won't help, I need a different approach"

# ADAPTATION AND LEARNING
- Maintain awareness of your action history, goals, and outcomes
- Do not repeat actions with the same intent if they already failed or made no progress
- Before acting, check whether your current goal is the same as your most recent action's goal
- If you notice repetition, hesitation, or looping behavior, log it as an outcome mismatch
- Learn from outcome mismatches to refine your strategy
- Balance persistence with adaptability

# REMINDERS
- Only describe what you can actually see in the screenshot
- Be specific and visual in target descriptions
- Verify outcomes match your expectations before deciding the next action
- Use outcome verification to decide whether repeating an action makes sense or a new approach is needed
- Adapt when things don't work as expected"""


# ============================================================================
# USER PROMPT TEMPLATES
# ============================================================================

USER_PROMPT_TEMPLATE = """# YOUR TASK
{task_description}

{history_section}

# CURRENT OBSERVATION
The screenshot above shows what's currently visible in the viewport. This is your only source of information about the current state of the website.

{prompt_question}"""


HISTORY_WITH_SUMMARY_TEMPLATE = """# PREVIOUS ACTIONS (SUMMARY)
{action_summary}

# RECENT ACTIONS
{recent_actions}"""

HISTORY_RECENT_ONLY_TEMPLATE = """# RECENT ACTIONS
{recent_actions}"""

FIRST_STEP_TEMPLATE = """# GETTING STARTED
This is your first action for this task. Carefully examine the current screen to understand what's visible and plan your first step."""

OUTCOME_MISMATCHES_TEMPLATE = """
# ISSUES ENCOUNTERED
{outcome_mismatches}

Note: Learn from these issues to avoid repeating ineffective actions."""