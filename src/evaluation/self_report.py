"""
Agent self-report collection for SEQ and SUS scores.

Uses GPT-4o-mini to collect subjective usability ratings from the agent
based on its experience during task execution.
"""

import os
import json
from typing import Dict, Any, Optional
from openai import AsyncOpenAI


class SelfReportCollector:
    """Collect SEQ and SUS scores via agent self-assessment"""
    
    # JSON Schema for SEQ response
    SEQ_SCHEMA = {
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
                "description": "Brief explanation of why you gave this rating"
            }
        },
        "required": ["rating", "reasoning"],
        "additionalProperties": False
    }
    
    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.3):
        """
        Initialize self-report collector.
        
        Args:
            model: OpenAI model to use for self-assessment
            temperature: Sampling temperature (lower = more consistent)
        """
        self._client = None
        self.model = model
        self.temperature = temperature
    
    @property
    def client(self) -> AsyncOpenAI:
        """Lazy initialization of OpenAI client"""
        if self._client is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OPENAI_API_KEY environment variable is not set. "
                    "SEQ/SUS collection requires OpenAI API access."
                )
            self._client = AsyncOpenAI(api_key=api_key)
        return self._client
    
    async def collect_seq(
        self,
        task_description: str,
        memory_state: Dict[str, Any],
        persona_context: str
    ) -> Optional[Dict[str, Any]]:
        """
        Collect Single Ease Question (SEQ) score.
        
        Asks the agent to self-assess task difficulty on a 1-7 scale
        after completing a task.
        
        Args:
            task_description: The task that was attempted
            memory_state: Final memory state with action_history, 
                         action_summary, outcome_mismatches
            persona_context: The persona the agent embodied
        
        Returns:
            Dict with:
                - rating: float (1-7)
                - reasoning: str
            Or None if collection fails
        """
        
        # Build system prompt with persona
        system_prompt = f"""You are reflecting on a website usability task you just completed.

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

Be honest and specific in your assessment."""

        # Build user prompt with task and memory
        user_prompt = self._build_seq_user_prompt(task_description, memory_state)
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "seq_response",
                        "strict": True,
                        "schema": self.SEQ_SCHEMA
                    }
                },
                temperature=self.temperature
            )
            
            content = response.choices[0].message.content
            result = json.loads(content)
            
            print(f"[SEQ] Rating: {result['rating']}/7")
            print(f"[SEQ] Reasoning: {result['reasoning'][:100]}...")
            
            return {
                "rating": float(result["rating"]),
                "reasoning": result["reasoning"]
            }
        
        except Exception as e:
            print(f"[SEQ] Collection failed: {e}")
            return None
    
    def _build_seq_user_prompt(
        self, 
        task_description: str, 
        memory_state: Dict[str, Any]
    ) -> str:
        """Build user prompt for SEQ collection"""
        
        # Extract memory components
        action_history = memory_state.get("action_history", [])
        action_summary = memory_state.get("action_summary")
        outcome_mismatches = memory_state.get("outcome_mismatches", [])
        
        prompt_parts = []
        
        # Task description
        prompt_parts.append(f"# TASK YOU COMPLETED\n{task_description}")
        
        # Action history
        if action_summary and action_history:
            prompt_parts.append(f"\n# WHAT HAPPENED\n\n## Earlier Actions (Summary)\n{action_summary}")
            prompt_parts.append(f"\n## Recent Actions")
            for i, action in enumerate(action_history):
                prompt_parts.append(
                    f"{i+1}. {action['action_type']}: {action['reasoning']}"
                )
        elif action_history:
            prompt_parts.append(f"\n# WHAT HAPPENED")
            for i, action in enumerate(action_history):
                prompt_parts.append(
                    f"{i+1}. {action['action_type']}: {action['reasoning']}"
                )
        elif action_summary:
            prompt_parts.append(f"\n# WHAT HAPPENED\n{action_summary}")
        
        # Outcome mismatches
        if outcome_mismatches:
            prompt_parts.append(f"\n# ISSUES YOU ENCOUNTERED")
            for i, mismatch in enumerate(outcome_mismatches):
                prompt_parts.append(f"{i+1}. {mismatch}")
        
        # Question
        prompt_parts.append("\n# YOUR ASSESSMENT\nBased on your experience completing this task, how easy or difficult was it? Rate from 1 (very difficult) to 7 (very easy) and explain your reasoning.")
        
        return "\n".join(prompt_parts)