"""Shared validators for times, weekly hours and timezones."""

from datetime import time
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from pydantic import AfterValidator, BaseModel, model_validator

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown timezone {value!r}") from None
    if value not in available_timezones():
        raise ValueError(f"unknown timezone {value!r}")
    return value


Timezone = Annotated[str, AfterValidator(_timezone)]


class Interval(BaseModel):
    start: time
    end: time

    @model_validator(mode="after")
    def _ordered(self) -> "Interval":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


def check_intervals(intervals: list[Interval]) -> list[Interval]:
    ordered = sorted(intervals, key=lambda i: i.start)
    for a, b in zip(ordered, ordered[1:], strict=False):
        if b.start < a.end:
            raise ValueError("intervals overlap")
    return ordered


DayIntervals = Annotated[list[Interval], AfterValidator(check_intervals)]


class WeeklyHours(BaseModel):
    """Opening hours per weekday; a missing or empty day is closed."""

    mon: DayIntervals = []
    tue: DayIntervals = []
    wed: DayIntervals = []
    thu: DayIntervals = []
    fri: DayIntervals = []
    sat: DayIntervals = []
    sun: DayIntervals = []
