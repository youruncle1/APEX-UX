from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum
import re

class VerificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"

@dataclass
class VerificationResult:
    status: VerificationStatus
    evidence: Optional[str] = None

class Verifier:
    """Handles task verification based on rules"""
    
    def __init__(self, rules: Optional[Dict[str, Any]]):
        self.rules = rules
    
    async def verify(self, current_url: str, page_text: str) -> VerificationResult:
        """Verify task completion based on rules"""
        if not self.rules:
            return VerificationResult(status=VerificationStatus.SKIPPED)
        
        rule_type = self.rules.get("type")
        
        if rule_type == "url_contains":
            value = self.rules.get("value")
            if value in current_url:
                return VerificationResult(
                    status=VerificationStatus.PASS,
                    evidence=f"URL contains '{value}': {current_url}"
                )
            return VerificationResult(
                status=VerificationStatus.FAIL,
                evidence=f"URL does not contain '{value}': {current_url}"
            )
        
        elif rule_type == "text_any":
            texts = self.rules.get("texts", [])
            for text in texts:
                if text.lower() in page_text.lower():
                    return VerificationResult(
                        status=VerificationStatus.PASS,
                        evidence=f"Found text: '{text}'"
                    )
            return VerificationResult(
                status=VerificationStatus.FAIL,
                evidence=f"None of the expected texts found: {texts}"
            )
        
        elif rule_type == "any_of":
            conditions = self.rules.get("conditions", [])
            for condition in conditions:
                verifier = Verifier(condition)
                result = await verifier.verify(current_url, page_text)
                if result.status == VerificationStatus.PASS:
                    return result
            return VerificationResult(
                status=VerificationStatus.FAIL,
                evidence="None of the conditions passed"
            )
        
        return VerificationResult(
            status=VerificationStatus.UNKNOWN,
            evidence=f"Unknown verification type: {rule_type}"
        )