from jevii_android.jev import JevDecisionClient
from jevii_android.models import AndroidState, Locator, ScreenQuality, TargetStrategy, UIElement, ActionType


class FakeJev(JevDecisionClient):
    def __init__(self):
        self.policy = __import__('jevii_android.policy', fromlist=['PolicyEngine']).PolicyEngine()
        self.calls = 0
    def _call(self, state, questions):
        self.calls += 1
        if self.calls == 1:
            return {"answers": {"next_mode": {"choice": "choose_element", "probabilities": {"choose_element": .95}, "confidence": .95}}}
        return {"answers": {"target": {"choice": "e000", "probabilities": {"e000": .96, "e999": .02}, "confidence": .95}}}


def test_jev_selects_element_then_best_locator():
    e = UIElement(id="e000", text="Network & internet", clickable=True, locators=[Locator(TargetStrategy.RESOURCE_ID, "pkg:id/network", reliability=.99, unique=True)])
    state = AndroidState("pkg", "A", "<hierarchy/>", None, elements=[e], screen_quality=ScreenQuality(.8,1,1,1,1,0,"xml-rich"))
    action = FakeJev().decide("open Network & internet settings", state)
    assert action.action == ActionType.TAP
    assert action.target.strategy == TargetStrategy.RESOURCE_ID
    assert action.target.value == "pkg:id/start"
