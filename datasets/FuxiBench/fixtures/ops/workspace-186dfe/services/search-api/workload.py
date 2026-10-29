"""Search-service workload: tokenise a synthetic catalogue, build an inverted
index and rank keyword queries.

Standard library only. The corpus is generated deterministically at import
time, so the benchmark is offline and repeatable.
"""

from __future__ import annotations

import re

_WORDS = (
    "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu "
    "nu xi omicron pi rho sigma tau upsilon phi chi psi omega"
).split()

_TOKEN_RE = re.compile(r"[a-z0-9-]+")

CORPUS = [
    "doc-{:04d} {}".format(
        doc_id,
        " ".join(_WORDS[(doc_id * 7 + position * 5) % len(_WORDS)] for position in range(28)),
    )
    for doc_id in range(400)
]

QUERIES = ("alpha beta", "gamma delta", "sigma tau", "omega psi", "epsilon zeta")


def tokenize(text):
    return _TOKEN_RE.findall(text.lower())


def build_index(corpus=CORPUS):
    index = {}
    for doc_id, text in enumerate(corpus):
        for term in sorted(set(tokenize(text))):
            index.setdefault(term, []).append(doc_id)
    return index


_INDEX = build_index()


def rank(query, index=None):
    index = _INDEX if index is None else index
    scores = {}
    for term in tokenize(query):
        for doc_id in index.get(term, ()):
            scores[doc_id] = scores.get(doc_id, 0) + 1
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _brute_rank(query):
    terms = tokenize(query)
    scores = {}
    for doc_id, text in enumerate(CORPUS):
        terms_in_doc = set(tokenize(text))
        hits = sum(1 for term in terms if term in terms_in_doc)
        if hits:
            scores[doc_id] = hits
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def check():
    index = build_index()
    if not index:
        raise AssertionError("inverted index is empty")
    for query in QUERIES:
        ranked = rank(query, index)
        if not ranked:
            raise AssertionError(f"query {query!r} matched no documents")
        if ranked != _brute_rank(query):
            raise AssertionError(f"ranking mismatch for query {query!r}")
    return True


CASES = {
    "tokenize_corpus": lambda: [tokenize(document) for document in CORPUS],
    "build_inverted_index": build_index,
    "rank_five_queries": lambda: [rank(query) for query in QUERIES],
}
