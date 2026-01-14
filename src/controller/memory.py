import os
from typing import Optional
from openai import AsyncOpenAI

class MemoryManager:
    """Manages action history, summaries, and outcome mismatches"""
    
    def __init__(self, max_recent_actions: int = 5):
        self.max_recent_actions = max_recent_actions
        self.action_history: list[dict] = []
        self.action_summary: Optional[str] = None
        self.outcome_mismatches: list[str] = []
        
        # For compression
        self.client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    def add_action(self, action: dict):
        """Add action to history"""
        self.action_history.append(action)
    
    def add_outcome_mismatch(self, mismatch: str):
        """Add outcome mismatch"""
        if mismatch:
            self.outcome_mismatches.append(mismatch)
    
    async def maybe_compress(self):
        """Compress history if it exceeds threshold"""
        if len(self.action_history) <= self.max_recent_actions:
            return
        
        # Take actions that need compression
        to_compress = self.action_history[:-self.max_recent_actions]
        self.action_history = self.action_history[-self.max_recent_actions:]
        
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
        
        # Compress using GPT-4o
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",  # Use mini for compression to save costs
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
            "outcome_mismatches": self.outcome_mismatches
        }