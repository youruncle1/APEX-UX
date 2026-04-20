import re
from typing import Optional, Dict, Any, Tuple

from src.browser_connector.models.data_models import (
    VerificationResult,
    VerificationStatus,
)


class Verifier:
    """
    Handles task verification based on rules
    """

    def __init__(self, rules: Optional[Dict[str, Any]]):
        self.rules = rules  # Dict containing all rules of a task

    async def verify(self, current_url: str, page_text: str) -> VerificationResult:
        """
        Verify task completion and return detailed results
        """
        if not self.rules:
            return VerificationResult(
                status=VerificationStatus.SKIPPED
            )  # skipped if no rules present

        result_status, details = await self._verify_rule(
            self.rules, current_url, page_text
        )

        return VerificationResult(status=result_status, details=details)

    async def _verify_rule(
        self, rule: Dict[str, Any], current_url: str, page_text: str
    ) -> Tuple[VerificationStatus, Dict[str, Any]]:
        """
        Verify a rule and return (status, details_dict).
        Also handles verification of nested rules and OR groups.
        """
        rule_type = rule.get("type")

        # Checks if a specific string is a substring of current_url
        if rule_type == "url_contains":
            value = rule.get("value")
            passed = value in current_url

            details = {
                "type": "url_contains",
                "passed": passed,
                "value": value,
                "evidence": f"URL {'contains' if passed else 'does not contain'} '{value}': {current_url}",
            }

            return (
                VerificationStatus.PASS if passed else VerificationStatus.FAIL,
                details,
            )

        # Checks if any of a list of strings appear anywhere in page
        elif rule_type == "text_any":
            texts = rule.get("texts", [])
            found = None

            # Replace whitespace characters with one space
            # applies to spaces, tabs \t, newlines \n, \r etc...
            # reason: page text extracted from a browser can have inconsistent whitespace (different viewports)
            normalized_page = re.sub(
                r"\s+", " ", page_text
            ).lower()  # .lower() for case insensitive

            for text in texts:
                normalized_text = re.sub(r"\s+", " ", text).lower()
                if normalized_text in normalized_page:
                    found = text
                    break

            passed = found is not None

            details = {
                "type": "text_any",
                "passed": passed,
                "texts": texts,
                "evidence": f"Found text: '{found}'"
                if found
                else f"None of the expected texts found: {texts}",
            }

            return (
                VerificationStatus.PASS if passed else VerificationStatus.FAIL,
                details,
            )

        # Logical OR over a list of rules
        # Passes if at least one of child rule passes
        # Recursive nesting - _verify_rule called on children
        elif rule_type == "any_of":
            conditions = rule.get("conditions", [])
            children_details = []
            any_passed = False

            # evaluate all children
            for condition in conditions:
                status, child_details = await self._verify_rule(
                    condition, current_url, page_text
                )
                children_details.append(child_details)
                if status == VerificationStatus.PASS:
                    any_passed = True

            passed = any_passed
            group_name = rule.get("name", "OR Group")  # group name in UI

            details = {
                "type": "any_of",
                "passed": passed,
                "name": group_name,
                "evidence": "At least one condition passed"
                if any_passed
                else "None of the conditions passed",
                "children": children_details,
            }

            return (
                VerificationStatus.PASS if passed else VerificationStatus.FAIL,
                details,
            )

        # Logical AND over a list of rules
        # Passes only if every child passes
        # Recursive nesting - _verify_rule called on children
        elif rule_type == "all_of":
            conditions = rule.get("conditions", [])
            children_details = []
            all_passed = True

            for condition in conditions:
                status, child_details = await self._verify_rule(
                    condition, current_url, page_text
                )
                children_details.append(child_details)
                if status != VerificationStatus.PASS:
                    all_passed = False

            passed = all_passed

            details = {
                "type": "all_of",
                "passed": passed,
                "evidence": "All conditions passed"
                if all_passed
                else "One or more conditions failed",
                "children": children_details,
            }

            return (
                VerificationStatus.PASS if passed else VerificationStatus.FAIL,
                details,
            )

        # unknown rule type treated as non-pass
        return (
            VerificationStatus.UNKNOWN,
            {
                "type": "unknown",
                "passed": False,
                "evidence": f"Unknown verification type: {rule_type}",
            },
        )
