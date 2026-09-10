import pytest

from app.services.filename_parser import parse_filename


@pytest.mark.parametrize(("filename", "title", "year"), [
    ("1917.2019.1080p.BluRay.x264.AAC5.1-[YTS.MX].mp4", "1917", 2019),
    ("1917 2019 1080p BluRay x264 AAC5 1 - [YTS MX].mkv", "1917", 2019),
    ("1984 (1984).mkv", "1984", 1984),
    ("1984.1984.mkv", "1984", 1984),
    ("2001.A.Space.Odyssey.1968.1080p.mkv", "2001 A Space Odyssey", 1968),
    ("2012 (2009).mkv", "2012", 2009),
    ("1917.mkv", "1917", None),
    ("1984.1080p.mkv", "1984", None),
    ("2001 A Space Odyssey.mkv", "2001 A Space Odyssey", None),
    ("300 (2006).mkv", "300", 2006),
    ("12 Strong (2018).mkv", "12 Strong", 2018),
    ("A Dog's Purpose (2017).mkv", "A Dog's Purpose", 2017),
    ("A Violent Separation (2019).mkv", "A Violent Separation", 2019),
])
def test_movie_title_and_release_year(filename, title, year):
    parsed = parse_filename(filename, "movies")
    assert (parsed.kind, parsed.title, parsed.year) == ("movie", title, year)


@pytest.mark.parametrize("filename", ["Show Name S01E01.ext", "Show Name - 1x01.ext"])
def test_episode_numbering_unchanged(filename):
    parsed = parse_filename(filename, "tv")
    assert (parsed.kind, parsed.series_title, parsed.season_number, parsed.episode_number) == (
        "episode", "Show Name", 1, 1,
    )
