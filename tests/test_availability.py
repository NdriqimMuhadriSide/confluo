"""Free-time calculation (input of the availability engine)."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from confluo_crm.availability import Rule, Span, free_intervals

BRU = ZoneInfo("Europe/Brussels")
MON = date(2027, 3, 1)  # a Monday


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), BRU)


def spans(result: list[Span]) -> list[tuple[str, str]]:
    return [
        (s.start.astimezone(BRU).strftime("%a %H:%M"), s.end.astimezone(BRU).strftime("%H:%M"))
        for s in result
    ]


WEEK = [Rule(d, time(9), time(17)) for d in range(1, 6)]


def free(**kw: object) -> list[tuple[str, str]]:
    args: dict[str, object] = {
        "rules": WEEK,
        "extra": [],
        "off": [],
        "busy": [],
        "tz": BRU,
        "start": MON,
        "days": 7,
    }
    args.update(kw)
    return spans(free_intervals(**args))  # type: ignore[arg-type]


def test_weekly_rules_give_working_days() -> None:
    assert free() == [(f"{d} 09:00", "17:00") for d in ("Mon", "Tue", "Wed", "Thu", "Fri")]


def test_split_shift_and_lunch_break() -> None:
    rules = [Rule(1, time(9), time(12, 30)), Rule(1, time(13, 30), time(18))]
    assert free(rules=rules, days=1) == [("Mon 09:00", "12:30"), ("Mon 13:30", "18:00")]


def test_day_off_and_partial_off() -> None:
    tue = date(2027, 3, 2)
    off = [Span(at(tue, 0), at(date(2027, 3, 3), 0)), Span(at(MON, 12), at(MON, 13))]
    assert free(off=off, days=3) == [
        ("Mon 09:00", "12:00"),
        ("Mon 13:00", "17:00"),
        ("Wed 09:00", "17:00"),
    ]


def test_appointments_are_busy() -> None:
    busy = [Span(at(MON, 9), at(MON, 9, 45)), Span(at(MON, 16, 30), at(MON, 17, 30))]
    assert free(busy=busy, days=1) == [("Mon 09:45", "16:30")]


def test_extra_hours_on_a_closed_day() -> None:
    sat = date(2027, 3, 6)
    assert ("Sat 10:00", "14:00") in free(extra=[Span(at(sat, 10), at(sat, 14))])


def test_rules_respect_validity_dates() -> None:
    rules = [Rule(1, time(9), time(17), valid_from=date(2027, 3, 8))]
    assert free(rules=rules, days=14) == [("Mon 09:00", "17:00")]  # only the second Monday
    assert (
        free_intervals(rules=rules, extra=[], off=[], busy=[], tz=BRU, start=MON, days=14)[
            0
        ].start.day
        == 8
    )


def test_local_hours_survive_the_dst_switch() -> None:
    # Brussels moves to summer time on Sunday 2027-03-28.
    rules = [Rule(5, time(9), time(17)), Rule(1, time(9), time(17))]
    result = free_intervals(
        rules=rules, extra=[], off=[], busy=[], tz=BRU, start=date(2027, 3, 26), days=4
    )
    fri, mon = result
    assert (fri.start.utcoffset(), fri.start.astimezone(ZoneInfo("UTC")).hour) == (
        fri.start.utcoffset(),
        8,
    )
    assert mon.start.astimezone(ZoneInfo("UTC")).hour == 7
    assert spans(result) == [("Fri 09:00", "17:00"), ("Mon 09:00", "17:00")]
