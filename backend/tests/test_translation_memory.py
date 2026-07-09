"""Translation Memory write-path resilience.

Regression for the concurrent-create race: two episodes translating at once both
see the table missing, both call create_table, the loser gets "already exists" and
must NOT crash with ``'NoneType' object has no attribute 'add'`` — it has to reopen
on a fresh connection and append.
"""

import shutil

import numpy as np
import pytest

from utils.translation_memory import TranslationMemory


class _FakeTable:
    def __init__(self):
        self.added = []

    def add(self, records):
        self.added.append(records)

    def delete(self, *a, **k):
        pass


class _FakeDB:
    """list_tables() reflects a (possibly stale) view; create_table always loses."""

    def __init__(self, tables):
        self._tables = dict(tables)

    def list_tables(self):
        return list(self._tables.keys())

    def create_table(self, name, data=None):
        raise RuntimeError("Table already exists")

    def open_table(self, name):
        return self._tables[name]


class _Embedder:
    def encode(self, lines, **kwargs):
        return [np.zeros(4, dtype=float) for _ in lines]


@pytest.fixture
def tm(tmp_path, monkeypatch):
    import utils.translation_memory as tmod
    monkeypatch.setattr(tmod, "TM_DIR", tmp_path)
    obj = TranslationMemory("race_proj", lang_code="el")
    monkeypatch.setattr(obj, "_get_embedder", lambda: _Embedder())
    yield obj
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_add_survives_lost_create_race(tm, monkeypatch):
    # Table not visible in our listing → create path → "already exists" → reopen.
    fake_table = _FakeTable()
    monkeypatch.setattr(tm, "_get_db", lambda: _FakeDB({}))   # empty listing
    monkeypatch.setattr(tm, "_reopen_table", lambda: fake_table)

    added = tm.add_translations(["hello"], ["γεια"], "S01E10")

    assert added == 1
    assert fake_table.added and fake_table.added[0][0]["source"] == "hello"


def test_add_handles_stale_listing_returning_none_table(tm, monkeypatch):
    # Table IS in the listing, but _get_table returns None (stale handle) → reopen.
    fake_table = _FakeTable()
    monkeypatch.setattr(tm, "_get_db", lambda: _FakeDB({TranslationMemory.TABLE_NAME: object()}))
    monkeypatch.setattr(tm, "_get_table", lambda: None)
    monkeypatch.setattr(tm, "_reopen_table", lambda: fake_table)

    added = tm.add_translations(["hello"], ["γεια"], "S01E10")

    assert added == 1
    assert fake_table.added


def test_add_raises_clear_error_when_table_unopenable(tm, monkeypatch):
    # Both create and reopen fail to yield a table → a clear RuntimeError, never an
    # opaque AttributeError on None.
    monkeypatch.setattr(tm, "_get_db", lambda: _FakeDB({}))
    monkeypatch.setattr(tm, "_reopen_table", lambda: None)

    with pytest.raises(RuntimeError):
        tm.add_translations(["hello"], ["γεια"], "S01E10")
