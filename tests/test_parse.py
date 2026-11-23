from steamwait.fetch_details import classify
from steamwait.parse_details import parse_record

PAID_ON_SALE = {
    "appid": 620, "fetched_at": "2026-11-24T18:00:00+00:00", "status": "ok",
    "payload": {"success": True, "data": {
        "name": "Portal 2", "type": "game", "is_free": False, "required_age": "0",
        "release_date": {"coming_soon": False, "date": "18 Apr, 2011"},
        "developers": ["Valve"], "publishers": ["Valve"],
        "genres": [{"id": "2", "description": "Action"}],
        "categories": [{"id": 2, "description": "Single-player"}],
        "content_descriptors": {"ids": [], "notes": None},
        "price_overview": {"currency": "CAD", "initial": 1149, "final": 114, "discount_percent": 90},
    }},
}
FREE = {"appid": 570, "fetched_at": "2026-11-24T18:00:00+00:00", "status": "ok",
        "payload": {"success": True, "data": {"name": "Dota 2", "type": "game", "is_free": True}}}
NOT_FOUND = {"appid": 1, "fetched_at": "2026-11-24T18:00:00+00:00", "status": "not_found",
             "payload": {"success": False}}
ERROR = {"appid": 2, "fetched_at": "2026-11-24T18:00:00+00:00", "status": "error", "payload": None}


def test_paid_on_sale():
    row = parse_record(PAID_ON_SALE)
    assert row["name"] == "Portal 2"
    assert row["discount_percent"] == 90
    assert row["price_final_cents"] == 114
    assert row["required_age"] == 0
    assert row["genres"] == ["Action"]


def test_free_game_has_no_price():
    row = parse_record(FREE)
    assert row["is_free"] is True
    assert row["price_final_cents"] is None
    assert row["genres"] == []


def test_not_found_and_error_never_crash():
    for rec in (NOT_FOUND, ERROR):
        row = parse_record(rec)
        assert row["name"] is None


def test_classify():
    assert classify(200, {"620": {"success": True, "data": {}}}) == "ok"
    assert classify(200, {"1": {"success": False}}) == "not_found"
    assert classify(429, None) == "rate_limited"
    assert classify(403, None) == "rate_limited"
    assert classify(500, None) == "error"
    assert classify(200, None) == "error"
