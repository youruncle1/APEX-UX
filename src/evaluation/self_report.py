"""
Agent self-report collection for SEQ and SUS scores.
"""

import os
import json
from typing import Dict, Any, Optional, List
from src.agents.llm_client import get_utility_client


class SelfReportCollector:
    """Collect SEQ and SUS scores via agent self-assessment"""
    
    # Standard SUS items (10 questions)
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
    
    # JSON Schemas for structured outputs (OpenAI only)
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
    
    # JSON instructions for Qwen (appended to prompt when no structured output)
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
    
    def __init__(self, temperature: float = 0.3):
        self.temperature = temperature
        self._client = None
        self._model = None
        self._backend = None
    
    def _ensure_client(self):
        """Lazy-initialize the LLM client."""
        if self._client is None:
            self._client, self._model = get_utility_client()
            self._backend = os.getenv("UTILITY_BACKEND", "openai").lower()
    
    def _is_qwen(self) -> bool:
        return self._backend == "qwen"
    
    def _clean_json_response(self, content: str) -> str:
        """Strip markdown fences and whitespace from model response."""
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            content = "\n".join(lines).strip()
        return content
    
    async def collect_seq(
        self,
        task_description: str,
        memory_state: Dict[str, Any],
        persona_context: str
    ) -> Optional[Dict[str, Any]]:
        """
        Collect Single Ease Question (SEQ) score.
        
        Returns:
            Dict with rating (float 1-7) and reasoning (str), or None on failure.
        """
        self._ensure_client()
        
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
        
        # Append JSON instruction for Qwen
        if self._is_qwen():
            user_prompt += self.SEQ_JSON_INSTRUCTION
        
        try:
            kwargs = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": self.temperature,
                "max_tokens": 512,
            }
            
            # Only OpenAI supports structured outputs
            if not self._is_qwen():
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "seq_response",
                        "strict": True,
                        "schema": self.SEQ_SCHEMA
                    }
                }
            
            response = await self._client.chat.completions.create(**kwargs)
            
            content = response.choices[0].message.content
            content = self._clean_json_response(content)
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
        
        prompt_parts.append(
            f"\n# YOUR ASSESSMENT\n"
            f"Based on your experience completing this task, how easy or difficult was it? "
            f"Rate from 1 (very difficult) to 7 (very easy) and provide detailed reasoning."
        )
        
        return "\n".join(prompt_parts)
    
    async def collect_sus(
        self,
        persona_context: str,
        task_summaries: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Collect System Usability Scale (SUS) score.
        
        Returns:
            Dict with sus_score (float 0-100), ratings (list[int]), reasoning (str),
            or None on failure.
        """
        self._ensure_client()
        
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
        
        # Append JSON instruction for Qwen
        if self._is_qwen():
            user_prompt += self.SUS_JSON_INSTRUCTION
        
        try:
            kwargs = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": self.temperature,
                "max_tokens": 1024,
            }
            
            if not self._is_qwen():
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "sus_response",
                        "strict": True,
                        "schema": self.SUS_SCHEMA
                    }
                }
            
            response = await self._client.chat.completions.create(**kwargs)
            
            content = response.choices[0].message.content
            content = self._clean_json_response(content)
            result = json.loads(content)
            
            # Validate ratings
            ratings = result["ratings"]
            if len(ratings) != 10 or not all(1 <= r <= 5 for r in ratings):
                print(f"[SUS] Invalid ratings: {ratings}")
                return None
            
            sus_score = self._calculate_sus_score(ratings)
            
            print(f"[SUS] Score: {sus_score:.1f}/100")
            print(f"[SUS] Reasoning: {result['reasoning'][:100]}...")
            
            return {
                "sus_score": sus_score,
                "ratings": ratings,
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
        Odd items (1,3,5,7,9): contribution = (rating - 1)
        Even items (2,4,6,8,10): contribution = (5 - rating)
        SUS score = sum * 2.5
        """
        total = 0
        for i, rating in enumerate(ratings):
            item_num = i + 1
            if item_num % 2 == 1:
                total += (rating - 1)
            else:
                total += (5 - rating)
        
        return round(total * 2.5, 1)
    
    @classmethod
    def get_sus_question(cls, item_number: int) -> str:
        return cls.SUS_ITEMS[item_number - 1]
    
    @classmethod
    def get_seq_question(cls) -> str:
        return cls.SEQ_QUESTION
    
    @classmethod
    def get_seq_scale(cls) -> str:
        return cls.SEQ_SCALE
    
    @classmethod
    def get_sus_scale(cls) -> str:
        return cls.SUS_SCALE