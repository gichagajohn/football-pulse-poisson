from backend.cities import get_venue_city


def test_lazio_is_rome_not_alkmaar():
    assert get_venue_city("SS Lazio") == "Rome"
    assert get_venue_city("Lazio") == "Rome"


def test_az_alkmaar_still_resolves():
    assert get_venue_city("AZ Alkmaar") == "Alkmaar"


def test_inter_and_arsenal():
    assert get_venue_city("FC Internazionale Milano") == "Milan"
    assert get_venue_city("Inter Milan") == "Milan"
    assert get_venue_city("Arsenal FC") == "London"


def test_unknown_is_none():
    assert get_venue_city("Some Village FC") is None
