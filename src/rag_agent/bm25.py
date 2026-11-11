"""Tokenizer and Okapi BM25, pure Python. No database, no network, no writes."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Collection, Container, Mapping

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")


def _plural(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str, stopwords: Collection[str] | None = None) -> list[str]:
    """Tokens of `text`, used for chunks and queries alike.

    Lower-case; a token with "-" is kept whole and followed by each "-" part of length >= 2;
    stopwords are dropped; a plural rule is applied; order and duplicates are kept.
    `stopwords=None` means retrieve.STOPWORDS (the one parameter block).
    """
    if stopwords is None:
        from rag_agent.retrieve import STOPWORDS  # lazy: retrieve imports this module

        stopwords = STOPWORDS
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text.lower()):
        out.append(tok)
        if "-" in tok:
            out.extend(p for p in tok.split("-") if len(p) >= 2)
    return [_plural(t) for t in out if t not in stopwords]


class BM25Index:
    """Okapi BM25 over `docs` (chunk_id -> text)."""

    def __init__(
        self,
        docs: Mapping[str, str],
        k1: float,
        b: float,
        stopwords: Collection[str] | None = None,
    ):
        self.k1 = k1
        self.b = b
        self.stopwords = stopwords
        self._tf: dict[str, Counter[str]] = {}
        self._len: dict[str, int] = {}
        df: Counter[str] = Counter()
        for chunk_id, text in docs.items():
            tokens = tokenize(text, stopwords)
            tf = Counter(tokens)
            self._tf[chunk_id] = tf
            self._len[chunk_id] = len(tokens)
            df.update(tf.keys())
        self.n_docs = len(docs)
        self.avgdl = sum(self._len.values()) / self.n_docs if self.n_docs else 0.0
        self._idf = {t: math.log(1 + (self.n_docs - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def scores(self, query: str) -> dict[str, float]:
        """Score of every chunk with score > 0, over the DISTINCT query terms."""
        terms = sorted({t for t in tokenize(query, self.stopwords) if t in self._idf})
        out: dict[str, float] = {}
        for chunk_id, tf in self._tf.items():
            norm = 1 - self.b + self.b * self._len[chunk_id] / self.avgdl
            score = 0.0
            for t in terms:
                f = tf.get(t)
                if f:
                    score += self._idf[t] * f * (self.k1 + 1) / (f + self.k1 * norm)
            if score > 0:
                out[chunk_id] = score
        return out

    def top(
        self, query: str, n: int, keep: Container[str] | Callable[[str], bool] | None = None
    ) -> list[tuple[str, float]]:
        """Best `n` (chunk_id, score), score desc, ties by chunk_id.

        `keep` filters AFTER scoring, so idf never depends on it.
        """
        scored = self.scores(query)
        if keep is not None:
            ok = keep if callable(keep) else keep.__contains__
            scored = {c: s for c, s in scored.items() if ok(c)}
        return sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
