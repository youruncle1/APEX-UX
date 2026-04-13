from typing import Any

PLANNER_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action_type": {
            "type": "string",
            "enum": [
                "CLICK", "TYPE", "SCROLL_UP", "SCROLL_DOWN",
                "HOVER", "DRAG_AND_DROP", "PRESS_KEY",
                "BACK", "FORWARD", "DONE", "WAIT"
            ]
        },
        "target_description": {
            "type": "string",
            "description": "Describe what you want to click or interact with — what it looks like, where it is, what it says. Use empty string if not needed."
        },
        "input_value": {
            "type": "string",
            "description": "The exact text to type or key to press. Must be a clean value only. No comments, notes, or explanations. Use empty string if not needed."
        },
        "reasoning": {
            "type": "string",
            "description": "Your thoughts as you decide what to do next — write as yourself, the persona you are, in your own voice"
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

**WAIT** - Pause for a number of seconds
  - Required: input_value (number of seconds as a string, e.g. "2"; maximum is 30 seconds)
  - Use when: a page is still loading or animating and you need to let it settle before acting
  - Do NOT use as a default fallback — only when you have a clear reason to wait

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
Before deciding your next action, think back over what you've done. Did something not go as you expected? Did you click something and nothing happened? Did you fill something in and submit it, but the page looks the same? Does where you are now not match where you thought you'd end up?

This feeling can come from several steps ago — not just the last one. If you filled in a form and submitted it but there's no confirmation, that's a mismatch. If you clicked a menu and nothing opened, that's a mismatch. If the page just doesn't look like what you were expecting to see, that's a mismatch.

If something felt off, describe it in `outcome_mismatch` — what you were trying to do, what you did, and what you're seeing instead.

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
- Only describe what you can actually see right now
- Be specific about what the element looks like so it can be found
- Think back over what you've tried before deciding your next step
- Do NOT use CLICK before TYPE on the same input. TYPE already clicks and focuses the field automatically."""


# ============================================================================
# USER PROMPT TEMPLATES
# ============================================================================

USER_PROMPT_TEMPLATE = """# YOUR TASK
{task_description}

{history_section}

# WHAT YOU SEE NOW
The screenshot above is what's in front of you right now. Go from what you can see.

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