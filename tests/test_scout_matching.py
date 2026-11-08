"""Fixture matching: the step that decides which match a bet is graded against."""

from backend.agents.scout_agent import _fold, _merge_fd_ids, _teams_match


def _fd(fid, home, away, utc, code="PL"):
    return {
        "id": fid,
        "utcDate": utc,
        "homeTeam": {"id": (fid * 10) if fid else None, "name": home},
        "awayTeam": {"id": (fid * 10 + 1) if fid else None, "name": away},
        "competition": {"code": code},
    }


def _fx(home, away, utc, code="PL"):
    return {
        "id": "odds-event-id",
        "utcDate": utc,
        "homeTeam": {"id": None, "name": home},
        "awayTeam": {"id": None, "name": away},
        "_competition_code": code,
    }


def test_fold_strips_accents_and_punctuation():
    assert _fold("Borussia Mönchengladbach") == "borussia monchengladbach"
    assert _fold("1. FC Köln") == "1 fc koln"
    assert _fold("Nott'm Forest") == "nott m forest"


def test_accent_variants_still_match():
    assert _teams_match("Borussia Mönchengladbach", "Borussia Monchengladbach")


def test_short_generic_tokens_do_not_bind_two_different_clubs():
    # These are the pairs a substring test merges. Binding the wrong one writes
    # the wrong fixture_id, and the bet is then graded against another team's
    # match — silently. A token has to carry enough signal on its own.
    assert not _teams_match("Real", "Real Madrid")
    assert not _teams_match("Real", "Real Sociedad")
    assert not _teams_match("Man", "Man City")

    # Legal suffixes are stripped, so these are the same club.
    assert _teams_match("Arsenal FC", "Arsenal")
    assert _teams_match("Sporting CP", "Sporting Lisbon") is False  # not an alias to guess
    assert _teams_match("Inter Milan", "Inter")  # 'inter' is 5 chars: allowed


def test_correct_fixture_is_matched_and_ids_attached():
    out = _merge_fd_ids(
        [_fx("Arsenal FC", "Burnley FC", "2026-11-08T15:00:00Z")],
        [_fd(101, "Arsenal FC", "Burnley FC", "2026-11-08T15:00:00Z")],
    )
    assert len(out) == 1
    assert out[0]["id"] == 101
    assert out[0]["homeTeam"]["id"] == 1010


def test_kickoff_mismatch_is_dropped():
    """Same names, different day: not the same match."""
    out = _merge_fd_ids(
        [_fx("Arsenal FC", "Burnley FC", "2026-11-08T15:00:00Z")],
        [_fd(101, "Arsenal FC", "Burnley FC", "2026-11-09T15:00:00Z")],
    )
    assert out == []


def test_ambiguous_match_is_dropped():
    """Two plausible football-data records is a coin flip, so take neither."""
    out = _merge_fd_ids(
        [_fx("Arsenal FC", "Burnley FC", "2026-11-08T15:00:00Z")],
        [
            _fd(101, "Arsenal FC", "Burnley FC", "2026-11-08T15:00:00Z"),
            _fd(102, "Arsenal", "Burnley", "2026-11-08T15:00:00Z"),
        ],
    )
    assert out == []


def test_different_competition_is_dropped():
    out = _merge_fd_ids(
        [_fx("Arsenal FC", "Burnley FC", "2026-11-08T15:00:00Z", code="PL")],
        [_fd(101, "Arsenal FC", "Burnley FC", "2026-11-08T15:00:00Z", code="CL")],
    )
    assert out == []


def test_record_without_an_id_is_dropped():
    """A null id would store a ticket the result checker can never grade."""
    out = _merge_fd_ids(
        [_fx("Arsenal FC", "Burnley FC", "2026-11-08T15:00:00Z")],
        [_fd(None, "Arsenal FC", "Burnley FC", "2026-11-08T15:00:00Z")],
    )
    assert out == []


def test_unmatched_fixture_is_dropped_not_guessed():
    out = _merge_fd_ids(
        [_fx("Arsenal FC", "Burnley FC", "2026-11-08T15:00:00Z")],
        [_fd(101, "Chelsea FC", "Everton FC", "2026-11-08T15:00:00Z")],
    )
    assert out == []


def test_one_football_data_record_cannot_be_stored_twice():
    fd = _fd(101, "Arsenal FC", "Burnley FC", "2026-11-08T15:00:00Z")
    out = _merge_fd_ids(
        [
            _fx("Arsenal FC", "Burnley FC", "2026-11-08T15:00:00Z"),
            _fx("Arsenal", "Burnley", "2026-11-08T15:00:00Z"),
        ],
        [fd],
    )
    assert len(out) == 1
