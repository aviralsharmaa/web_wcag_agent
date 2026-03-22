from __future__ import annotations

from html.parser import HTMLParser


class _StackParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: int = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        self.stack.append(tag.lower())

    def handle_endtag(self, tag: str) -> None:
        norm = tag.lower()
        if not self.stack:
            self.errors += 1
            return
        if self.stack[-1] == norm:
            self.stack.pop()
            return
        # Best-effort mismatch accounting.
        self.errors += 1
        if norm in self.stack:
            while self.stack and self.stack[-1] != norm:
                self.stack.pop()
            if self.stack:
                self.stack.pop()


class ParserValidatorWorker:
    def analyze(self, html: str) -> dict:
        parser = _StackParser()
        parser.feed(html)
        parser.close()
        errors = parser.errors + len(parser.stack)
        return {"parsing_errors": errors}
