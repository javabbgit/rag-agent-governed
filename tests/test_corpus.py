"""Unit tests for rag_agent.corpus (no network)."""

from pathlib import Path

import pytest

from rag_agent import corpus

FIXTURES = Path(__file__).parent / "fixtures" / "corpus" / "docs"
REPO = Path(__file__).resolve().parent.parent

SAMPLE = """+++
id = "POL-001"
title = "Sample"
+++

# Sample

Summary line here.

## First Part

Body one.

### Deeper

Stays inside the first part.

## Second: Part (A)

Body two.
"""


def test_slugify():
    assert corpus.slugify("Second: Part (A)") == "second-part-a"
    assert corpus.slugify("  --Hello__World--  ") == "hello-world"
    assert corpus.slugify("GDS/TDS Limits") == "gds-tds-limits"


def test_front_matter_parsed():
    doc = corpus.parse_text(SAMPLE)
    assert doc.front_matter == {"id": "POL-001", "title": "Sample"}
    assert doc.title == "Sample"
    assert doc.h1_count == 1


def test_sections_split_summary_h2_h3():
    doc = corpus.parse_text(SAMPLE)
    assert [s.chunk_id for s in doc.sections] == [
        "POL-001#summary",
        "POL-001#first-part",
        "POL-001#second-part-a",
    ]
    assert doc.sections[0].text == "Summary line here."
    first = doc.sections[1]
    assert first.text.startswith("## First Part")
    assert "### Deeper" in first.text and "Stays inside" in first.text
    assert "Body two" not in first.text
    assert first.word_count == len(first.text.split())


def test_unclosed_front_matter_raises():
    with pytest.raises(ValueError):
        corpus.parse_text("+++\nid = 'x'\n# T\n")


def test_headings_in_code_fence_ignored():
    raw = SAMPLE + "\n```\n## Not a heading\n```\n"
    assert len(corpus.parse_text(raw).h2_headings) == 2


def test_iter_chunks_deterministic_order():
    ids = [c.chunk_id for c in corpus.iter_chunks(FIXTURES)]
    assert ids == sorted(ids, key=lambda i: (i.split("#")[0], 0)) or ids[0].startswith("POL-001#")
    assert ids[0] == "POL-001#summary"
    assert ids == [c.chunk_id for c in corpus.iter_chunks(FIXTURES)]
    docs = [i.split("#")[0] for i in ids]
    assert docs == sorted(docs)


def test_real_manifest_has_40_docs():
    m = corpus.load_manifest(REPO / "corpus" / "manifest.toml")
    ids = [d["id"] for d in m["docs"]]
    assert len(ids) == 40
    assert len(set(ids)) == 40
    assert sum(d["status"] == "superseded" for d in m["docs"]) == 3
    assert all(d["status"] == "superseded" for d in m["docs"] if d["id"].endswith("-V1"))
    assert {d["batch"] for d in m["docs"]} == {"B1", "B2", "B3", "B4", "B5"}
    assert all(t == t.lower() for t in m["out_of_scope"]["banned_terms"])


def test_real_facts_types():
    f = corpus.load_facts(REPO / "corpus" / "facts.toml")
    assert f["rates"]["VR5C_spread_pts"] == -0.55
    assert f["ratios"]["gds_max_pct"] == 35.0
    assert f["v1"]["ratios"]["gds_max_pct"] == 33.0
    assert "buffer_pts" not in f.get("v1", {}).get("qualifying", {})
