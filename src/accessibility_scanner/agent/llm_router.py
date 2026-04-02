"""LLM-powered routing agent that decides what to click / explore next."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an accessibility testing agent controlling a web browser.
You receive a screenshot description and a list of interactive elements on the current page.
Your job is to decide the NEXT action to test accessibility across the application.

Rules:
- Explore every distinct screen/route reachable from the current page.
- Avoid repeating the same action that leads to an already-visited screen.
- Prefer navigation links, menu items, tabs, and buttons that lead to new pages.
- If you see a form, try interacting with it (fill & submit) to reveal validation/error states.
- When there are no more new screens to explore, respond with {"action": "done"}.

IMPORTANT: Your goal is to explore at least 30-50 distinct screens. Be aggressive:
- After visiting a top-nav item, explore ALL its sub-tabs, sub-pages, and filters.
- Click into list items (stocks, orders, holdings) to see detail views.
- Use "back" to return and explore sibling routes.
- Use "scroll" to reveal content below the fold before making decisions.
- Explore: Settings, Profile, Notifications, Help, Footer links, Modal triggers.
- Do NOT say "done" until you have visited 30+ unique screens.

Respond ONLY with valid JSON in one of these formats:
  {"action": "click", "index": <element_index>, "reason": "<why>"}
  {"action": "fill", "index": <element_index>, "value": "<text>", "reason": "<why>"}
  {"action": "navigate", "url": "<url>", "reason": "<why>"}
  {"action": "scroll", "reason": "<why>"}
  {"action": "back", "reason": "<why>"}
  {"action": "done", "reason": "<why>"}
"""


class LLMRouter:
    """Uses the LiteLLM gateway to make navigation decisions."""

    def __init__(self) -> None:
        base_url = os.getenv("LLM_GTWY_BASE_URL", "http://localhost:4000/v1")
        api_key = os.getenv("LLM_GTWY_API_KEY", "sk-placeholder")
        self.model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-6")
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self._history: list[dict[str, str]] = []

    def decide_next_action(
        self,
        page_description: str,
        interactive_elements: list[dict[str, Any]],
        visited_urls: list[str],
        current_url: str,
        exploration_context: str = "",
    ) -> dict[str, Any]:
        elements_text = "\n".join(
            f"  [{i}] <{e['tag']}> "
            f"text={e.get('text', '')!r} "
            f"role={e.get('role', '')} "
            f"href={e.get('href', '')} "
            f"type={e.get('type', '')} "
            f"aria-label={e.get('aria_label', '')}"
            for i, e in enumerate(interactive_elements)
        )

        user_msg = (
            f"Current URL: {current_url}\n"
            f"Page description: {page_description}\n"
            f"Already visited: {json.dumps(visited_urls[-20:])}\n\n"
            f"Interactive elements on page:\n{elements_text}\n\n"
            "What should I do next?"
        )

        self._history.append({"role": "user", "content": user_msg})

        try:
            system_prompt = SYSTEM_PROMPT
            if exploration_context.strip():
                system_prompt = f"{SYSTEM_PROMPT}\n\nProduct-specific exploration guidance:\n{exploration_context.strip()}"
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *self._history[-10:],  # keep context window manageable
                ],
                temperature=0.1,
                max_tokens=300,
            )
            content = resp.choices[0].message.content.strip()
            self._history.append({"role": "assistant", "content": content})

            # Parse JSON — handle markdown-wrapped responses
            match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if match:
                content = match.group(1)
            else:
                start = content.find('{')
                end = content.rfind('}')
                if start != -1 and end != -1:
                    content = content[start:end+1]
            return json.loads(content)
        except Exception as e:
            logger.error("LLM routing error: %s", e)
            return {"action": "done", "reason": f"LLM error: {e}"}

    def summarize_screen(self, page_info: dict[str, Any]) -> str:
        """Ask LLM to describe the current screen for accessibility context."""
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "Describe this web page screen in 2-3 sentences for accessibility audit purposes.",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(page_info, default=str)[:3000],
                    },
                ],
                temperature=0.1,
                max_tokens=200,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.warning("LLM summarize error: %s", e)
            return f"Screen at {page_info.get('url', 'unknown')}"

    def filter_step_actions(self, page_info: dict[str, Any], interactive_elements: list[dict[str, Any]], planned_actions: list[dict[str, Any]]) -> list[int]:
        """Ask LLM which predefined actions to execute based on the current screen."""
        if not planned_actions:
            return []
            
        elements_text = "\n".join(
            f"  <{e['tag']}> text={e.get('text', '')!r} role={e.get('role', '')} aria-label={e.get('aria_label', '')}" 
            for e in interactive_elements[:50]
        )
        actions_text = "\n".join(
            f"  [{i}] type={a['type']} target={a.get('description', '')}" 
            for i, a in enumerate(planned_actions)
        )
        
        prompt = f"""
You are an accessibility automation agent. Your task is to look at a user-interface and decide which predefined actions from a list are currently REQUIRED.

Screenshot summary: {page_info.get('title', '')} - {page_info.get('url', '')}
Visible elements overview:
{elements_text}

Planned actions sequence:
{actions_text}

Which of the planned actions MUST be executed on this screen right now?
For example, if an action says "Read and enter captcha" but there is NO captcha image visibly mentioned in the elements, you MUST omit it.
Respond with ONLY a JSON array of integer indices representing the actions to take. Example: [0, 2]
"""
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=50,
            )
            content = resp.choices[0].message.content.strip()
            # Extract JSON array
            match = re.search(r'\[.*?\]', content, re.DOTALL)
            if match:
                content = match.group(0)
            indices = json.loads(content)
            if isinstance(indices, list):
                return [i for i in indices if isinstance(i, int) and 0 <= i < len(planned_actions)]
            return list(range(len(planned_actions)))
        except Exception as e:
            logger.warning("LLM action filter error: %s", e)
            # Fallback to returning all planned actions
            return list(range(len(planned_actions)))
