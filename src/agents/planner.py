import os
import json
from typing import Optional
from openai import AsyncOpenAI
from dataclasses import dataclass
from .prompts import (
    SYSTEM_PROMPT_TEMPLATE,
    USER_PROMPT_TEMPLATE,
    HISTORY_WITH_SUMMARY_TEMPLATE,
    HISTORY_RECENT_ONLY_TEMPLATE,
    FIRST_STEP_TEMPLATE,
    OUTCOME_MISMATCHES_TEMPLATE
)

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
                "type": "string",
                "description": "Natural language description of the target element (required for CLICK, HOVER, DRAG_AND_DROP). Use empty string if not needed."
            },
            "input_value": {
                "type": "string",
                "description": "Text to type (required for TYPE action) or key to press (for PRESS_KEY). Use empty string if not needed."
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
    

    def __init__(
        self, 
        model: str = "gpt-4o", 
        temperature: float = 0.7,
        persona_context: str = ""
    ):
        """
        Initialize planner agent.
        """
        self.client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model
        self.temperature = temperature
        
        # Build system prompt once with persona context
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            persona_context=persona_context
        )
    
    async def plan(
        self,
        task_description: str,
        screenshot_base64: str,
        action_history: list[dict],
        action_summary: Optional[str],
        outcome_mismatches: list[str]
    ) -> Optional[PlannerAction]:
        """
        Decide the next action based on current context.
        """
        
        # Build user message 
        user_message = self._build_user_message(
            task_description=task_description,
            action_history=action_history,
            action_summary=action_summary,
            outcome_mismatches=outcome_mismatches
        )
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system", 
                        "content": self.system_prompt 
                    },
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
                target_description=action_data["target_description"] if action_data["target_description"] else None,
                input_value=action_data["input_value"] if action_data["input_value"] else None,
                reasoning=action_data["reasoning"],
                outcome_mismatch=action_data["outcome_mismatch"] if action_data["outcome_mismatch"] else None
            )
        
        except Exception as e:
            print(f"[Planner] Failed to parse action: {e}")
            return None
    
    def _build_user_message(
        self,
        task_description: str,
        action_history: list[dict],
        action_summary: Optional[str],
        outcome_mismatches: list[str]
    ) -> str:
        """
        Build the user message with dynamic context.
        
        Structure:
        1. Task description
        2. Action history (summary + recent)
        3. Outcome mismatches
        4. Current observation prompt
        5. Question
        """
        
        # Build history section
        history_section = self._build_history_section(
            action_history=action_history,
            action_summary=action_summary
        )
        
        # Add outcome mismatches if present
        if outcome_mismatches:
            mismatches_text = "\n".join([f"- {m}" for m in outcome_mismatches])
            history_section += OUTCOME_MISMATCHES_TEMPLATE.format(
                outcome_mismatches=mismatches_text
            )
        
        # Determine prompt question based on context
        if not action_history and not action_summary:
            prompt_question = "Examine the screen carefully. What is your first action?"
        else:
            prompt_question = "Based on the current state and your history, what is your next action?"
        
        # Assemble final user message
        return USER_PROMPT_TEMPLATE.format(
            task_description=task_description,
            history_section=history_section,
            prompt_question=prompt_question
        )
    
    def _build_history_section(
        self,
        action_history: list[dict],
        action_summary: Optional[str]
    ) -> str:
        """Build the history section of the user prompt"""
        
        # Case 1: First step
        if not action_history and not action_summary:
            return FIRST_STEP_TEMPLATE
        
        # Case 2: Have both summary and recent actions
        if action_summary and action_history:
            recent_actions_text = self._format_action_history(action_history)
            return HISTORY_WITH_SUMMARY_TEMPLATE.format(
                action_summary=action_summary,
                recent_actions=recent_actions_text
            )
        
        # Case 3: Have only recent actions
        if action_history:
            recent_actions_text = self._format_action_history(action_history)
            return HISTORY_RECENT_ONLY_TEMPLATE.format(
                recent_actions=recent_actions_text
            )
        
        # Case 4: Have only summary (shouldn't happen but handle it)
        if action_summary:
            return f"# PREVIOUS ACTIONS\n{action_summary}"
        
        return ""
    
    def _format_action_history(self, action_history: list[dict]) -> str:
        """Format action history list into readable text"""
        return "\n".join([
            f"Step {i+1}: {action['action_type']} - {action['reasoning']}"
            for i, action in enumerate(action_history)
        ])