import os
from typing import Optional
from openai import AsyncOpenAI

class MemoryManager:
    """Manages action history, summaries, and outcome mismatches"""
    
    def __init__(self, compression_threshold: int = 5):
        """
        Initialize memory manager.
        """
        self.compression_threshold = compression_threshold
        self.action_history: list[dict] = []
        self.action_summary: Optional[str] = None
        self.outcome_mismatches: list[dict] = []  # [{"step": int, "mismatch": str}]
        self.current_step = 0
        self.client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    def add_action(self, action: dict):
        """Add action to history and increment step counter"""
        self.current_step += 1
        self.action_history.append(action)
    
    def add_outcome_mismatch(self, mismatch: str):
        """
        Add outcome mismatch tied to current step.
        
        Args:
            mismatch: Description of what went wrong
        """
        if mismatch:
            self.outcome_mismatches.append({
                "step": self.current_step,
                "mismatch": mismatch
            })
    
    async def maybe_compress(self):
        """
        Compress history if it exceeds threshold.
        """
        if len(self.action_history) <= self.compression_threshold:
            return  # No compression needed
        
        to_compress = self.action_history[:-1] 
        self.action_history = self.action_history[-1:] 
        
        # Build text to compress
        actions_text = "\n".join([
            f"Step {i+1}: {action['action_type']} - {action['reasoning']}"
            for i, action in enumerate(to_compress)
        ])
        
        # If summary exists, include it for recompression
        if self.action_summary:
            compress_input = f"Previous summary:\n{self.action_summary}\n\nNew actions:\n{actions_text}"
        else:
            compress_input = f"Actions:\n{actions_text}"
        
        # Compress using GPT-4o-mini
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You compress action histories into concise summaries. Focus on navigation milestones and key decisions. Be brief."
                    },
                    {
                        "role": "user",
                        "content": compress_input
                    }
                ],
                temperature=0.3,
                max_tokens=200
            )
            
            self.action_summary = response.choices[0].message.content.strip()
            print(f"[Memory] Compressed {len(to_compress)} actions into summary")
        
        except Exception as e:
            print(f"[Memory] Compression failed: {e}")
    
    def get_state(self) -> dict:
        """Get current memory state for database storage"""
        return {
            "action_history": self.action_history,
            "action_summary": self.action_summary,
            "outcome_mismatches": self.outcome_mismatches  # Now includes step info
        }
    
    def get_outcome_mismatches_for_planner(self) -> list[str]:
        """
        Get outcome mismatches as simple strings for planner prompt.
        Planner doesn't need step numbers, just the mismatch descriptions.
        """
        return [item["mismatch"] for item in self.outcome_mismatches]