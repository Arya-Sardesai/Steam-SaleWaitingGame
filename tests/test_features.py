import numpy as np
import pandas as pd

from steamwait.features import add_calendar_features, build_checkins

EVENTS = np.array(["2025-06-26", "2025-12-18"], dtype="datetime64[ns]")


def test_calendar_features():
    df = pd.DataFrame({"d": pd.to_datetime(["2025-06-20", "2025-06-26", "2025-07-01", "2026-01-01"])})
    out = add_calendar_features(df, "d", EVENTS)
    assert out["days_to_next_major_event"].tolist()[:3] == [6, 175, 170]   # on the day -> next one
    assert out["days_since_major_event"].tolist()[1:3] == [0, 5]
    assert np.isnan(out["days_to_next_major_event"].iloc[3])               # nothing announced yet
    assert np.isnan(out["days_since_major_event"].iloc[0])                 # no earlier event


def _gap(duration, observed):
    return {"appid": 1, "gap_start": pd.Timestamp("2025-01-01"), "duration_days": duration,
            "event_observed": observed}


def test_checkin_labels_and_censoring():
    gaps = pd.DataFrame([
        _gap(10, True),     # sale 10 days after gap start
        _gap(40, True),     # sale 40 days after
        _gap(20, False),    # still waiting after 20 days (censored)
    ])
    rows = build_checkins(gaps, EVENTS, horizon=14, check_days=(0, 7, 30))
    got = {(r.duration_days, r.days_waited): r.label for r in rows.itertuples()}
    assert got[(10, 0)] == 1 and got[(10, 7)] == 1    # 10 and 3 days left: inside 14
    assert got[(40, 0)] == 0 and got[(40, 7)] == 0    # 40 and 33 days left: outside
    assert got[(40, 30)] == 1                         # 10 days left
    assert (20, 0) in got and got[(20, 0)] == 0       # censored at 20 > 14: known "no"
    assert (20, 7) not in got                         # censored at 13 left < 14: unknown, dropped
    assert (10, 30) not in got                        # already on sale by then: no row
