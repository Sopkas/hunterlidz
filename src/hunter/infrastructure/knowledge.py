"""Team documents as AI context. (H-04)

The documents live outside the repository (HUNTER_KNOWLEDGE_DIR, default `documents/`):
the repository is public and the documents contain prices and sales strategy.
They are found by their number prefix, so a renamed title does not break the lookup.
"""

from __future__ import annotations

from pathlib import Path

from hunter.application.errors import ErrorCode, HunterError

MAX_DOCUMENT_CHARS = 40_000


class FileKnowledgeBase:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def documents(self, prefixes: tuple[str, ...]) -> dict[str, str]:
        found: dict[str, str] = {}
        for prefix in prefixes:
            matches = (
                sorted(self.directory.glob(f"{prefix}-*.md")) if self.directory.is_dir() else []
            )
            if not matches:
                raise HunterError(
                    ErrorCode.INVALID_INPUT,
                    f"в базе знаний {self.directory} нет документа {prefix}-*.md "
                    "(задайте HUNTER_KNOWLEDGE_DIR)",
                )
            path = matches[0]
            text = path.read_text(encoding="utf-8-sig")
            if len(text) > MAX_DOCUMENT_CHARS:
                raise HunterError(ErrorCode.INVALID_INPUT, f"документ {path.name} слишком большой")
            found[path.name] = text
        return found
