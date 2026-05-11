"""Email routing rule engine — YAML rules + glob pattern matching."""

import fnmatch
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

import yaml


@dataclass
class RoutingRule:
    recipient: str          # glob pattern
    sender: str             # glob pattern
    path: str               # destination path, may contain templated vars
    label: str              # classification label
    action: str = "save"    # save | archive-no-save

    def matches(self, sender: str, recipient: str) -> bool:
        """Check if this rule matches the given sender and recipient."""
        return fnmatch.fnmatch(recipient.lower(), self.recipient.lower()) and fnmatch.fnmatch(sender.lower(), self.sender.lower())

    def compute_path(self, sender: str) -> str:
        """Expand template variables in the path."""
        sender_domain = sender.split("@")[-1] if "@" in sender else sender
        return (
            self.path
            .replace("{sender_domain}", sender_domain)
            .replace("{date}", date.today().isoformat())
            .replace("{label}", self.label)
        )


@dataclass
class RoutingResult:
    rule: RoutingRule
    expanded_path: str

    @property
    def label(self) -> str:
        return self.rule.label

    @property
    def action(self) -> str:
        return self.rule.action


@dataclass
class RuleSet:
    rules: List[RoutingRule] = field(default_factory=list)

    def match(self, sender: str, recipient: str) -> Optional[RoutingResult]:
        """Return first matching rule, or None."""
        for rule in self.rules:
            if rule.matches(sender, recipient):
                return RoutingResult(
                    rule=rule,
                    expanded_path=rule.compute_path(sender),
                )
        return None


def load_rules(path: str) -> RuleSet:
    """Load routing rules from YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    rules = []
    for r in raw.get("rules", []):
        rules.append(RoutingRule(
            recipient=r.get("recipient", "*"),
            sender=r.get("sender", "*"),
            path=r.get("path", "~/Downloads/email-attachments/unsorted/"),
            label=r.get("label", "unsorted"),
            action=r.get("action", "save"),
        ))

    return RuleSet(rules=rules)
