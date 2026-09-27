from jevii_android.models import Locator, TargetStrategy, UIElement
from jevii_android.policy import PolicyEngine


def element(reliability=.99):
    return UIElement(id="e001", text="Network & internet", clickable=True, locators=[Locator(TargetStrategy.RESOURCE_ID, "id/network", reliability=reliability, unique=True)])


def test_policy_executes_clear_winner():
    d = PolicyEngine().decide("e001", {"e001": .92, "e002": .05}, .9, {"e001": element()})
    assert d.action == "execute"
    assert d.execution_score > .7


def test_policy_inspects_ambiguous_winner():
    d = PolicyEngine().decide("e001", {"e001": .60, "e002": .52}, .9, {"e001": element()})
    assert d.action in {"inspect", "retry"}
