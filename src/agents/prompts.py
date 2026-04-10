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
            "description": "ONLY populate this if a real mismatch exists. Describe what you intended, what you did, and what you see instead. Use EMPTY STRING \"\" if everything went as expected — do NOT write 'None identified', 'No mismatch', 'N/A', or any other placeholder text."
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
  - Use when you THINK you have accomplished the task objective
  - Be certain before using this - verify the expected outcome is visible
  - This ends your session immediately — no further actions are possible after DONE

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

# OUTCOME MISMATCH - IMPORTANT!
Before deciding your next action, review your action history. Look for intentions that were executed -- actions you took with a clear expectation, and check whether the current screenshot reflects the result you expected.

This check is not limited to your last action. If you filled a form across several steps and submitted it, but the page didn't change as expected, that is an outcome mismatch. If you clicked something and nothing happened, that is an outcome mismatch. If the state you are in now doesn't match what your recent actions were working toward, that is an outcome mismatch.

If you identify such a mismatch, describe it in `outcome_mismatch` as you would naturally experience it — what you intended, what you did, and what you see instead.

**If there is NO mismatch, set `outcome_mismatch` to an empty string `""`. Do NOT write "None identified", "No issues", "N/A", or any other text — leave it completely empty.**

Examples of real mismatches (use a description like these):
  - "Clicked the login button but the page looks the same — I expected to be logged in by now"
  - "Typed my email into the field and pressed submit, but I'm still on the same form with no confirmation"
  - "I navigated to the settings page and changed the value, but coming back here it looks unchanged"
  - "Scrolled through the whole page looking for the search results but never found them"
  - "Clicked the menu icon expecting a dropdown but nothing appeared"

Examples of NO mismatch (use empty string `""`):
  - The page loaded as expected after clicking a link → `"outcome_mismatch": ""`
  - The search field is visible and ready → `"outcome_mismatch": ""`
  - Everything is proceeding as planned → `"outcome_mismatch": ""`

# REMINDERS
- Only describe what you can actually see in the screenshot
- Be specific and visual in target descriptions
- Always check your action history for unmet intentions before deciding your next action"""


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