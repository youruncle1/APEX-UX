import os
import json
from typing import Optional
from openai import AsyncOpenAI
from dataclasses import dataclass
from .prompts import PLANNER_PROMPT

@dataclass
class PlannerAction:
    action_type: str
    target_description: Optional[str] = None
    input_value: Optional[str] = None
    reasoning: str = ""
    outcome_mismatch: Optional[str] = None

class PlannerAgent:
    """
    Planner Agent using GPT-4o with structured outputs.
    Decides what action to take next based on screenshot and context.
    """
    
    # OPENAI JSON Schema for action response
    ACTION_SCHEMA = {
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
                "type": ["string", "null"],
                "description": "Natural language description of the target element (required for CLICK, HOVER, DRAG_AND_DROP)"
            },
            "input_value": {
                "type": ["string", "null"],
                "description": "Text to type (required for TYPE action)"
            },
            "reasoning": {
                "type": "string",
                "description": "Short explanation of why you chose this action"
            },
            "outcome_mismatch": {
                "type": ["string", "null"],
                "description": "If the previous action didn't produce the expected result, describe what went wrong"
            }
        },
        "required": ["action_type", "reasoning"],
        "additionalProperties": False
    }
    

    def __init__(self, model: str = "gpt-4o", temperature: float = 0.7):
        self.client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model
        self.temperature = temperature
    
    async def plan(
        self,
        task_description: str,
        persona_context: str,
        screenshot_base64: str,
        action_history: list[dict],
        action_summary: Optional[str],
        outcome_mismatches: list[str]
    ) -> Optional[PlannerAction]:
        """
        Decide the next action based on current context.
        Returns None if parsing fails (controller will retry).
        """
        
        # Build user message with all context
        user_message = self._build_user_message(
            task_description=task_description,
            persona_context=persona_context,
            action_history=action_history,
            action_summary=action_summary,
            outcome_mismatches=outcome_mismatches
        )
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": PLANNER_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_message},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{screenshot_base64}"
                                }
                            }
                        ]
                    }
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "action_response",
                        "strict": True,
                        "schema": self.ACTION_SCHEMA
                    }
                },
                temperature=self.temperature
            )
            
            # Parse structured output
            content = response.choices[0].message.content
            action_data = json.loads(content)
            
            return PlannerAction(
                action_type=action_data["action_type"],
                target_description=action_data.get("target_description"),
                input_value=action_data.get("input_value"),
                reasoning=action_data["reasoning"],
                outcome_mismatch=action_data.get("outcome_mismatch")
            )
        
        except Exception as e:
            print(f"[Planner] Failed to parse action: {e}")
            return None
    
    def _build_user_message(
        self,
        task_description: str,
        persona_context: str,
        action_history: list[dict],
        action_summary: Optional[str],
        outcome_mismatches: list[str]
    ) -> str:
        """Build the user message with all context"""
        
        parts = [
            f"# TASK\n{task_description}\n",
            f"# YOUR PERSONA\n{persona_context}\n"
        ]
        
        # Add action summary if exists
        if action_summary:
            parts.append(f"# PREVIOUS ACTIONS (SUMMARY)\n{action_summary}\n")
        
        # Add recent action history if exists
        if action_history:
            history_text = "\n".join([
                f"Step {i+1}: {action['action_type']} - {action['reasoning']}"
                for i, action in enumerate(action_history)
            ])
            parts.append(f"# RECENT ACTIONS\n{history_text}\n")
        
        # Add outcome mismatches if exists
        if outcome_mismatches:
            mismatches_text = "\n".join([f"- {m}" for m in outcome_mismatches])
            parts.append(f"# ISSUES ENCOUNTERED\n{mismatches_text}\n")
        
        parts.append("# CURRENT SCREEN\nSee the screenshot above.\n")
        parts.append("What is your next action?")
        
        return "\n".join(parts)