"""Safe starter workflows that map directly to current engine behavior."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


WORKFLOW_TEMPLATES: dict[str, dict[str, Any]] = {
    "research-summary": {
        "name": "research-summary",
        "description": "Research a topic and prepare a concise summary.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "title": "Research topic"},
                "audience": {"type": "string", "title": "Audience", "default": "General"},
            },
            "required": ["topic"],
        },
        "start": "research",
        "nodes": {
            "research": {"type": "task", "prompt": "Research the requested topic using the supplied inputs.", "next": "summarize"},
            "summarize": {"type": "task", "prompt": "Produce a concise, cited summary for the requested audience.", "next": "done"},
            "done": {"type": "end"},
        },
    },
    "daily-briefing": {
        "name": "daily-briefing",
        "description": "Prepare a daily briefing and pause for explicit review.",
        "input_schema": {
            "type": "object",
            "properties": {"date": {"type": "string", "title": "Briefing date", "default": "today"}},
            "required": ["date"],
        },
        "start": "prepare",
        "nodes": {
            "prepare": {"type": "task", "prompt": "Prepare the daily briefing from available run inputs.", "next": "review"},
            "review": {"type": "human", "payload": {"action": "review_briefing"}, "next": "done"},
            "done": {"type": "end"},
        },
    },
    "compare-options": {
        "name": "compare-options",
        "description": "Compare alternatives against explicit criteria.",
        "input_schema": {
            "type": "object",
            "properties": {
                "options": {"type": "string", "title": "Options"},
                "criteria": {"type": "string", "title": "Decision criteria"},
            },
            "required": ["options", "criteria"],
        },
        "start": "compare",
        "nodes": {
            "compare": {"type": "task", "prompt": "Compare the options against the supplied criteria.", "next": "done"},
            "done": {"type": "end"},
        },
    },
    "deployment-plan": {
        "name": "deployment-plan",
        "description": "Prepare a deployment plan without executing deployment commands.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "title": "Service"},
                "environment": {"type": "string", "title": "Environment", "default": "staging"},
            },
            "required": ["service"],
        },
        "start": "plan",
        "nodes": {
            "plan": {"type": "task", "prompt": "Prepare a rollback-aware deployment plan. Do not execute commands.", "next": "review"},
            "review": {"type": "human", "payload": {"action": "review_deployment_plan"}, "next": "done"},
            "done": {"type": "end"},
        },
    },
}


def list_templates() -> list[dict[str, Any]]:
    return [
        {"id": template_id, "name": value["name"], "description": value["description"], "definition": deepcopy(value)}
        for template_id, value in WORKFLOW_TEMPLATES.items()
    ]


def get_template(template_id: str) -> dict[str, Any] | None:
    value = WORKFLOW_TEMPLATES.get(template_id)
    return deepcopy(value) if value is not None else None
