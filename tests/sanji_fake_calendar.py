"""Sanji 流程測試共用的假 Google 行事曆（取代 shared.google_calendar，不打真 API）。"""

from __future__ import annotations

import types


def install(monkeypatch):
    from gateway.handlers import sanji_calendar

    fake = types.SimpleNamespace(conflicts=[], free=[], created=[], fail_check=None, fail_add=None)

    def find_conflicts(start, end):
        if fake.fail_check:
            raise fake.fail_check
        return list(fake.conflicts)

    def find_free_slots(day, minutes, near=None):
        return list(fake.free)

    def create_event(**kw):
        if fake.fail_add:
            raise fake.fail_add
        fake.created.append(kw)
        return types.SimpleNamespace(id="gcal-1", html_link="https://calendar/x")

    module = types.SimpleNamespace(
        find_conflicts=find_conflicts, find_free_slots=find_free_slots, create_event=create_event
    )
    monkeypatch.setattr(sanji_calendar, "_gc", lambda: module)
    return fake


def event(title, start, end):
    return types.SimpleNamespace(title=title, start=start, end=end)
