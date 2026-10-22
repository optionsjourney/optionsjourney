import json
from datetime import date, timedelta

from optionsjourney.demo.counts import Counts, DayCounts

DAY = date(2026, 10, 22)


def make(tmp_path, day=DAY):
    box = {"day": day}
    return Counts(tmp_path / "counts.json", lambda: box["day"]), box


def test_counts_add_up_in_a_day(tmp_path):
    c, _ = make(tmp_path)
    c.visit()
    c.visit()
    c.visit()
    c.waited(30)
    c.waited(90)
    c.visit_ended(120, 3)
    c.visit_ended(60, 8)
    (row,) = c.days()
    assert (row.day, row.visits, row.waited) == (DAY, 3, 2)
    assert row.wait_seconds == 120
    assert row.average_wait == 60
    assert row.visit_seconds == 180
    assert row.average_visit == 90
    assert row.furthest == {3: 1, 8: 1}


def test_new_day_starts_fresh_row(tmp_path):
    c, box = make(tmp_path)
    c.visit()
    box["day"] = DAY + timedelta(days=1)
    c.visit()
    c.visit()
    rows = c.days()
    assert [(r.day, r.visits) for r in rows] == [(DAY, 1), (DAY + timedelta(days=1), 2)]


def test_reached_share_of_ended_visits():
    row = DayCounts(DAY, 4, 0, 0.0, 0.0, {2: 1, 3: 2, 8: 1})
    assert row.reached(3) == 0.75
    assert row.reached(8) == 0.25
    assert row.reached(1) == 1.0


def test_empty_day_averages_are_zero():
    row = DayCounts(DAY, 0, 0, 0.0, 0.0, {})
    assert row.average_wait == 0
    assert row.average_visit == 0
    assert row.reached(3) == 0


def test_survives_reload(tmp_path):
    c, _ = make(tmp_path)
    c.visit()
    c.visit_ended(10, 2)
    again, _ = make(tmp_path)
    assert again.days()[0].furthest == {2: 1}
    assert again.days()[0].visits == 1


def test_broken_file_starts_empty_and_is_replaced(tmp_path):
    path = tmp_path / "counts.json"
    path.write_text("{not json")
    c, _ = make(tmp_path)
    assert c.days() == []
    c.visit()
    assert json.loads(path.read_text())
    assert c.days()[0].visits == 1


def test_wrong_shape_starts_empty(tmp_path):
    (tmp_path / "counts.json").write_text('["a"]')
    c, _ = make(tmp_path)
    assert c.days() == []


def test_keeps_90_days(tmp_path):
    c, box = make(tmp_path, DAY - timedelta(days=100))
    c.visit()
    box["day"] = DAY - timedelta(days=89)
    c.visit()
    box["day"] = DAY
    c.visit()
    days = [r.day for r in c.days(n=200)]
    assert DAY - timedelta(days=100) not in days
    assert DAY - timedelta(days=89) in days and DAY in days
    assert len(json.loads((tmp_path / "counts.json").read_text())) == 2


def test_days_returns_last_n(tmp_path):
    c, box = make(tmp_path)
    for i in range(5):
        box["day"] = DAY + timedelta(days=i)
        c.visit()
    assert [r.day for r in c.days(2)] == [DAY + timedelta(days=3), DAY + timedelta(days=4)]


def test_file_holds_only_documented_keys(tmp_path):
    c, _ = make(tmp_path)
    c.visit()
    c.waited(5)
    c.visit_ended(7, 4)
    data = json.loads((tmp_path / "counts.json").read_text())
    assert set(data) == {DAY.isoformat()}
    assert set(data[DAY.isoformat()]) == {
        "visits", "waited", "wait_seconds", "visit_seconds", "furthest"}
    assert not list(tmp_path.glob("*.tmp")) and len(list(tmp_path.iterdir())) == 1


def test_furthest_is_clamped_to_known_steps(tmp_path):
    c, _ = make(tmp_path)
    c.visit_ended(1, 0)
    c.visit_ended(1, 99)
    assert c.days()[0].furthest == {1: 1, 8: 1}


def test_unwritable_path_never_raises_and_keeps_memory(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    c = Counts(blocker / "counts.json", lambda: DAY)
    c.visit()
    c.waited(5)
    c.visit_ended(9, 2)
    (row,) = c.days()
    assert (row.visits, row.waited, row.furthest) == (1, 1, {2: 1})


def test_path_is_a_directory_never_raises(tmp_path):
    (tmp_path / "counts.json").mkdir()
    c, _ = make(tmp_path)
    c.visit()
    assert c.days()[0].visits == 1
    assert [p.name for p in tmp_path.iterdir()] == ["counts.json"]


def test_huge_number_in_file_starts_empty(tmp_path):
    (tmp_path / "counts.json").write_text(
        '{"2026-10-22": {"visits": 1e999, "waited": 0, "wait_seconds": 0,'
        ' "visit_seconds": 0, "furthest": {}}}')
    c, _ = make(tmp_path)
    assert c.days() == []
