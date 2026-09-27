from __future__ import annotations

from dataclasses import dataclass

from .models import UIElement


@dataclass
class SelectionDecision:
    element: UIElement | None
    action: str
    reason: str
    probability: float
    confidence: float
    execution_score: float
    probabilities: dict[str, float]


class PolicyEngine:
    """Turn Jev uncertainty + locator reliability into an execution decision."""

    def __init__(self, execute_threshold: float = 0.56, inspect_threshold: float = 0.36):
        self.execute_threshold = execute_threshold
        self.inspect_threshold = inspect_threshold

    def decide(self, selected_id: str, probabilities: dict[str, float], confidence: float, elements: dict[str, UIElement]) -> SelectionDecision:
        element = elements.get(selected_id)
        if element is None:
            return SelectionDecision(None, "fail", "Jev selected an unknown candidate", 0, confidence, 0, probabilities)
        probability = float(probabilities.get(selected_id, 0.0))
        locator_quality = max((l.reliability for l in element.locators), default=0.0)
        execution_score = probability * max(0.2, confidence) * locator_quality

        ordered = sorted(probabilities.values(), reverse=True)
        margin = (ordered[0] - ordered[1]) if len(ordered) > 1 else ordered[0] if ordered else 0
        if execution_score >= self.execute_threshold and margin >= .08:
            action = "execute"
            reason = "high semantic probability, confidence, and locator reliability"
        elif execution_score >= self.inspect_threshold:
            action = "inspect"
            reason = "candidate is plausible but ambiguous; request richer observation before clicking"
        else:
            action = "retry"
            reason = "selection confidence is too low to click safely"
        return SelectionDecision(element, action, reason, probability, confidence, execution_score, probabilities)
