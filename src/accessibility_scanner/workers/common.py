from __future__ import annotations

from ..html_utils import DOMSnapshot, ElementNode


def accessible_name(snapshot: DOMSnapshot, node: ElementNode) -> str:
    if node.attrs.get("aria-label"):
        return node.attrs["aria-label"].strip()
    if node.attrs.get("title"):
        return node.attrs["title"].strip()
    if node.tag == "img" and "alt" in node.attrs:
        return node.attrs.get("alt", "").strip()
    if node.tag == "input" and node.attrs.get("value"):
        return node.attrs["value"].strip()
    text = snapshot.descendants_text(node)
    return text.strip()


def is_interactive(node: ElementNode) -> bool:
    if node.tag in {"button", "select", "textarea"}:
        return True
    if node.tag == "a" and "href" in node.attrs:
        return True
    if node.tag == "input" and node.attrs.get("type", "text").lower() != "hidden":
        return True
    if "tabindex" in node.attrs:
        return True
    role = node.attrs.get("role", "")
    if role in {"button", "link", "checkbox", "radio", "tab", "switch", "textbox", "combobox"}:
        return True
    return False
