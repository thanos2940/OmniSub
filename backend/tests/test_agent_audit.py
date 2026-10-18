"""
Translation audit: lines on the wrong line, and broken text
===========================================================
Besides untranslated lines, the audit finds lines that carry the translation of another line
(a model reply that lost its place, a subtitle file made for another cut). Episodes Omnisub
translated itself get those lines cleared — with a backup — and queued for gap-filling;
imported subtitle files are only reported.

It also finds broken text - Greek mojibake, prompt text that leaked into a translation,
wrong-alphabet words - and with ``repair`` fixes it in the episode (with a backup) and in the
subtitle file on disk.
"""

import json

import pytest

from utils import storage
from utils.translation_queue import TranslationQueue
from services.agent_task_service import AgentTaskService

LINES = [
    "Where were you last night?", "I was at the station, waiting for Johnny.", "He never came.",
    "That's not like him at all!", "I know. I called him three times.", "Did he answer?", "No.",
    "We should go to his place at 9.", "It's late. Let's wait until morning, okay?", "Fine, but I'm not sleeping.",
    "Neither am I!", "Look! There's someone at the door.", "Who is it?", "Police. Open up!",
    "What do you want from us at 3 in the morning?", "Your friend Johnny Walker is in the hospital.",
    "Is he alive?", "Yes. He was hit by a car on Route 66, but he'll live.", "Oh, thank God!", "Can we see him?",
    "Tomorrow. Visiting hours start at 10.", "Thank you, officer.", "Get some sleep.", "Good night.",
]
GOOD = [f"Ελ: {s}" for s in LINES]
SHIFTED = GOOD[:8] + GOOD[9:19] + GOOD[18:]   # lines 9-18 carry the next line's translation
PROJECT = "Audit Show"


@pytest.fixture(autouse=True)
def empty_queue():
    def wipe():
        with TranslationQueue()._conn() as conn:
            conn.execute("DELETE FROM translation_queue")
            conn.commit()
    wipe()
    yield
    wipe()


@pytest.fixture
def service(tmp_path):
    return AgentTaskService(tasks_dir=tmp_path / "agent_tasks")


def _episode(episode, translations, **meta):
    cues = []
    for i, (src, tgt) in enumerate(zip(LINES, translations)):
        a, b = 1000 + i * 2100, 2800 + i * 2100
        cues.append({"index": i + 1, "start": f"00:00:{a // 1000:02d},{a % 1000:03d}",
                     "end": f"00:00:{b // 1000:02d},{b % 1000:03d}", "original": src,
                     "translated": tgt, "translations": {"el": tgt}})
    storage.save_episode(PROJECT, episode, cues)
    m = storage.load_episode_metadata(PROJECT, episode) or {}
    m.update(translated=True, translation_status="completed", **meta)
    storage.save_episode_metadata(PROJECT, episode, m)


@pytest.fixture
def library():
    storage.create_project(PROJECT, {"show_name": PROJECT, "target_language": "Greek"})
    _episode("S01E01", SHIFTED, translation_source="agent", last_translated_at="2026-10-01T10:00:00")
    _episode("S01E02", GOOD, translation_source="agent", last_translated_at="2026-10-01T11:00:00")
    _episode("S01E03", SHIFTED, arr_has_target=True)  # imported from disk


def _greek(episode):
    return [(c.get("translations") or {}).get("el") for c in storage.load_episode_data(PROJECT, episode)]


def test_audit_reports_misplaced_lines_and_changes_nothing(service, library):
    res = service.audit_project(PROJECT)
    assert res["misplaced"] == ["S01E01"]
    assert res["misplaced_imported"] == ["S01E03"]
    assert res["incomplete_count"] == 0
    row = next(e for e in res["episodes"] if e["episode_name"] == "S01E01")
    assert row["misplaced"] == 10 and row["imported"] is False
    assert row["misplaced_lines"][0] == {"line": 9, "fits_line": 10}
    assert storage.load_episode_metadata(PROJECT, "S01E01")["translation_gaps"]["misplaced"] == 10
    assert storage.load_episode_metadata(PROJECT, "S01E02")["translation_gaps"]["misplaced"] == 0
    assert _greek("S01E01") == SHIFTED
    assert service.list_pending_tasks() == []


def test_requeue_clears_misplaced_lines_with_a_backup(service, library):
    res = service.audit_project(PROJECT, requeue=True)
    assert res["cleared_lines"] == 10
    greek = _greek("S01E01")
    assert greek[8:18] == [None] * 10 and greek[:8] == GOOD[:8] and greek[18:] == GOOD[18:]
    backup = json.loads((storage.episode_dir(PROJECT, "S01E01") / "misplaced_backup.json").read_text(encoding="utf-8"))
    assert [x["line"] for x in backup[0]["lines"]] == list(range(9, 19))
    assert backup[0]["lines"][0] == {"line": 9, "original": LINES[8], "translation": GOOD[9], "fits_line": 10}
    assert _greek("S01E03") == SHIFTED  # imported subtitles are left alone
    pending = service.list_pending_tasks()
    assert [(p["episode_name"], p["mode"]) for p in pending] == [("S01E01", "gaps")]
    claimed = service.prepare_task_workspace(None)
    assert claimed["units"] == 10  # only the cleared lines are translated again


def test_misplaced_check_can_be_skipped(service, library):
    res = service.audit_project(PROJECT, misplaced=False)
    assert res["misplaced"] == [] and res["misplaced_imported"] == []
    assert storage.load_episode_metadata(PROJECT, "S01E01")["translation_gaps"]["misplaced"] == 0


# ===========================================================================
# Broken text: mojibake, leaked prompt text, wrong-alphabet words
# ===========================================================================

BROKEN_PROJECT = "Repair Show"
SOURCES = LINES[:11] + ["What?"]
GREEK_TEXT = [
    "Πού ήσουν χθες το βράδυ;", "Ήμουν στο σταθμό και περίμενα τον Τζόνι.", "Δεν ήρθε ποτέ.",
    "Δεν του μοιάζει καθόλου!", "Το ξέρω. Τον πήρα τρεις φορές τηλέφωνο.", "Απάντησε;", "Όχι.",
    "Πρέπει να πάμε σπίτι του στις 9.", "Είναι αργά. Ας περιμένουμε ως το πρωί, εντάξει;",
    "Εντάξει, αλλά δεν θα κοιμηθώ.", "Ούτε εγώ!", "Τι;",
]
MIXED = "Ο Συνταγματάρχה είναι εδώ"
# S01E01 (translated by an agent): 1 mojibake, 4 leaks, 1 wrong-alphabet line
DAMAGED = list(GREEK_TEXT)
DAMAGED[1] = GREEK_TEXT[1].encode("cp1253").decode("cp1252")
DAMAGED[2] = "1| It happened, like, right here! Just this morning!"
DAMAGED[3] = "[prev] Θα ακούσουμε τι έχετε να πείτε στην υπηρεσία."
DAMAGED[4] = "EN: I know. I called him three times. CURRENT: I know. CORRECTED: Το ξέρω. Τον πήρα τρεις φορές."
DAMAGED[5] = "[S01E04] Οι Φόρτζερ φτάνουν στην Ακαδημία Ήντεν και ο Λόιντ ανησυχεί."
DAMAGED[6] = MIXED
# S01E02 (imported): every line is mojibake, the short ones too
GARBLED = [t.encode("cp1253").decode("cp1252") for t in GREEK_TEXT]


def _cues_for(pairs):
    cues = []
    for i, (src, tgt) in enumerate(pairs):
        a, b = 1000 + i * 2100, 2800 + i * 2100
        cues.append({"index": i + 1, "start": f"00:00:{a // 1000:02d},{a % 1000:03d}",
                     "end": f"00:00:{b // 1000:02d},{b % 1000:03d}", "original": src,
                     "translated": tgt, "translations": {"el": tgt}})
    return cues


def _broken_episode(project, episode, translations, **meta):
    storage.save_episode(project, episode, _cues_for(zip(SOURCES, translations)))
    m = storage.load_episode_metadata(project, episode) or {}
    m.update(translated=True, translation_status="completed", **meta)
    storage.save_episode_metadata(project, episode, m)


def _srt(translations):
    return "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\n{t}\n\n" for i, t in enumerate(translations, 1))


@pytest.fixture
def exports(monkeypatch):
    """Exports are recorded instead of written (there is no media folder in a test)."""
    calls = []

    def fake_export(project, episode, cues):
        calls.append({"episode": episode, "translated": [c.get("translated") for c in cues]})
        return f"/media/{episode}.el.srt"

    monkeypatch.setattr(AgentTaskService, "_export", staticmethod(fake_export))
    return calls


@pytest.fixture
def broken_library(tmp_path):
    target = tmp_path / "media" / "Repair.Show.S01E02.el.srt"
    target.parent.mkdir()
    target.write_bytes(b"\xef\xbb\xbf" + _srt(GARBLED).replace("\n", "\r\n").encode("utf-8"))
    storage.create_project(BROKEN_PROJECT, {"show_name": BROKEN_PROJECT, "target_language": "Greek"})
    _broken_episode(BROKEN_PROJECT, "S01E01", DAMAGED, translation_source="agent", last_translated_at="2026-10-01T10:00:00")
    _broken_episode(BROKEN_PROJECT, "S01E02", GARBLED, arr_has_target=True, arr_target_path=str(target))
    _broken_episode(BROKEN_PROJECT, "S01E03", GREEK_TEXT, translation_source="agent", last_translated_at="2026-10-01T11:00:00")
    return target


def _texts(episode, project=BROKEN_PROJECT):
    return [(c.get("translations") or {}).get("el") for c in storage.load_episode_data(project, episode)]


def test_audit_reports_broken_lines_and_changes_nothing(service, broken_library, exports):
    res = service.audit_project(BROKEN_PROJECT)
    assert res["broken"] == ["S01E01", "S01E02"] and res["broken_count"] == 2
    one = next(e for e in res["episodes"] if e["episode_name"] == "S01E01")
    assert one["broken"] == {"mojibake": 1, "numbered": 1, "context_echo": 1, "review_echo": 1,
                             "summary_echo": 1, "mixed_script": 1}
    assert one["broken_lines"] == [{"line": 2, "kind": "mojibake"}, {"line": 3, "kind": "numbered"},
                                   {"line": 4, "kind": "context_echo"}, {"line": 5, "kind": "review_echo"},
                                   {"line": 6, "kind": "summary_echo"}, {"line": 7, "kind": "mixed_script"}]
    assert one["imported"] is False
    two = next(e for e in res["episodes"] if e["episode_name"] == "S01E02")
    assert two["broken"] == {"mojibake": 12} and two["imported"] is True  # the short "Τι;" is "Ôé;"
    clean = next(e for e in res["episodes"] if e["episode_name"] == "S01E03")
    assert "broken" not in clean and "broken_lines" not in clean
    assert storage.load_episode_metadata(BROKEN_PROJECT, "S01E01")["translation_gaps"]["broken"] == 6
    assert storage.load_episode_metadata(BROKEN_PROJECT, "S01E02")["translation_gaps"]["broken"] == 12
    assert storage.load_episode_metadata(BROKEN_PROJECT, "S01E03")["translation_gaps"]["broken"] == 0
    # nothing was touched
    assert _texts("S01E01") == DAMAGED and _texts("S01E02") == GARBLED
    assert not (storage.episode_dir(BROKEN_PROJECT, "S01E01") / "text_repair_backup.json").exists()
    assert "repaired_lines" not in res and "disk_repaired" not in res and exports == []
    assert service.list_pending_tasks() == []


def test_broken_text_check_does_not_disturb_clean_projects(service, library):
    res = service.audit_project(PROJECT)
    assert res["broken"] == [] and res["broken_count"] == 0
    assert all("broken" not in e for e in res["episodes"])
    assert storage.load_episode_metadata(PROJECT, "S01E02")["translation_gaps"]["broken"] == 0


def test_repair_fixes_the_episode_and_keeps_a_backup(service, broken_library, exports):
    res = service.audit_project(BROKEN_PROJECT, repair=True)
    # mojibake decoded, the review block's correction kept, the leaks cleared, the wrong-alphabet line left
    assert res["repaired_lines"] == 2 + 12 and res["broken_cleared"] == 3
    fixed = _texts("S01E01")
    assert fixed[1] == GREEK_TEXT[1]
    assert fixed[2:4] == [None, None] and fixed[5] is None
    assert fixed[4] == "Το ξέρω. Τον πήρα τρεις φορές."
    assert fixed[6] == MIXED
    assert fixed[0] == GREEK_TEXT[0] and fixed[7:] == GREEK_TEXT[7:]
    cues = storage.load_episode_data(BROKEN_PROJECT, "S01E01")
    assert cues[1]["translated"] == GREEK_TEXT[1] and cues[2]["translated"] == ""
    assert _texts("S01E02") == GREEK_TEXT

    backup = json.loads((storage.episode_dir(BROKEN_PROJECT, "S01E01") / "text_repair_backup.json").read_text(encoding="utf-8"))
    assert len(backup) == 1 and backup[0]["lang"] == "el" and backup[0]["repaired_at"]
    assert [b["line"] for b in backup[0]["lines"]] == [2, 3, 4, 5, 6]
    assert backup[0]["lines"][0] == {"line": 2, "original": SOURCES[1], "before": DAMAGED[1], "after": GREEK_TEXT[1],
                                     "kind": "mojibake"}
    assert backup[0]["lines"][1] == {"line": 3, "original": SOURCES[2], "before": DAMAGED[2], "after": "",
                                     "kind": "numbered"}
    # what is still wrong is still reported; what was repaired no longer counts
    assert res["broken"] == ["S01E01", "S01E02"]
    assert storage.load_episode_metadata(BROKEN_PROJECT, "S01E01")["translation_gaps"]["broken"] == 1
    assert storage.load_episode_metadata(BROKEN_PROJECT, "S01E02")["translation_gaps"]["broken"] == 0
    # the cleared lines are gaps now, but nothing is queued without requeue
    assert res["incomplete"] == ["S01E01"] and "enqueue" not in res
    assert service.list_pending_tasks() == []

    again = service.audit_project(BROKEN_PROJECT, repair=True)
    assert again["repaired_lines"] == 0 and again["broken_cleared"] == 0 and again["broken"] == ["S01E01"]
    assert len(json.loads((storage.episode_dir(BROKEN_PROJECT, "S01E01") / "text_repair_backup.json")
                          .read_text(encoding="utf-8"))) == 1


def test_imported_episode_file_is_repaired_in_place_with_a_backup(service, broken_library, exports):
    original = broken_library.read_bytes()
    res = service.audit_project(BROKEN_PROJECT, repair=True)
    assert res["disk_repaired"] == [{"episode": "S01E02", "path": str(broken_library), "lines": 12}]
    data = broken_library.read_bytes()
    assert data == b"\xef\xbb\xbf" + _srt(GREEK_TEXT).replace("\n", "\r\n").encode("utf-8")  # BOM and CRLF kept
    backup = storage.episode_dir(BROKEN_PROJECT, "S01E02") / "disk_backup" / broken_library.name
    assert backup.read_bytes() == original
    # imported episodes are never exported again; episodes Omnisub translated are
    assert [c["episode"] for c in exports] == ["S01E01"] and res["reexported"] == ["S01E01"]

    again = service.audit_project(BROKEN_PROJECT, repair=True)
    assert again["disk_repaired"] == [] and backup.read_bytes() == original


def test_an_existing_disk_backup_is_never_overwritten(service, broken_library, exports):
    backup = storage.episode_dir(BROKEN_PROJECT, "S01E02") / "disk_backup" / broken_library.name
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"the very first original")
    service.audit_project(BROKEN_PROJECT, repair=True)
    assert backup.read_bytes() == b"the very first original"
    assert broken_library.read_bytes().decode("utf-8-sig").replace("\r\n", "\n") == _srt(GREEK_TEXT)  # repaired nonetheless


def test_a_legacy_encoded_target_file_is_left_alone(service, broken_library, exports):
    """cp1253 bytes are Greek, not mojibake: the readers decode them correctly now."""
    broken_library.write_bytes(_srt(GREEK_TEXT).encode("cp1253"))
    res = service.audit_project(BROKEN_PROJECT, repair=True)
    assert res["disk_repaired"] == []
    assert broken_library.read_bytes() == _srt(GREEK_TEXT).encode("cp1253")
    assert _texts("S01E02") == GREEK_TEXT  # the episode itself was repaired


def test_disk_repair_is_retried_when_the_file_was_out_of_reach(service, broken_library, exports):
    away = broken_library.with_name("away.srt")
    broken_library.rename(away)
    res = service.audit_project(BROKEN_PROJECT, repair=True)
    assert res["disk_repaired"] == [] and _texts("S01E02") == GREEK_TEXT
    away.rename(broken_library)
    res = service.audit_project(BROKEN_PROJECT, repair=True)
    assert [d["episode"] for d in res["disk_repaired"]] == ["S01E02"]
    assert broken_library.read_bytes().decode("utf-8-sig").replace("\r\n", "\n") == _srt(GREEK_TEXT)


def test_a_file_that_cannot_be_written_is_reported(service, broken_library, exports):
    import stat
    broken_library.chmod(stat.S_IREAD)
    try:
        res = service.audit_project(BROKEN_PROJECT, repair=True)
    finally:
        broken_library.chmod(stat.S_IWRITE | stat.S_IREAD)
    assert res["disk_repaired"] == []
    assert [d["episode"] for d in res["disk_failed"]] == ["S01E02"] and res["disk_failed"][0]["error"]
    assert _texts("S01E02") == GREEK_TEXT


def test_episodes_omnisub_translated_are_exported_again(service, broken_library, exports):
    service.audit_project(BROKEN_PROJECT, repair=True)
    assert len(exports) == 1 and exports[0]["episode"] == "S01E01"
    assert exports[0]["translated"][1] == GREEK_TEXT[1] and exports[0]["translated"][2] == ""


def test_an_export_that_fails_is_reported(service, broken_library, monkeypatch):
    monkeypatch.setattr(AgentTaskService, "_export", staticmethod(lambda p, e, c: None))
    res = service.audit_project(BROKEN_PROJECT, repair=True)
    assert res["reexported"] == [] and res["reexport_failed"] == ["S01E01"]


def test_wrong_alphabet_lines_are_only_cleared_when_requeueing(service, broken_library, exports):
    res = service.audit_project(BROKEN_PROJECT, repair=True)
    assert _texts("S01E01")[6] == MIXED and res["broken_cleared"] == 3
    res = service.audit_project(BROKEN_PROJECT, requeue=True)  # requeue implies repair
    assert _texts("S01E01")[6] is None and res["broken_cleared"] == 1 and res["repaired_lines"] == 0
    backup = json.loads((storage.episode_dir(BROKEN_PROJECT, "S01E01") / "text_repair_backup.json").read_text(encoding="utf-8"))
    assert backup[-1]["lines"] == [{"line": 7, "original": SOURCES[6], "before": MIXED, "after": "", "kind": "mixed_script"}]
    assert res["broken"] == ["S01E01"]  # what this run found; nothing is left broken
    assert storage.load_episode_metadata(BROKEN_PROJECT, "S01E01")["translation_gaps"]["broken"] == 0
    assert service.audit_project(BROKEN_PROJECT)["broken"] == []


def test_requeue_queues_the_episode_whose_garbage_lines_were_cleared(service, broken_library, exports):
    res = service.audit_project(BROKEN_PROJECT, requeue=True)
    assert res["repaired_lines"] == 14 and res["broken_cleared"] == 4  # the wrong-alphabet line too
    assert res["enqueue"]["episodes"] == ["S01E01"]  # S01E02 is complete again, S01E03 was never broken
    pending = service.list_pending_tasks()
    assert [(p["episode_name"], p["mode"]) for p in pending] == [("S01E01", "gaps")]
    claimed = service.prepare_task_workspace(None)
    assert claimed["units"] == 4  # only the cleared lines are translated again


def test_requeue_queues_an_episode_even_when_every_line_was_cleared(service, exports):
    storage.create_project(BROKEN_PROJECT, {"show_name": BROKEN_PROJECT, "target_language": "Greek"})
    _broken_episode(BROKEN_PROJECT, "S01E01", ["[prev] Θα ακούσουμε"] * 12)  # not translated by Omnisub, nothing kept
    res = service.audit_project(BROKEN_PROJECT, requeue=True)
    assert res["broken_cleared"] == 12 and res["enqueue"]["episodes"] == ["S01E01"]


def test_misplaced_clearing_still_runs_after_a_repair(service, library, exports):
    res = service.audit_project(PROJECT, requeue=True, repair=True)
    assert res["cleared_lines"] == 10 and res["broken_count"] == 0
    assert [p["episode_name"] for p in service.list_pending_tasks()] == ["S01E01"]
