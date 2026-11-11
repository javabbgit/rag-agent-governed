"""Corpus parsing: facts, manifest, documents and chunks. Pure: no network, no database.

A document is a Markdown file with TOML front matter between two "+++" lines, one H1, and H2
sections. Each section is one chunk. Text between the H1 and the first H2 is the "summary" chunk.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FENCE = "+++"
SUMMARY = "summary"


@dataclass(frozen=True)
class Section:
    chunk_id: str
    doc_id: str
    heading: str
    text: str
    word_count: int


@dataclass(frozen=True)
class Doc:
    front_matter: dict[str, Any]
    title: str | None
    sections: list[Section] = field(default_factory=list)
    h1_count: int = 0
    h2_headings: list[str] = field(default_factory=list)
    body: str = ""
    path: Path | None = None


def load_facts(path: str | Path) -> dict[str, Any]:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def load_manifest(path: str | Path) -> dict[str, Any]:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def slugify(heading: str) -> str:
    """Lower-case; each run of characters outside [a-z0-9] becomes "-"; strip "-"."""
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


def split_front_matter(raw: str) -> tuple[dict[str, Any], str]:
    """Return (front matter dict, body). No front matter gives ({}, raw)."""
    lines = raw.splitlines()
    if not lines or lines[0].strip() != FENCE:
        return {}, raw
    for i in range(1, len(lines)):
        if lines[i].strip() == FENCE:
            fm = tomllib.loads("\n".join(lines[1:i]))
            return fm, "\n".join(lines[i + 1 :])
    raise ValueError("front matter is not closed by a second '+++' line")


def _heading_level(line: str) -> tuple[int, str] | None:
    m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
    return (len(m.group(1)), m.group(2)) if m else None


def parse_text(raw: str, path: Path | None = None) -> Doc:
    fm, body = split_front_matter(raw)
    doc_id = str(fm.get("id", path.stem if path else ""))
    title: str | None = None
    h1_count = 0
    h2_headings: list[str] = []
    # blocks: (heading, lines) where the first block is the summary
    blocks: list[tuple[str, list[str]]] = [(SUMMARY, [])]
    in_fence = False
    seen_h1 = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        level = None if in_fence else _heading_level(line)
        if level and level[0] == 1:
            h1_count += 1
            if title is None:
                title = level[1]
            seen_h1 = True
            continue
        if level and level[0] == 2:
            h2_headings.append(level[1])
            blocks.append((level[1], [line]))
            continue
        if seen_h1 or len(blocks) > 1:
            blocks[-1][1].append(line)
    sections: list[Section] = []
    for heading, lines in blocks:
        text = "\n".join(lines).strip()
        if heading == SUMMARY and not text:
            continue
        slug = SUMMARY if heading == SUMMARY else slugify(heading)
        sections.append(
            Section(
                chunk_id=f"{doc_id}#{slug}",
                doc_id=doc_id,
                heading=heading,
                text=text,
                word_count=len(text.split()),
            )
        )
    return Doc(
        front_matter=fm,
        title=title,
        sections=sections,
        h1_count=h1_count,
        h2_headings=h2_headings,
        body=body,
        path=path,
    )


def parse_doc(path: str | Path) -> Doc:
    p = Path(path)
    return parse_text(p.read_text(encoding="utf-8"), p)


def iter_chunks(docs_dir: str | Path) -> Iterator[Section]:
    """All sections of all documents, ordered by doc id (file stem), then position."""
    for path in sorted(Path(docs_dir).glob("*.md"), key=lambda p: p.stem):
        yield from parse_doc(path).sections
