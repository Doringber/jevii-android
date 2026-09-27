from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .models import Locator, ScreenQuality, TargetStrategy, UIElement

_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _bool(node: ET.Element, name: str, default: bool = False) -> bool:
    value = node.attrib.get(name)
    return default if value is None else value.lower() == "true"


def _bounds(raw: str) -> tuple[int, int, int, int] | None:
    m = _BOUNDS.fullmatch(raw or "")
    return tuple(map(int, m.groups())) if m else None


def _xpath_literal(s: str) -> str:
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"


@dataclass
class ParsedNode:
    node: ET.Element
    parent: ET.Element | None
    index: int
    path: str


class HierarchyParser:
    """Turn the raw Android accessibility hierarchy into useful, executable UI candidates."""

    def parse(self, xml: str) -> tuple[list[UIElement], ScreenQuality]:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return [], ScreenQuality(0.0, 0, 0, 0, 0, 0, "invalid XML")

        nodes = list(self._walk(root))
        rid_counts = Counter((p.node.attrib.get("resource-id") or "").strip() for p in nodes)
        text_counts = Counter((p.node.attrib.get("text") or "").strip() for p in nodes)
        desc_counts = Counter((p.node.attrib.get("content-desc") or "").strip() for p in nodes)

        candidates: list[UIElement] = []
        for i, p in enumerate(nodes):
            n = p.node
            enabled = _bool(n, "enabled", True)
            clickable = _bool(n, "clickable") or _bool(n, "long-clickable")
            editable = "edittext" in (n.attrib.get("class") or "").lower() or _bool(n, "focusable") and _bool(n, "focused")
            scrollable = _bool(n, "scrollable")
            text = (n.attrib.get("text") or "").strip()
            desc = (n.attrib.get("content-desc") or "").strip()
            rid = (n.attrib.get("resource-id") or "").strip()
            bounds = _bounds(n.attrib.get("bounds", ""))
            descendant_texts = self._descendant_texts(n) if clickable and not (text or desc) else []
            synthesized_label = bool(descendant_texts)
            if synthesized_label:
                text = descendant_texts[0]

            meaningful = bool(text or desc or rid)
            interactive = clickable or editable or scrollable or _bool(n, "checkable")
            if not enabled or not (meaningful and interactive):
                continue

            parent_text = self._parent_context(p.parent)
            nearby = self._nearby_context(p.parent, n)
            element = UIElement(
                id=f"e{len(candidates):03d}",
                text=text,
                content_desc=desc,
                resource_id=rid,
                class_name=n.attrib.get("class", ""),
                package=n.attrib.get("package", ""),
                clickable=clickable,
                enabled=enabled,
                editable=editable,
                scrollable=scrollable,
                selected=_bool(n, "selected"),
                bounds=bounds,
                parent_text=parent_text,
                nearby_text=nearby,
                xpath=self._build_xpath(n, p.path, rid, text, desc, rid_counts, text_counts, desc_counts),
            )
            element.locators = self._locators(element, rid_counts, text_counts, desc_counts)
            if synthesized_label:
                # The label belongs to a child TextView, while the clickable
                # bounds belong to this row container. Use the real row bounds
                # instead of inventing a text selector for the parent.
                if bounds:
                    x1, y1, x2, y2 = bounds
                    element.locators = [Locator(
                        TargetStrategy.COORDINATES,
                        x=(x1 + x2) // 2,
                        y=(y1 + y2) // 2,
                        reliability=0.72,
                        unique=True,
                    )]
                element.nearby_text = list(dict.fromkeys(element.nearby_text + descendant_texts[1:]))[:6]
            candidates.append(element)

        candidates = self._dedupe(candidates)
        quality = self._quality(nodes, candidates)
        return candidates[:60], quality

    def fingerprint(self, package: str | None, activity: str | None, elements: list[UIElement]) -> str:
        normalized = "|".join(
            f"{e.resource_id}::{e.text}::{e.content_desc}::{e.bounds}" for e in elements
        )
        return hashlib.sha1(f"{package}|{activity}|{normalized}".encode("utf-8")).hexdigest()

    def _walk(self, root: ET.Element) -> Iterable[ParsedNode]:
        def rec(parent: ET.Element | None, node: ET.Element, path: str):
            children = list(node)
            if node.tag == "node":
                yield ParsedNode(node=node, parent=parent, index=int(node.attrib.get("index", "0") or 0), path=path)
            for idx, child in enumerate(children):
                cls = child.attrib.get("class") or child.tag
                child_path = f"{path}/{cls}[{idx + 1}]"
                yield from rec(node if node.tag == "node" else parent, child, child_path)
        yield from rec(None, root, "")

    @staticmethod
    def _parent_context(parent: ET.Element | None) -> str:
        if parent is None:
            return ""
        values = []
        for k in ("text", "content-desc", "resource-id"):
            value = (parent.attrib.get(k) or "").strip()
            if value:
                values.append(value)
        return " | ".join(values[:3])

    @staticmethod
    def _nearby_context(parent: ET.Element | None, current: ET.Element) -> list[str]:
        if parent is None:
            return []
        out: list[str] = []
        for child in list(parent):
            if child is current:
                continue
            for k in ("text", "content-desc"):
                value = (child.attrib.get(k) or "").strip()
                if value and value not in out:
                    out.append(value)
        return out[:6]

    @staticmethod
    def _descendant_texts(node: ET.Element) -> list[str]:
        """Return visible child labels for clickable containers such as Settings rows."""
        out: list[str] = []
        for child in node.iter("node"):
            if child is node:
                continue
            value = (child.attrib.get("text") or child.attrib.get("content-desc") or "").strip()
            if value and value not in out:
                out.append(value)
        return out[:7]

    @staticmethod
    def _build_xpath(node: ET.Element, fallback_path: str, rid: str, text: str, desc: str, rid_counts, text_counts, desc_counts) -> str:
        if rid and rid_counts[rid] == 1:
            return f"//*[@resource-id={_xpath_literal(rid)}]"
        if desc and desc_counts[desc] == 1:
            return f"//*[@content-desc={_xpath_literal(desc)}]"
        if text and text_counts[text] == 1:
            return f"//*[@text={_xpath_literal(text)}]"
        cls = node.attrib.get("class") or "*"
        if text:
            return f"//{cls}[@text={_xpath_literal(text)}]"
        if desc:
            return f"//{cls}[@content-desc={_xpath_literal(desc)}]"
        return fallback_path or "//*"

    @staticmethod
    def _locators(e: UIElement, rid_counts, text_counts, desc_counts) -> list[Locator]:
        out: list[Locator] = []
        if e.resource_id:
            unique = rid_counts[e.resource_id] == 1
            out.append(Locator(TargetStrategy.RESOURCE_ID, e.resource_id, reliability=0.99 if unique else 0.82, unique=unique))
        if e.content_desc:
            unique = desc_counts[e.content_desc] == 1
            out.append(Locator(TargetStrategy.DESCRIPTION, e.content_desc, reliability=0.95 if unique else 0.76, unique=unique))
        if e.text:
            unique = text_counts[e.text] == 1
            out.append(Locator(TargetStrategy.TEXT, e.text, reliability=0.91 if unique else 0.70, unique=unique))
        if e.xpath:
            out.append(Locator(TargetStrategy.XPATH, e.xpath, reliability=0.80))
        if e.bounds:
            x1, y1, x2, y2 = e.bounds
            out.append(Locator(TargetStrategy.COORDINATES, x=(x1+x2)//2, y=(y1+y2)//2, reliability=0.45))
        return sorted(out, key=lambda x: x.reliability, reverse=True)

    @staticmethod
    def _dedupe(elements: list[UIElement]) -> list[UIElement]:
        seen: set[tuple] = set()
        out: list[UIElement] = []
        for e in elements:
            key = (e.resource_id, e.text, e.content_desc, e.bounds)
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
        for i, e in enumerate(out):
            e.id = f"e{i:03d}"
        return out

    @staticmethod
    def _quality(nodes: list[ParsedNode], elements: list[UIElement]) -> ScreenQuality:
        useful = len(elements)
        interactive = sum(1 for p in nodes if _bool(p.node, "clickable") or _bool(p.node, "scrollable") or "edittext" in (p.node.attrib.get("class") or "").lower())
        with_text = sum(1 for p in nodes if (p.node.attrib.get("text") or "").strip())
        with_id = sum(1 for p in nodes if (p.node.attrib.get("resource-id") or "").strip())
        with_desc = sum(1 for p in nodes if (p.node.attrib.get("content-desc") or "").strip())
        denom = max(1, interactive)
        score = min(1.0,
            0.35 * min(1, useful / max(1, denom)) +
            0.25 * min(1, with_id / max(1, len(nodes) * .15)) +
            0.20 * min(1, with_text / max(1, len(nodes) * .15)) +
            0.20 * min(1, with_desc / max(1, len(nodes) * .08))
        )
        reason = "xml-rich" if score >= .7 else "xml-ambiguous" if score >= .4 else "xml-poor"
        return ScreenQuality(round(score, 3), useful, interactive, with_text, with_id, with_desc, reason)
