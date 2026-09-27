from __future__ import annotations

import os
from typing import Any

import requests

from .models import Action, ActionType, AndroidState, Target
from .policy import PolicyEngine


SYSTEM_CRITERIA = {
    "choose_element": "A visible element can directly advance the current goal.",
    "swipe_up": "The needed control is likely below the visible area; scroll down the content.",
    "swipe_down": "The needed control is likely above the visible area; scroll up the content.",
    "back": "Go back one Android screen because the current screen does not advance the goal.",
    "wait": "The screen is still loading or transitioning.",
    "complete": "The goal is visibly satisfied on this screen.",
    "fail": "The goal cannot safely continue from the current state.",
}


class JevDecisionClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None, policy: PolicyEngine | None = None):
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY")
        self.model = model or os.getenv("JEV_MODEL", "jev-latest")
        self.base_url = base_url or os.getenv("JEV_BASE_URL", "https://api.typesafe.ai/v1/systemone")
        self.policy = policy or PolicyEngine()
        if not self.api_key:
            raise RuntimeError("TYPESAFE_API_KEY is required")

    def _call(self, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
        r = requests.post(
            self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "state": state, "questions": questions},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def decide(self, goal: str, state: AndroidState) -> Action:
        compact = state.compact()
        compact["goal"] = goal

        mode_res = self._call(compact, {
            "next_mode": {
                "type": "choice",
                "instructions": "Choose exactly one next step. Take only one UI-changing action. Prefer choosing a real visible element when possible.",
                "criteria": SYSTEM_CRITERIA,
            }
        })
        mode_answer = mode_res["answers"]["next_mode"]
        mode = str(mode_answer["choice"])

        if mode == "complete":
            return Action(ActionType.COMPLETE, reason="Jev judged the visible goal complete", metadata=mode_answer)
        if mode == "fail":
            return Action(ActionType.FAIL, reason="Jev judged the goal cannot continue", metadata=mode_answer)
        if mode == "wait":
            return Action(ActionType.WAIT, duration_ms=700, reason="Jev requested wait", metadata=mode_answer)
        if mode == "back":
            return Action(ActionType.BACK, reason="Jev requested back", metadata=mode_answer)
        if mode == "swipe_up":
            return Action(ActionType.SWIPE, direction="up", metadata=mode_answer)
        if mode == "swipe_down":
            return Action(ActionType.SWIPE, direction="down", metadata=mode_answer)

        elements = {e.id: e for e in state.elements}
        if not elements:
            return Action(ActionType.WAIT, duration_ms=500, reason="No reliable XML candidates; richer perception/vision is required", metadata={"needs_vision": True})

        criteria = {eid: e.semantic_summary() for eid, e in elements.items()}
        pick_res = self._call({
            "goal": goal,
            "package": state.package,
            "activity": state.activity,
            "screen_quality": state.screen_quality.score if state.screen_quality else None,
        }, {
            "target": {
                "type": "choice",
                "instructions": "Which visible interactive element most directly advances the user goal right now? Choose the element itself, not a locator strategy.",
                "criteria": criteria,
            }
        })
        answer = pick_res["answers"]["target"]
        selected = str(answer["choice"])
        probabilities = {str(k): float(v) for k, v in answer.get("probabilities", {}).items()}
        confidence = float(answer.get("confidence", 0.0))
        decision = self.policy.decide(selected, probabilities, confidence, elements)

        meta = {
            "jev": answer,
            "policy": {
                "action": decision.action,
                "reason": decision.reason,
                "execution_score": decision.execution_score,
            },
        }
        if decision.action == "inspect":
            return Action(ActionType.WAIT, duration_ms=250, reason=decision.reason, metadata={**meta, "needs_vision": True})
        if decision.action == "retry":
            return Action(ActionType.WAIT, duration_ms=400, reason=decision.reason, metadata=meta)

        element = decision.element
        assert element is not None
        best = element.locators[0] if element.locators else None
        if best is None:
            return Action(ActionType.WAIT, duration_ms=250, reason="Selected element has no executable locator", metadata={**meta, "needs_vision": True})
        return Action(
            ActionType.TAP,
            target=Target(best.strategy, value=best.value, x=best.x, y=best.y),
            reason=f"Jev selected {element.id}: {element.label}",
            metadata={**meta, "element_id": element.id, "locator_reliability": best.reliability},
        )
