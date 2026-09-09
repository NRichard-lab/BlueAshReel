from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.metadata.types import Candidate

AUTO_MATCH_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.70
MINIMUM_MARGIN = 0.08


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title.casefold()).replace("&", " and ")
    text = "".join(char for char in text if not unicodedata.combining(char))
    # Apostrophes join words; other punctuation separates them.
    text = re.sub(r"['\u2019]", "", text)
    return " ".join(re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).split())


@dataclass(frozen=True)
class MatchDecision:
    status: str
    candidate: Candidate | None
    confidence: float
    method: str


def score_candidate(
    title: str,
    year: int | None,
    candidate: Candidate,
    *,
    kind: str = "movie",
    runtime_seconds: float | None = None,
) -> tuple[float, str]:
    wanted = normalize_title(title)
    if not wanted or candidate.kind != kind:
        return 0.0, "type_or_title_mismatch"
    titles = (candidate.title, candidate.original_title or "", *candidate.alternate_titles)
    similarities = [SequenceMatcher(None, wanted, normalize_title(value), autojunk=False).ratio() for value in titles]
    similarity = max(similarities)
    title_method = "title" if similarities.index(similarity) == 0 else "alternate_title"
    score = 0.8 * similarity
    if year is not None and candidate.year is not None:
        difference = abs(year - candidate.year)
        score += 0.2 if difference == 0 else (0.08 if difference == 1 else 0)
        method = f"{title_method}_year"
        if difference > 1:
            score = min(score, 0.69)
    else:
        score += 0.1 if year is None else 0.08
        method = title_method
    if runtime_seconds and runtime_seconds > 0 and candidate.runtime_seconds and candidate.runtime_seconds > 0:
        delta = abs(runtime_seconds - candidate.runtime_seconds) / max(runtime_seconds, candidate.runtime_seconds)
        if delta > 0.05:
            score -= min(0.25, delta * 0.6)
        method += "_runtime"
    # A near spelling match without corroborating year is reviewable, not automatic.
    if similarity < 0.96:
        score = min(score, 0.89)
    return round(max(0.0, min(1.0, score)), 4), method


def choose_match(
    title: str,
    year: int | None,
    candidates: Iterable[Candidate],
    *,
    kind: str = "movie",
    runtime_seconds: float | None = None,
) -> MatchDecision:
    """Deterministic, conservative scoring; result order/popularity do not decide identity."""
    unique = {(candidate.provider, candidate.provider_id): candidate for candidate in candidates}
    scored = [
        (*score_candidate(title, year, candidate, kind=kind, runtime_seconds=runtime_seconds), candidate)
        for candidate in unique.values()
    ]
    scored.sort(key=lambda row: (-row[0], row[2].provider, row[2].provider_id))
    if not scored or scored[0][0] < REVIEW_THRESHOLD:
        return MatchDecision("unmatched", None, scored[0][0] if scored else 0.0, "insufficient_confidence")
    score, method, candidate = scored[0]
    margin = score - scored[1][0] if len(scored) > 1 else 1.0
    if score >= AUTO_MATCH_THRESHOLD and margin + 1e-9 >= MINIMUM_MARGIN:
        return MatchDecision("matched", candidate, score, f"automatic_{method}")
    return MatchDecision("needs_review", candidate, score, "ambiguous" if margin < MINIMUM_MARGIN else method)
