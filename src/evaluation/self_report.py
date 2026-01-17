"""
Agent self-report collection for SEQ and SUS scores.

"""

import os
import json
from typing import Dict, Any, Optional, List
from openai import AsyncOpenAI


class SelfReportCollector:
    """Collect SEQ and SUS scores via agent self-assessment"""
    
    # Standard SUS items (10 questions) - CONSTANTS
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
    
    # SEQ constants
    SEQ_QUESTION = "Overall, this task was:"
    SEQ_SCALE = "1 (Very Difficult) to 7 (Very Easy)"
    
    # SUS scale
    SUS_SCALE = "1 (Strongly Disagree) to 5 (Strongly Agree)"
    
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
                "description": "Detailed explanation of why you gave this rating"
            }
        },
        "required": ["rating", "reasoning"],
        "additionalProperties": False
    }
    
    # JSON Schema for SUS response
    SUS_SCHEMA = {
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
        
        Args:
            task_description: The task that was attempted
            memory_state: Final memory state with action_history, 
                         action_summary, outcome_mismatches
            persona_context: The persona the agent embodied
        
        Returns:
            Dict with:
                - rating: float (1-7)
                - reasoning: str (detailed explanation)
            Or None if collection fails
        """
        
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

Be honest, detailed, and specific in your assessment."""

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
        
        prompt_parts.append(f"\n# YOUR ASSESSMENT\nBased on your experience completing this task, how easy or difficult was it? Rate from 1 (very difficult) to 7 (very easy) and provide detailed reasoning.")
        
        return "\n".join(prompt_parts)
    
    async def collect_sus(
        self,
        persona_context: str,
        task_summaries: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Collect System Usability Scale (SUS) score.
        
        Args:
            persona_context: The persona the agent embodied
            task_summaries: List of dicts with task details
        
        Returns:
            Dict with:
                - sus_score: float (0-100, calculated from ratings)
                - ratings: list[int] (10 ratings, 1-5 each)
                - reasoning: str (overall impression)
            Or None if collection fails
        """
        
        system_prompt = f"""You are reflecting on your overall experience using a website.

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

        user_prompt = self._build_sus_user_prompt(task_summaries)
        
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
                        "name": "sus_response",
                        "strict": True,
                        "schema": self.SUS_SCHEMA
                    }
                },
                temperature=self.temperature
            )
            
            content = response.choices[0].message.content
            result = json.loads(content)
            
            # Calculate SUS score
            sus_score = self._calculate_sus_score(result["ratings"])
            
            print(f"[SUS] Score: {sus_score:.1f}/100")
            print(f"[SUS] Reasoning: {result['reasoning'][:100]}...")
            
            return {
                "sus_score": sus_score,
                "ratings": result["ratings"],  # Just array of 10 integers
                "reasoning": result["reasoning"]
            }
        
        except Exception as e:
            print(f"[SUS] Collection failed: {e}")
            return None
    
    def _build_sus_user_prompt(self, task_summaries: List[Dict[str, Any]]) -> str:
        """Build user prompt for SUS collection"""
        
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
        
        for i, item in enumerate(self.SUS_ITEMS, 1):
            prompt_parts.append(f"{i}. {item}")
        
        prompt_parts.append("\n# YOUR RESPONSE")
        prompt_parts.append("Provide an array of 10 ratings (in order) and detailed reasoning explaining your ratings.")
        
        return "\n".join(prompt_parts)
    
    def _calculate_sus_score(self, ratings: List[int]) -> float:
        """
        Calculate SUS score using standard formula.
        
        For odd-numbered items (1,3,5,7,9): contribution = (rating - 1)
        For even-numbered items (2,4,6,8,10): contribution = (5 - rating)
        SUS score = sum of contributions * 2.5
        """
        
        total = 0
        for i, rating in enumerate(ratings):
            item_num = i + 1  # 1-indexed
            if item_num % 2 == 1:  # Odd items (positive)
                total += (rating - 1)
            else:  # Even items (negative, reverse scored)
                total += (5 - rating)
        
        sus_score = total * 2.5
        return round(sus_score, 1)
    
    @classmethod
    def get_sus_question(cls, item_number: int) -> str:
        """Get SUS question text by item number (1-10)"""
        return cls.SUS_ITEMS[item_number - 1]
    
    @classmethod
    def get_seq_question(cls) -> str:
        """Get SEQ question text"""
        return cls.SEQ_QUESTION
    
    @classmethod
    def get_seq_scale(cls) -> str:
        """Get SEQ scale description"""
        return cls.SEQ_SCALE
    
    @classmethod
    def get_sus_scale(cls) -> str:
        """Get SUS scale description"""
        return cls.SUS_SCALE