from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Literal


class ActionType(str, Enum):
    TAP = "tap"
    TYPE = "type"
    SWIPE = "swipe"
    BACK = "back"
    HOME = "home"
    OPEN_APP = "open_app"
    WAIT = "wait"
    ASSERT_VISIBLE = "assert_visible"
    COMPLETE = "complete"
    FAIL = "fail"


class TargetStrategy(str, Enum):
    RESOURCE_ID = "resource_id"
    TEXT = "text"
    DESCRIPTION = "description"
    XPATH = "xpath"
    COORDINATES = "coordinates"


@dataclass
class Locator:
    strategy: TargetStrategy
    value: str | None = None
    x: int | None = None
    y: int | None = None
    reliability: float = 0.0
    unique: bool = False


@dataclass
class UIElement:
    id: str
    text: str = ""
    content_desc: str = ""
    resource_id: str = ""
    class_name: str = ""
    package: str = ""
    clickable: bool = False
    enabled: bool = True
    editable: bool = False
    scrollable: bool = False
    selected: bool = False
    bounds: tuple[int, int, int, int] | None = None
    parent_text: str = ""
    nearby_text: list[str] = field(default_factory=list)
    xpath: str = ""
    locators: list[Locator] = field(default_factory=list)

    @property
    def role(self) -> str:
        name = self.class_name.lower()
        if self.editable or "edittext" in name:
            return "input"
        if "button" in name:
            return "button"
        if "checkbox" in name or "switch" in name:
            return "toggle"
        if self.scrollable:
            return "scrollable"
        if self.clickable:
            return "clickable"
        return "element"

    @property
    def label(self) -> str:
        return self.text or self.content_desc or self.resource_id.rsplit("/", 1)[-1] or self.class_name

    def semantic_summary(self) -> str:
        parts = [f"role={self.role}", f"label={self.label!r}"]
        if self.parent_text:
            parts.append(f"parent={self.parent_text!r}")
        if self.nearby_text:
            parts.append(f"nearby={self.nearby_text[:4]!r}")
        if self.resource_id:
            parts.append(f"id={self.resource_id!r}")
        return "; ".join(parts)


@dataclass
class ScreenQuality:
    score: float
    useful_nodes: int
    interactive_nodes: int
    with_text: int
    with_resource_id: int
    with_description: int
    reason: str


@dataclass
class Target:
    strategy: TargetStrategy
    value: str | None = None
    x: int | None = None
    y: int | None = None


@dataclass
class Action:
    action: ActionType
    target: Target | None = None
    text: str | None = None
    package: str | None = None
    direction: Literal["up", "down", "left", "right"] | None = None
    duration_ms: int = 300
    reason: str = ""
    expected_result: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AndroidState:
    package: str | None
    activity: str | None
    hierarchy_xml: str
    screenshot_path: str | None
    width: int | None = None
    height: int | None = None
    step: int = 0
    recent_actions: list[dict[str, Any]] = field(default_factory=list)
    elements: list[UIElement] = field(default_factory=list)
    screen_quality: ScreenQuality | None = None
    stable: bool = True
    fingerprint: str = ""

    def compact(self, max_elements: int = 40) -> dict[str, Any]:
        elements = {
            e.id: {
                "role": e.role,
                "label": e.label,
                "text": e.text,
                "description": e.content_desc,
                "resource_id": e.resource_id,
                "parent_text": e.parent_text,
                "nearby_text": e.nearby_text[:4],
                "locator_reliability": max((l.reliability for l in e.locators), default=0.0),
            }
            for e in self.elements[:max_elements]
        }
        return {
            "package": self.package,
            "activity": self.activity,
            "screen_size": [self.width, self.height],
            "step": self.step,
            "stable": self.stable,
            "screen_quality": asdict(self.screen_quality) if self.screen_quality else None,
            "elements": elements,
            "recent_actions": self.recent_actions[-6:],
        }
