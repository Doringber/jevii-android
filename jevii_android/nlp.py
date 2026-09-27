from __future__ import annotations

import re
from typing import Any


class NlpCompileError(ValueError):
    def __init__(self, issues: list[dict[str, Any]]):
        self.issues = issues
        super().__init__("Natural-language use case has unsupported or ambiguous instructions")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _split_instructions(story: str) -> list[str]:
    text = story.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\bif\s+(?:it\s+is\s+)?visible\s*,", "if visible ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:and\s+)?then\b", ";", text, flags=re.IGNORECASE)
    text = re.sub(
        r",\s*(?=(?:and\s+)?(?:open|launch|start|search|look up|find|switch|return|go back|restart|relaunch|force.stop|verify|check|assert|tap|type|press|wait)\b)",
        ";",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\band\s+(?=(?:open|launch|start|switch|return|go back|restart|relaunch|force.stop|verify|check|assert|tap|type|press|wait)\b)",
        ";",
        text,
        flags=re.IGNORECASE,
    )
    return [part.strip(" \t.;") for part in re.split(r"[;\n]+|(?<=[.!?])\s+", text) if part.strip(" \t.;")]


def _app_for_clause(clause: str, aliases: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    normalized = _normalize(clause)
    matches = [(key, value) for key, value in aliases.items() if re.search(rf"(?<![\w]){re.escape(key)}(?![\w])", normalized)]
    return max(matches, key=lambda item: len(item[0]))[1] if matches else None


def compile_story(case: dict[str, Any]) -> dict[str, Any]:
    extra_fields = set(case).difference({"name", "story", "apps"})
    if extra_fields:
        raise ValueError(f"Unknown NLP case fields: {', '.join(sorted(extra_fields))}")
    name = case.get("name")
    story = case.get("story")
    app_table = case.get("apps")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("NLP case must define a non-empty name")
    if not isinstance(story, str) or not story.strip():
        raise ValueError("NLP case must define a non-empty story")
    if not isinstance(app_table, dict) or not app_table:
        raise ValueError("NLP case must map app aliases to Android package names in [apps]")

    aliases: dict[str, dict[str, Any]] = {}
    for alias, app_config in app_table.items():
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError("Every [apps] entry must have a non-empty alias")
        if isinstance(app_config, str):
            package = app_config
            selectors: dict[str, Any] = {}
        elif isinstance(app_config, dict):
            package = app_config.get("package")
            selectors = {key: app_config[key] for key in ("activity", "search_text", "home_text") if key in app_config}
            unknown = set(app_config).difference({"package", "activity", "search_text", "home_text"})
            if unknown:
                raise ValueError(f"Unknown [apps.{alias}] settings: {', '.join(sorted(unknown))}")
            if any(not isinstance(value, str) or not value.strip() for value in selectors.values()):
                raise ValueError(f"[apps.{alias}] activity, search_text and home_text values must be non-empty strings")
        else:
            raise ValueError(f"[apps] entry {alias} must be a package string or an app table")
        if not isinstance(package, str) or not package.strip():
            raise ValueError(f"[apps.{alias}] must define a non-empty package")
        aliases[_normalize(alias)] = {"name": alias.strip(), "package": package.strip(), **selectors}

    steps: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    last_query: str | None = None
    active_app: dict[str, Any] | None = None

    def app_step(operation: str, app: dict[str, Any]) -> dict[str, Any]:
        step = {operation: app["package"], "timeout_seconds": 4}
        if app.get("activity"):
            step["activity"] = app["activity"]
        return step

    for index, clause in enumerate(_split_instructions(story), 1):
        normalized = _normalize(clause)
        url_step = re.match(r"^(?:open|visit|navigate to)\s+(https?://\S+?)(?:\s+in\s+(.+))?$", clause, re.IGNORECASE)
        if url_step:
            url = url_step.group(1).rstrip(".,;)")
            app = _app_for_clause(url_step.group(2), aliases) if url_step.group(2) else None
            if url_step.group(2) and not app:
                issues.append({"code": "APP_ALIAS_REQUIRED", "instruction": clause, "message": "Add the requested browser/app and its package to the [apps] map."})
            else:
                step = {"open_url": url, "timeout_seconds": 4}
                if app:
                    active_app = app
                    step["package"] = app["package"]
                steps.append(step)
                continue

        if re.fullmatch(r"(?:open|launch|start)\s+(?:the\s+)?(?:app\s+)?[\w .+-]+", clause, re.IGNORECASE):
            app = _app_for_clause(clause, aliases)
            if app:
                active_app = app
                steps.append(app_step("open_app", app))
                continue

        if re.match(r"^(?:switch|return|go back)\s+to\s+", clause, re.IGNORECASE):
            app = _app_for_clause(clause, aliases)
            if app:
                active_app = app
                steps.append(app_step("open_app", app))
                continue

        if re.match(r"^(?:restart|relaunch|force.stop|close and reopen|stop and reopen)\b", clause, re.IGNORECASE):
            app = _app_for_clause(clause, aliases) or active_app
            if app:
                active_app = app
                steps.append(app_step("relaunch_app", app))
                continue

        search = re.match(r"^(?:search for|look up|find)\s+(.+)$", clause, re.IGNORECASE)
        if search and active_app:
            query = search.group(1).strip().strip("\"'“”‘’")
            query = re.sub(r"\s+in\s+(?:the\s+)?(?:app|application)$", "", query, flags=re.IGNORECASE).strip()
            if query:
                last_query = query
                if active_app.get("search_text"):
                    steps.append({"tap_text": active_app["search_text"], "timeout_seconds": 2})
                else:
                    steps.append({"goal": f"Open and focus the search field in {active_app['name']} to search for {query!r}", "max_steps": 8})
                steps.extend([
                    {"type_text": query},
                    {"enter": True},
                    {"assert_text": query, "timeout_seconds": 2},
                ])
                continue

        title = re.match(r"^(?:open|select|view)\s+(?:the\s+)?(?:(\d{4})\s+)?(?:movie\s+)?(?:title|movie|film)(?:\s+page)?$", clause, re.IGNORECASE)
        if title and active_app and last_query:
            year = title.group(1)
            detail = f"{year} " if year else ""
            steps.extend([
                {"goal": f"Open the {detail}movie title page for {last_query!r} in {active_app['name']}", "max_steps": 8},
                {"assert_text": last_query, "timeout_seconds": 2},
            ])
            continue

        if re.match(r"^(?:press|hit)\s+(?:enter|return|submit)$", clause, re.IGNORECASE):
            steps.append({"enter": True})
            continue

        if re.match(r"^go\s+home$|^press\s+home$", clause, re.IGNORECASE):
            steps.append({"home": True})
            continue

        if re.match(r"^(?:go\s+)?back$", clause, re.IGNORECASE):
            steps.append({"back": True})
            continue

        tap = re.match(r"^(?:if\s+(?:it\s+is\s+)?visible[, ]+)?tap\s+(?:on\s+)?(?:the\s+)?[\"'“”](.+?)[\"'“”]$", clause, re.IGNORECASE)
        if tap:
            optional = normalized.startswith("if visible") or normalized.startswith("if it is visible")
            steps.append({"tap_text": tap.group(1), "timeout_seconds": 0.3 if optional else 1.5, "optional": optional})
            continue

        typing = re.match(r"^(?:type|enter text|write)\s+[\"'“”](.+?)[\"'“”]$", clause, re.IGNORECASE)
        if typing:
            steps.append({"type_text": typing.group(1)})
            continue

        assertion = re.match(r"^(?:verify|check|assert)\s+.+?\bshows?\s+[\"'“”](.+?)[\"'“”]$", clause, re.IGNORECASE)
        if assertion:
            steps.append({"assert_text": assertion.group(1), "timeout_seconds": 2})
            continue

        home_check = re.match(r"^(?:verify|check|assert)\s+(.+?)\s+home(?:\s+screen)?$", clause, re.IGNORECASE)
        if home_check:
            app = _app_for_clause(home_check.group(1), aliases) or active_app
            if app:
                if app.get("home_text"):
                    steps.append({"assert_text": app["home_text"], "timeout_seconds": 2})
                else:
                    steps.append({"goal": f"Show the home screen in {app['name']}", "max_steps": 6})
                continue

        wait = re.match(r"^wait\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds?|seconds?|secs?)$", clause, re.IGNORECASE)
        if wait:
            amount = float(wait.group(1))
            unit = wait.group(2).lower()
            milliseconds = round(amount if unit.startswith("m") else amount * 1000)
            steps.append({"wait_ms": milliseconds})
            continue

        if not active_app and re.match(r"^(?:open|launch|start)\b", clause, re.IGNORECASE):
            issues.append({"code": "APP_ALIAS_REQUIRED", "instruction": clause, "message": "Add this app and its Android package to the [apps] map."})
        else:
            issues.append({"code": "UNSUPPORTED_INSTRUCTION", "instruction": clause, "message": "No action was generated for this instruction. Rewrite it using a supported action or split it into a Jev goal."})

    if issues:
        raise NlpCompileError(issues)
    if not steps:
        raise NlpCompileError([{"code": "EMPTY_PLAN", "instruction": story, "message": "The story did not produce any executable steps."}])
    return {"name": name.strip(), "steps": steps, "source_story": story}
