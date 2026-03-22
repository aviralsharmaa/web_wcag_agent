from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable


@dataclass
class ElementNode:
    tag: str
    attrs: dict[str, str]
    parent: int | None
    text_chunks: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(chunk.strip() for chunk in self.text_chunks if chunk.strip()).strip()


class DOMParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[ElementNode] = []
        self._stack: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = ElementNode(
            tag=tag.lower(),
            attrs={k.lower(): (v or "") for k, v in attrs},
            parent=self._stack[-1] if self._stack else None,
        )
        self.nodes.append(node)
        self._stack.append(len(self.nodes) - 1)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._stack:
            self.nodes[self._stack[-1]].text_chunks.append(data)


@dataclass
class DOMSnapshot:
    nodes: list[ElementNode]

    @classmethod
    def from_html(cls, html: str) -> "DOMSnapshot":
        parser = DOMParser()
        parser.feed(html)
        parser.close()
        return cls(nodes=parser.nodes)

    def find(self, tag: str) -> list[ElementNode]:
        norm = tag.lower()
        return [node for node in self.nodes if node.tag == norm]

    def find_by_attr(self, tag: str | None, attr_name: str) -> list[ElementNode]:
        attr = attr_name.lower()
        return [
            node
            for node in self.nodes
            if (tag is None or node.tag == tag.lower()) and attr in node.attrs
        ]

    def has_ancestor_tag(self, node: ElementNode, tags: Iterable[str]) -> bool:
        indices = {name.lower() for name in tags}
        cur = node.parent
        while cur is not None:
            parent = self.nodes[cur]
            if parent.tag in indices:
                return True
            cur = parent.parent
        return False

    def descendants_text(self, node: ElementNode) -> str:
        idx = self.nodes.index(node)
        chunks = [node.text]
        for child in self._children_of(idx):
            if child.text:
                chunks.append(child.text)
        return " ".join(chunk for chunk in chunks if chunk).strip()

    def _children_of(self, parent_idx: int) -> list[ElementNode]:
        children: list[ElementNode] = []
        for idx, node in enumerate(self.nodes):
            if node.parent == parent_idx:
                children.append(node)
                children.extend(self._children_of(idx))
        return children


def parse_style(style_value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in style_value.split(";"):
        if ":" not in pair:
            continue
        key, value = pair.split(":", 1)
        out[key.strip().lower()] = value.strip().lower()
    return out


def visible_text(html: str) -> str:
    snap = DOMSnapshot.from_html(html)
    parts = [node.text for node in snap.nodes if node.text]
    return " ".join(parts).strip().lower()
