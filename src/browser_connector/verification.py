from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
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
    details: Optional[dict] = None  # NEW: Structured results

class Verifier:
    """Handles task verification based on rules"""
    
    def __init__(self, rules: Optional[Dict[str, Any]]):
        self.rules = rules
    
    async def verify(self, current_url: str, page_text: str) -> VerificationResult:
        """Verify task completion and return detailed results"""
        if not self.rules:
            return VerificationResult(status=VerificationStatus.SKIPPED)
        
        # Verify and get detailed results
        result_status, details = await self._verify_rule(self.rules, current_url, page_text)
        
        # Build comprehensive evidence string
        evidence = self._build_evidence_string(details)
        
        return VerificationResult(
            status=result_status,
            evidence=evidence,
            details=details
        )
    
    async def _verify_rule(
        self, 
        rule: Dict[str, Any], 
        current_url: str, 
        page_text: str
    ) -> Tuple[VerificationStatus, Dict[str, Any]]:
        """
        Verify a rule and return (status, details_dict)
        
        Details dict structure:
        {
            'type': str,
            'passed': bool,
            'rule': dict,  # Original rule
            'evidence': str,  # Human-readable result
            'children': list  # For compound rules
        }
        """
        rule_type = rule.get("type")
        
        # ========== Individual Rules ==========
        
        if rule_type == "url_contains":
            value = rule.get("value")
            passed = value in current_url
            
            details = {
                'type': 'url_contains',
                'passed': passed,
                'rule': rule,
                'evidence': f"URL {'contains' if passed else 'does not contain'} '{value}': {current_url}"
            }
            
            return (
                VerificationStatus.PASS if passed else VerificationStatus.FAIL,
                details
            )
        
        elif rule_type == "text_any":
            texts = rule.get("texts", [])
            found = None
            
            for text in texts:
                if text.lower() in page_text.lower():
                    found = text
                    break
            
            passed = found is not None
            
            details = {
                'type': 'text_any',
                'passed': passed,
                'rule': rule,
                'evidence': f"Found text: '{found}'" if found else f"None of the expected texts found: {texts}"
            }
            
            return (
                VerificationStatus.PASS if passed else VerificationStatus.FAIL,
                details
            )
        
        # ========== Compound Rules ==========
        
        elif rule_type == "any_of":
            conditions = rule.get("conditions", [])
            children_details = []
            any_passed = False
            
            # KEY: Check ALL conditions, don't stop at first pass
            for condition in conditions:
                status, child_details = await self._verify_rule(condition, current_url, page_text)
                children_details.append(child_details)
                if status == VerificationStatus.PASS:
                    any_passed = True
            
            passed = any_passed
            evidence = "At least one condition passed" if any_passed else "None of the conditions passed"
            
            details = {
                'type': 'any_of',
                'passed': passed,
                'rule': rule,
                'evidence': evidence,
                'children': children_details
            }
            
            return (
                VerificationStatus.PASS if passed else VerificationStatus.FAIL,
                details
            )
        
        elif rule_type == "all_of":
            conditions = rule.get("conditions", [])
            children_details = []
            all_passed = True
            
            # KEY: Check ALL conditions, don't stop at first failure
            for condition in conditions:
                status, child_details = await self._verify_rule(condition, current_url, page_text)
                children_details.append(child_details)
                if status != VerificationStatus.PASS:
                    all_passed = False
            
            passed = all_passed
            evidence = "All conditions passed" if all_passed else "One or more conditions failed"
            
            details = {
                'type': 'all_of',
                'passed': passed,
                'rule': rule,
                'evidence': evidence,
                'children': children_details
            }
            
            return (
                VerificationStatus.PASS if passed else VerificationStatus.FAIL,
                details
            )
        
        # ========== Unknown ==========
        
        return (
            VerificationStatus.UNKNOWN,
            {
                'type': 'unknown',
                'passed': False,
                'rule': rule,
                'evidence': f"Unknown verification type: {rule_type}"
            }
        )
    
    def _build_evidence_string(self, details: Dict[str, Any]) -> str:
        """
        Build comprehensive human-readable evidence string from details.
        Lists all failures and key passes.
        """
        lines = []
        
        # Overall result
        overall = "PASSED" if details['passed'] else "FAILED"
        lines.append(f"Overall: {overall}")
        
        # Collect all failures and passes
        failures = []
        passes = []
        
        def collect_results(node, path=""):
            if 'children' in node:
                # Compound rule
                rule_name = node['rule'].get('name', node['type'].upper())
                node_path = f"{path}/{rule_name}" if path else rule_name
                
                for child in node['children']:
                    collect_results(child, node_path)
            else:
                # Leaf rule
                if node['passed']:
                    passes.append(f"{path}: {node['evidence']}")
                else:
                    failures.append(f"{path}: {node['evidence']}")
        
        collect_results(details)
        
        # Add failures first (most important)
        if failures:
            lines.append("\nFailures:")
            for failure in failures:
                lines.append(f"  ✗ {failure}")
        
        # Add passes (for context)
        if passes:
            lines.append("\nPasses:")
            for pass_item in passes[:3]:  # Limit to 3 to avoid clutter
                lines.append(f"  ✓ {pass_item}")
            if len(passes) > 3:
                lines.append(f"  ... and {len(passes) - 3} more")
        
        return "\n".join(lines)