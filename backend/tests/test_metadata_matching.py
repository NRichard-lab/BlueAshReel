from __future__ import annotations

import pytest

from app.metadata.matching import choose_match, normalize_title
from app.metadata.types import Candidate
from app.services.filename_parser import parse_filename


def movie(identifier: str, title: str, year: int | None = None, **kwargs: object) -> Candidate:
    return Candidate("tmdb", identifier, "movie", title, year=year, **kwargs)  # type: ignore[arg-type]


def test_exact_title_year_beats_first_search_result() -> None:
    candidates = [movie("2", "The Matrix Reloaded", 2003), movie("603", "The Matrix", 1999)]
    decision = choose_match("The Matrix", 1999, candidates)
    assert decision.status == "matched" and decision.confidence == 1.0
    assert decision.candidate == candidates[1] and decision.method == "automatic_title_year"


@pytest.mark.parametrize(
    ("local", "remote"),
    [
        ("Spider-Man: No Way Home", "Spider Man No Way Home"),
        ("Amelie", "Amélie"),
        ("Mr. & Mrs. Smith", "Mr and Mrs Smith"),
        ("Schindler's List", "Schindlers List"),
    ],
)
def test_title_punctuation_and_accents_are_normalized(local: str, remote: str) -> None:
    assert normalize_title(local) == normalize_title(remote)
    assert choose_match(local, 2000, [movie("1", remote, 2000)]).status == "matched"


def test_original_and_alternate_titles_can_establish_identity() -> None:
    original = movie("1", "Spirited Away", 2001, original_title="Sen to Chihiro no kamikakushi")
    alternate = movie("2", "Live Die Repeat", 2014, alternate_titles=("Edge of Tomorrow",))
    assert choose_match("Sen to Chihiro no kamikakushi", 2001, [original]).method == "automatic_alternate_title_year"
    assert choose_match("Edge of Tomorrow", 2014, [alternate]).status == "matched"


def test_wrong_year_remake_is_not_matched() -> None:
    decision = choose_match("The Thing", 1982, [movie("1", "The Thing", 2011)])
    assert decision.status == "unmatched" and decision.candidate is None and decision.confidence < 0.7


def test_one_year_difference_needs_review() -> None:
    assert choose_match("The Film", 2000, [movie("1", "The Film", 2001)]).status == "needs_review"


def test_ambiguous_identical_titles_never_choose_first_result() -> None:
    candidates = [movie("2", "Shared Title", 2000), movie("1", "Shared Title", 2000)]
    decision = choose_match("Shared Title", 2000, candidates)
    reversed_decision = choose_match("Shared Title", 2000, reversed(candidates))
    assert decision == reversed_decision and decision.status == "needs_review" and decision.method == "ambiguous"


def test_low_confidence_and_type_mismatch_remain_unmatched() -> None:
    assert choose_match("The Matrix", 1999, [movie("1", "A Different Story", 1999)]).status == "unmatched"
    assert (
        choose_match("The Matrix", 1999, [Candidate("tmdb", "1", "series", "The Matrix", year=1999)]).status
        == "unmatched"
    )
    assert choose_match("", None, []).status == "unmatched"


def test_runtime_corroborates_or_rejects_otherwise_matching_title() -> None:
    candidate = movie("603", "The Matrix", 1999, runtime_seconds=8160)
    good = choose_match("The Matrix", 1999, [candidate], runtime_seconds=8100)
    bad = choose_match("The Matrix", 1999, [candidate], runtime_seconds=4000)
    assert good.status == "matched" and good.method == "automatic_title_year_runtime"
    assert bad.status == "needs_review" and bad.confidence < good.confidence


def test_unique_exact_series_without_year_matches_from_sxxeyy_parser() -> None:
    parsed = parse_filename("Ahsoka.S01E05.1080p.mkv", "tv")
    assert (parsed.series_title, parsed.season_number, parsed.episode_number) == ("Ahsoka", 1, 5)
    decision = choose_match(
        parsed.series_title or "",
        parsed.year,
        [Candidate("tmdb", "114461", "series", "Ahsoka", year=2023)],
        kind="series",
    )
    assert decision.status == "matched" and decision.confidence == 0.9


def test_near_title_without_year_does_not_auto_match() -> None:
    assert choose_match("The Matrx", None, [movie("603", "The Matrix", 1999)]).status == "needs_review"


def test_duplicate_search_rows_do_not_make_one_identity_ambiguous() -> None:
    candidate = movie("603", "The Matrix", 1999)
    assert choose_match("The Matrix", 1999, [candidate, candidate]).status == "matched"
