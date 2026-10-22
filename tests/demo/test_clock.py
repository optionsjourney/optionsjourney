from datetime import UTC, date, datetime, time, timedelta

from optionsjourney.demo.clock import NY, DemoClock


def test_clock_starts_on_the_demo_morning():
    c = DemoClock.start()
    ny = datetime.now(UTC).astimezone(NY)
    assert ny.date() == date(2026, 10, 21) and (ny.hour, ny.minute) == (9, 40)
    assert c.now().tzinfo is not None


def test_skip_moves_time_and_tells_listeners():
    c = DemoClock.start()
    seen = []
    c.listeners.append(lambda a, b: seen.append((a, b)))
    before = c.now()
    after = c.skip(15)
    assert timedelta(minutes=14, seconds=59) < after - before < timedelta(minutes=15, seconds=2)
    assert seen and seen[0][1] == after


def test_skip_stops_at_the_close():
    c = DemoClock.start()
    c.to_close()
    assert c.is_over() and c.now().astimezone(NY).time() >= time(16, 0)
    assert c.skip(15).astimezone(NY).time() == time(16, 0)


def test_stop_brings_back_the_real_clock():
    DemoClock.start(datetime(2020, 1, 6, 15, 0, tzinfo=UTC))
    assert datetime.now(UTC).year == 2020
    DemoClock.stop()
    assert datetime.now(UTC).year != 2020
