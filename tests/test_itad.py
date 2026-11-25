from steamwait.parse_itad import parse_record

REC = {
    "appid": 620, "itad_id": "018d937f-21e1-728e-86d7-9acb3c59f2bb", "status": "ok",
    "payload": [
        {"timestamp": "2026-06-25T17:00:00+00:00", "shop": {"id": 61, "name": "Steam"},
         "deal": {"price": {"amount": 1.24, "amountInt": 124, "currency": "CAD"},
                  "regular": {"amount": 12.49, "amountInt": 1249, "currency": "CAD"}, "cut": 90}},
        {"timestamp": "2026-07-09T17:00:00+00:00", "shop": {"id": 61, "name": "Steam"},
         "deal": {"price": {"amount": 12.49, "amountInt": 1249, "currency": "CAD"},
                  "regular": {"amount": 12.49, "amountInt": 1249, "currency": "CAD"}, "cut": 0}},
    ],
}


def test_parse_history():
    rows = parse_record(REC)
    assert len(rows) == 2
    assert rows[0]["price_cents"] == 124 and rows[0]["cut_percent"] == 90
    assert rows[1]["cut_percent"] == 0 and rows[1]["shop_id"] == 61


def test_empty_and_broken_payloads():
    assert parse_record({"appid": 1, "payload": None}) == []
    assert parse_record({"appid": 1, "payload": []}) == []
    assert parse_record({"appid": 1, "payload": ["junk", {"deal": None}]})[0]["price_cents"] is None
