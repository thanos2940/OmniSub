"""
Text repair
===========
Broken text found in the library: Greek mojibake (cp1253 read as cp1252, stored as UTF-8),
prompt text that leaked into translations, wrong-alphabet words. These tests pin the rules that
decide what is repaired - and, as important, what is left alone (real French/German/Spanish
text, lyrics kept as authored, sources that really start with "1| ").
"""

import pytest

from utils import text_repair as tr
from utils.text_normalize import sanitize_translation_text

GREEK = "Το ανθρώπινο μυαλό είναι ένα σύμπαν,"
MOJIBAKE = "Ôï áíèñþðéíï ìõáëü åßíáé Ýíá óýìðáí,"


def garble(text, via="cp1252"):
    """What reading cp1253 bytes with the wrong codec makes of Greek text."""
    return text.encode("cp1253").decode(via)


class TestMojibake:

    def test_repairs_the_sample(self):
        assert garble(GREEK) == MOJIBAKE
        assert tr.repair_mojibake(MOJIBAKE) == GREEK

    @pytest.mark.parametrize("line", [
        "Δεν ξέρω τι να πω. Ήταν μια δύσκολη μέρα για όλους μας.",
        "«Καλημέρα», είπε. Ώρα για φαγητό! Όλοι στο τραπέζι.",
        "Ποιος σου το είπε αυτό; Πότε το έμαθες; Ξέρεις ποιος είμαι;",
        "Ναι.",
    ])
    def test_round_trips_greek_through_cp1252_and_latin1(self, line):
        assert tr.repair_mojibake(garble(line)) == line
        assert tr.repair_mojibake(garble(line, "latin-1")) == line

    def test_latin1_control_characters_stand_for_cp1252_punctuation(self):
        line = "Εκείνος είπε “όχι” – και έφυγε… για πάντα."
        garbled = garble(line, "latin-1")
        assert "\x93" in garbled and "“" not in garbled
        assert tr.repair_mojibake(garbled) == line
        assert tr.repair_mojibake(garble(line)) == line

    def test_german_lyrics_kept_as_authored_are_not_repaired(self):
        lyric = "Feiern wir diesen Sieg für den nächsten Kampf!"
        # re-decoded it would be 'fόr den nδchsten': mostly Latin letters
        assert lyric.encode("cp1252").decode("cp1253") == "Feiern wir diesen Sieg fόr den nδchsten Kampf!"
        assert tr.repair_mojibake(lyric) is None
        assert tr.repair_mojibake(lyric, relaxed=True) is None

    @pytest.mark.parametrize("line", [
        "Où est-ce que tu étais, mon petit cœur ? C'était très élégant, déjà.",
        "¿Qué quieres? ¡Está allí, señor! ¡Cómo no!",
        "Che cosa è successo? Perché non sei venuto? Più tardi, così è la vita.",
        "Não sei o que fazer. Você está aqui? Está tudo bem, coração.",
        "Das ist über alle Maßen schön, größer und weißer als gestern.",
        "Café, naïve, résumé, façade, jalapeño, piñata.",
        "“Hello,” he said — “yes…”",
        "Just an English line.",
    ])
    def test_real_western_text_is_never_repaired(self, line):
        assert tr.repair_mojibake(line) is None
        assert tr.repair_mojibake(line, relaxed=True) is None

    def test_text_with_greek_letters_is_not_mojibake(self):
        assert tr.repair_mojibake("Το ανθρώπινο μυαλό ìõáëü åßíáé") is None

    def test_needs_three_accented_characters_unless_the_context_is_known(self):
        short = garble("Τι;")
        assert short == "Ôé;"
        assert tr.repair_mojibake(short) is None
        assert tr.repair_mojibake(short, relaxed=True) == "Τι;"

    def test_relaxed_accepts_latin_names_but_not_mixed_words(self):
        line = garble("Ο Patrick Jane είναι εδώ.")
        assert tr.repair_mojibake(line) is None          # under 70% Greek letters
        assert tr.repair_mojibake(line, relaxed=True) == "Ο Patrick Jane είναι εδώ."
        # 'für' mixes plain and accented letters in one word: Western text, never Greek mojibake
        assert tr.repair_mojibake("Ä für ì", relaxed=True) is None

    @pytest.mark.parametrize("garbled, greek", [
        # from The Mentalist: English names, Greek look-alike capitals inside Latin names
        ("Jenny Didrikson? Cbi. Áóôõíïìßá.", "Jenny Didrikson? Cbi. Αστυνομία."),
        ("Lisbon. Cho êáé Van Pelt.", "Lisbon. Cho και Van Pelt."),
        ("Ç Åöïñßá ôïõò Ý÷åé óáí Åd and Jenny Didrikson.", "Η Εφορία τους έχει σαν Εd and Jenny Didrikson."),
        ("÷ùñéóìÝíïò áðü êÜðïéá Êathryn Stubbs-Gulbrand.", "χωρισμένος από κάποια Κathryn Stubbs-Gulbrand."),
        ("Ç Sierra vista.", "Η Sierra vista."),  # only single accented letters
    ])
    def test_relaxed_repairs_lines_that_are_mostly_english_names(self, garbled, greek):
        assert tr.repair_mojibake(garbled) is None
        assert tr.repair_mojibake(garbled, relaxed=True) == greek

    def test_markup_is_kept_and_is_not_text(self):
        assert tr.repair_mojibake("<i>" + garble("Ναι.") + "</i>") == "<i>Ναι.</i>"
        assert tr.repair_mojibake("{\\an8}" + garble("Το ανθρώπινο") + "\\N" + garble("μυαλό")) == \
            "{\\an8}Το ανθρώπινο\\Nμυαλό"
        # a font name inside an override block is not re-decoded
        assert tr.repair_mojibake("{\\fnCafé}" + garble("Ναι, ναι.")) == "{\\fnCafé}Ναι, ναι."

    def test_bytes_cp1253_does_not_define_make_it_unrepairable(self):
        assert tr.repair_mojibake("Ôï ÿ ðáéäß ìïõ") is None   # 0xFF
        assert tr.repair_mojibake("Ôï ðáéäß ìïõ ♪") is None  # not a cp1252 character

    def test_prevailing_needs_a_majority(self):
        assert tr.mojibake_prevails(6, 10)
        assert not tr.mojibake_prevails(5, 10)
        assert not tr.mojibake_prevails(0, 0)


class TestLeaks:

    def test_numbered_english_of_another_line_is_garbage(self):
        leak = "1| It happened, like, right here! Just this morning!"
        assert tr.salvage_leak(leak, "Whatever the source said", "el") == ("numbered", "")

    def test_numbered_greek_keeps_the_translation(self):
        assert tr.salvage_leak("12| Γειά σου", "Hello", "el") == ("numbered", "Γειά σου")
        assert tr.salvage_leak("12 | Γειά σου\n13| Τι κάνεις;", "Hello", "el") == ("numbered", "Γειά σου\nΤι κάνεις;")

    def test_a_source_that_really_starts_with_a_number_is_fine(self):
        assert tr.salvage_leak("1| It happened", "1| It happened", "el") is None
        assert tr.salvage_leak("2| Ναι", "2| Yes", "el") is None

    def test_numbered_without_a_known_script_only_drops_a_copy_of_the_source(self):
        assert tr.salvage_leak("4| Hola amigo", "Hello friend", "es") == ("numbered", "Hola amigo")
        assert tr.salvage_leak("4| Hello friend", "Hello friend", "es") == ("numbered", "")
        assert tr.salvage_leak("4| It happened", None, "") == ("numbered", "It happened")

    @pytest.mark.parametrize("leak", [
        "[prev] Θα ακούσουμε τι έχετε να πείτε στην υπηρεσία.",
        "[lookahead (do NOT translate)] Θα ακούσουμε",
        "  [PREV] Γειά",
    ])
    def test_echoed_context_is_no_translation(self, leak):
        assert tr.salvage_leak(leak, "We will hear what you have to say.", "el") == ("context_echo", "")

    def test_context_marker_in_the_source_is_not_a_leak(self):
        assert tr.salvage_leak("[prev] Γειά", "[prev] Hello", "el") is None

    def test_reviewer_block_with_a_correction_keeps_the_correction(self):
        text = "EN: Where were you? CURRENT: Where were you? CORRECTED: Πού ήσουν;"
        assert tr.salvage_leak(text, "Where were you?", "el") == ("review_echo", "Πού ήσουν;")
        multiline = "EN: Where were you?\n   CURRENT: Where were you?\n   CORRECTED: Πού ήσουν;"
        assert tr.salvage_leak(multiline, "Where were you?", "el") == ("review_echo", "Πού ήσουν;")

    def test_reviewer_block_without_one_is_cleared(self):
        assert tr.salvage_leak("EN: X CURRENT: X ISSUES: line appears untranslated...", "X", "el") == ("review_echo", "")
        assert tr.salvage_leak("EN: Where were you?", "Where were you?", "el") == ("review_echo", "")
        # a "correction" that is not in the target script is no correction
        assert tr.salvage_leak("EN: X CURRENT: X CORRECTED: Where were you?", "X", "el") == ("review_echo", "")

    def test_dual_format_output_is_left_to_the_sanitizer(self):
        assert tr.salvage_leak("EN: How are you EL: Πως είσαι", "How are you", "el") is None
        assert tr.salvage_leak("EN: How are you -> Πως είσαι", "How are you", "el") is None
        assert tr.salvage_leak("EN: Hello ES: Hola", "Hello", "es") is None

    def test_a_source_that_starts_with_en_is_not_a_leak(self):
        assert tr.salvage_leak("EN: Hello", "EN: Hello", "el") is None

    def test_episode_summary_echo(self):
        tagged = "[S01E04] Οι Φόρτζερ φτάνουν στην Ακαδημία Ήντεν και ο Λόιντ ανησυχεί."
        assert tr.salvage_leak(tagged, "Where is Anya?", "el") == ("summary_echo", "")
        recap = "Ο Λόιντ είναι κατάσκοπος. Προηγουμένως στη σειρά, η Άνια πέρασε τις εξετάσεις."
        assert tr.salvage_leak(recap, "Where is Anya?", "el") == ("summary_echo", "")
        english = "Previously on Spy x Family: Anya passed the exam."
        assert tr.salvage_leak(english, "Where is Anya?", "el") == ("summary_echo", "")

    def test_a_real_recap_line_says_it_legitimately(self):
        line = "Προηγουμένως στη σειρά Spy x Family"
        assert tr.salvage_leak(line, "Previously on Spy x Family", "el") is None
        assert tr.salvage_leak(line, "Previously, on Spy x Family", "el") is None
        assert tr.salvage_leak("[S01E04] Επεισόδιο", "[S01E04] Episode", "el") is None
        # without the source the phrase cannot be judged; the episode tag still can
        assert tr.salvage_leak(line, None, "el") is None
        assert tr.salvage_leak("[S01E04] Οι Φόρτζερ", None, "el") == ("summary_echo", "")

    def test_echo_of_an_echo(self):
        assert tr.salvage_leak("3| [prev] Γειά σου", "Hello", "el") == ("numbered", "")
        assert tr.salvage_leak("3| EN: X CURRENT: X CORRECTED: Γειά σου", "X", "el") == ("numbered", "Γειά σου")

    @pytest.mark.parametrize("fine", [
        "Πώς είσαι;", "- Ναι.\n- Όχι.", "10:30 και όλα καλά", "Το [prev] είναι λέξη", "♪", "",
        "Ο αριθμός 5 | 6 στον πίνακα", "ENA: Ναι", "Συνέχεια...",
    ])
    def test_ordinary_translations_are_not_leaks(self, fine):
        assert tr.salvage_leak(fine, "Some source", "el") is None


class TestMixedScript:

    @pytest.mark.parametrize("line, word", [
        ("Ο Συνταγματάρχה είναι εδώ", "Συνταγματάρχה"),
        ("Εσύ μέשתς εδώ", "μέשתς"),
        ("Άρมστρονγκ", "Άρมστρονγκ"),
        ("Είναι прекраτικό", "прекраτικό"),
        ("Ένα καλό 안녕ά", "안녕ά"),
    ])
    def test_a_word_with_greek_and_another_script(self, line, word):
        assert tr.mixed_script_words(line, "Some English source") == [word]

    def test_a_word_entirely_in_another_script_is_not_mixed(self):
        assert tr.mixed_script_words("Ο κόσμος 東京 είναι εδώ", "The world") == []
        assert tr.mixed_script_words("Ο Иван είναι εδώ", "Ivan is here") == []

    def test_a_source_with_those_letters_exempts_the_line(self):
        assert tr.mixed_script_words("Συνταγματάρχה", "東京") == []
        assert tr.mixed_script_words("Συνταγματάρχה", "Colonel") == ["Συνταγματάρχה"]

    def test_plain_greek_and_latin_names_are_fine(self):
        assert tr.mixed_script_words("Ο Patrick Jane είναι εδώ.", "Patrick Jane is here.") == []
        assert tr.mixed_script_words("", "") == []


class TestRepairTranslation:

    def test_kinds(self):
        src = "The human mind is a universe,"
        assert tr.repair_translation(MOJIBAKE, src) == {"kind": "mojibake", "text": GREEK}
        assert tr.repair_translation("1| It happened, like, right here!", src) == {"kind": "numbered", "text": ""}
        assert tr.repair_translation("[prev] Γειά", src) == {"kind": "context_echo", "text": ""}
        assert tr.repair_translation("EN: X CURRENT: X CORRECTED: Γειά", "X") == {"kind": "review_echo", "text": "Γειά"}
        assert tr.repair_translation("[S01E04] Μια περίληψη", src) == {"kind": "summary_echo", "text": ""}
        assert tr.repair_translation("Συνταγματάρχה", "Colonel") == {"kind": "mixed_script", "text": ""}
        assert "mixed_script" in tr.RETRANSLATE_KINDS and "mojibake" not in tr.RETRANSLATE_KINDS
        assert set(tr.KINDS) == {"mojibake", "numbered", "context_echo", "review_echo", "summary_echo", "mixed_script"}

    @pytest.mark.parametrize("fine", [GREEK, "Hello there", "Ναι.", "", "   ", "Feiern wir für den nächsten Kampf!"])
    def test_fine_lines(self, fine):
        assert tr.repair_translation(fine, "Some source") is None

    def test_mojibake_and_mixed_script_are_for_greek_targets_only(self):
        assert tr.repair_translation(MOJIBAKE, "The human mind", "fr") is None
        assert tr.repair_translation("Συνταγματάρχה", "Colonel", "ru") is None
        assert tr.repair_translation("[prev] Hola", "Hello", "es") == {"kind": "context_echo", "text": ""}
        assert tr.repair_translation(MOJIBAKE, "The human mind", "ell")["kind"] == "mojibake"


SRT_TEXT = ("1\n00:00:01,000 --> 00:00:03,000\nΤο ανθρώπινο μυαλό είναι ένα σύμπαν,\n\n"
            "2\n00:00:04,000 --> 00:00:06,000\nΝαι.\n")


class TestDecodeSubtitleBytes:

    def test_utf8_with_and_without_bom(self):
        assert tr.decode_subtitle_bytes(SRT_TEXT.encode("utf-8")) == SRT_TEXT
        assert tr.decode_subtitle_bytes(SRT_TEXT.encode("utf-8-sig")) == SRT_TEXT

    def test_cp1253_greek(self):
        assert tr.decode_subtitle_bytes(SRT_TEXT.encode("cp1253")) == SRT_TEXT

    def test_cp1253_greek_with_a_byte_cp1253_does_not_define(self):
        raw = SRT_TEXT.encode("cp1253") + b"\xd2"  # a stray 0xD2
        text = tr.decode_subtitle_bytes(raw)
        assert text.startswith(SRT_TEXT)

    @pytest.mark.parametrize("text", [
        "1\n00:00:01,000 --> 00:00:03,000\nOù es-tu allé, mon cœur ? Très élégant !\n",
        "1\n00:00:01,000 --> 00:00:03,000\nFür den nächsten Kampf! Schön, größer.\n",
        "1\n00:00:01,000 --> 00:00:03,000\n¿Qué quieres? ¡Está allí, señor!\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\n“Quoted” — and… done.\n",
    ])
    def test_cp1252_western_text_stays_cp1252(self, text):
        assert tr.decode_subtitle_bytes(text.encode("cp1252")) == text

    def test_bytes_cp1252_does_not_define_do_not_fail_the_file(self):
        text = tr.decode_subtitle_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nCaf\xe9 \x81 \x93hi\x94\n")
        assert text == "1\n00:00:01,000 --> 00:00:02,000\nCafé \x81 “hi”\n"

    def test_utf16_only_with_a_bom(self):
        assert tr.decode_subtitle_bytes(SRT_TEXT.encode("utf-16")) == SRT_TEXT
        # an even-length cp1252 file must not "decode" as BOM-less utf-16
        even = "1\n00:00:01,000 --> 00:00:02,000\nCafé au lait!!!\n"
        assert len(even.encode("cp1252")) % 2 == 0
        assert tr.decode_subtitle_bytes(even.encode("cp1252")) == even

    def test_utf8_file_that_holds_mojibake_is_repaired(self):
        raw = garble(SRT_TEXT).encode("utf-8")
        assert garble(SRT_TEXT) != SRT_TEXT
        assert tr.decode_subtitle_bytes(raw) == SRT_TEXT
        assert tr.decode_subtitle_bytes(b"\xef\xbb\xbf" + raw) == SRT_TEXT

    def test_a_file_with_only_a_few_mojibake_lines_is_left_alone_on_read(self):
        lines = [f"{n}\n00:00:{n:02d},000 --> 00:00:{n:02d},900\n{GREEK} {n}\n" for n in range(1, 9)]
        lines[3] = f"4\n00:00:04,000 --> 00:00:04,900\n{MOJIBAKE}\n"
        text = "\n".join(lines)
        assert tr.decode_subtitle_bytes(text.encode("utf-8")) == text

    def test_utf8_french_is_not_touched(self):
        text = "1\n00:00:01,000 --> 00:00:03,000\nÀ demain, où ça ? Là-bas, à droite. Ça va très bien.\n"
        assert tr.decode_subtitle_bytes(text.encode("utf-8")) == text

    def test_newlines_are_the_files_own(self):
        crlf = SRT_TEXT.replace("\n", "\r\n")
        assert tr.decode_subtitle_bytes(crlf.encode("utf-8")) == crlf
        assert tr.decode_subtitle_bytes(crlf.encode("cp1253")) == crlf

    def test_empty_file(self):
        assert tr.decode_subtitle_bytes(b"") == ""


ASS_HEAD = ("[Script Info]\r\nTitle: Test\r\nScriptType: v4.00+\r\n\r\n"
            "[V4+ Styles]\r\nFormat: Name, Fontname, Fontsize\r\nStyle: Default,Arial,48\r\n\r\n"
            "[Events]\r\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n")


class TestRepairSubtitleText:

    def test_srt_keeps_index_timing_and_line_endings(self):
        content = garble(SRT_TEXT).replace("\n", "\r\n")
        fixed, n = tr.repair_subtitle_text(content)
        assert fixed == SRT_TEXT.replace("\n", "\r\n")
        assert n == 2

    def test_srt_multi_line_cue_and_tags(self):
        content = ("7\n00:00:01,000 --> 00:00:03,000\n<i>" + garble("Το ανθρώπινο μυαλό") + "</i>\n"
                   + garble("Τι;") + "\n\n8\n00:00:04,000 --> 00:00:06,000\nHello there\n")
        fixed, n = tr.repair_subtitle_text(content)
        # the short second line borrows the evidence of the first
        assert fixed == "7\n00:00:01,000 --> 00:00:03,000\n<i>Το ανθρώπινο μυαλό</i>\nΤι;\n\n8\n00:00:04,000 --> 00:00:06,000\nHello there\n"
        assert n == 1

    def test_a_cue_may_mix_languages(self):
        content = "1\n00:00:01,000 --> 00:00:03,000\n- Hello there\n- " + garble("Το ανθρώπινο μυαλό") + "\n"
        fixed, n = tr.repair_subtitle_text(content)
        assert fixed == "1\n00:00:01,000 --> 00:00:03,000\n- Hello there\n- Το ανθρώπινο μυαλό\n"
        assert n == 1

    def test_ass_repairs_only_the_text_field(self):
        content = (ASS_HEAD
                   + "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\an8}" + garble("Το ανθρώπινο, μυαλό")
                   + "\\N" + garble("γεμάτο μυστικά") + "\r\n"
                   + "Comment: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,," + garble("Σχόλιο για όλους") + "\r\n"
                   + "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Hello there, my friend\r\n")
        fixed, n = tr.repair_subtitle_text(content)
        assert fixed == (ASS_HEAD
                         + "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\an8}Το ανθρώπινο, μυαλό\\Nγεμάτο μυστικά\r\n"
                         + "Comment: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,," + garble("Σχόλιο για όλους") + "\r\n"
                         + "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Hello there, my friend\r\n")
        assert n == 1

    def test_vtt(self):
        content = "WEBVTT\n\nNOTE " + garble("Σημείωση") + "\n\nintro\n00:01.000 --> 00:03.000 align:start\n" + garble("Το ανθρώπινο μυαλό") + "\n"
        fixed, n = tr.repair_subtitle_text(content)
        assert fixed == "WEBVTT\n\nNOTE " + garble("Σημείωση") + "\n\nintro\n00:01.000 --> 00:03.000 align:start\nΤο ανθρώπινο μυαλό\n"
        assert n == 1

    def test_nothing_to_repair(self):
        text = SRT_TEXT + "\n3\n00:00:07,000 --> 00:00:08,000\nFeiern wir für den nächsten Kampf!\n"
        assert tr.repair_subtitle_text(text) == (text, 0)
        assert tr.repair_subtitle_text("") == ("", 0)

    def test_the_leftovers_of_a_mostly_mojibake_file_are_repaired_too(self):
        lines = [f"{GREEK} {n}" for n in range(1, 10)] + ["Τι;", "Ο Patrick Jane είναι εδώ."]
        german = "Feiern wir diesen Sieg für den nächsten Kampf!"
        body = [garble(l) for l in lines] + [german, "Où ça ? Là-bas, à droite."]
        content = "\n".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\n{t}\n" for i, t in enumerate(body, 1))
        fixed, n = tr.repair_subtitle_text(content)
        expected = "\n".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\n{t}\n"
                             for i, t in enumerate(lines + [german, "Où ça ? Là-bas, à droite."], 1))
        assert fixed == expected
        assert n == len(lines)

    def test_a_mostly_greek_file_keeps_its_short_accented_lines(self):
        # no majority of mojibake: only what is certain is repaired
        body = [GREEK] * 6 + [garble(GREEK), garble("Τι;")]
        content = "\n".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},900\n{t}\n" for i, t in enumerate(body, 1))
        fixed, n = tr.repair_subtitle_text(content)
        assert n == 1 and garble("Τι;") in fixed and garble(GREEK) not in fixed


class TestSanitizerStripsLeaks:

    def test_numbered_prefix(self):
        assert sanitize_translation_text("12| Γειά σου", source_text="Hello", target_lang_code="el") == "Γειά σου"
        assert sanitize_translation_text("1| It happened, like, right here!", source_text="Whatever",
                                         target_lang_code="el") == ""
        assert sanitize_translation_text("1| It happened", source_text="1| It happened",
                                         target_lang_code="el") == "1| It happened"
        # no source and no language: the prefix goes, the text is not judged
        assert sanitize_translation_text("1| It happened") == "It happened"

    def test_target_language_name_is_enough(self):
        assert sanitize_translation_text("7| Hello again", source_text="x", target_lang="Greek") == ""

    def test_context_echo(self):
        assert sanitize_translation_text("[prev] Θα ακούσουμε τι έχετε να πείτε.", source_text="We will hear you.",
                                         target_lang_code="el") == ""

    def test_reviewer_block(self):
        assert sanitize_translation_text("EN: Hi CURRENT: Hi CORRECTED: Γειά", source_text="Hi",
                                         target_lang_code="el") == "Γειά"
        assert sanitize_translation_text("EN: X CURRENT: X ISSUES: untranslated", source_text="X",
                                         target_lang_code="el") == ""

    def test_summary_echo(self):
        assert sanitize_translation_text("[S01E04] Οι Φόρτζερ φτάνουν στην Ακαδημία", source_text="Hi",
                                         target_lang_code="el") == ""
        line = "Προηγουμένως στη σειρά Spy x Family"
        assert sanitize_translation_text(line, source_text="Previously on Spy x Family",
                                         target_lang_code="el") == line
        assert sanitize_translation_text(line, target_lang_code="el") == line

    def test_existing_dual_format_handling_still_applies_after_a_salvage(self):
        assert sanitize_translation_text("3| How are you -> Πως είσαι", source_text="How are you",
                                         target_lang_code="el") == "Πως είσαι"
        assert sanitize_translation_text("EN: How are you EL: Πως είσαι") == "Πως είσαι"


def test_a_file_known_to_be_mojibake_repairs_its_name_heavy_leftovers():
    """A second pass over a partly repaired file: its Greek lines are the evidence."""
    def cue(i, t):
        return f"{i}\n00:00:0{i},000 --> 00:00:0{i},900\n{t}\n\n"

    srt ="".join(cue(i, "Το ανθρώπινο μυαλό είναι ένα σύμπαν.") for i in range(1, 6))
    srt += cue(6, "Lisbon. Cho êáé Van Pelt.") + cue(7, "Feiern wir für den nächsten Kampf!") + cue(8, "à la carte")
    assert tr.repair_subtitle_text(srt) == (srt, 0)
    new, fixed = tr.repair_subtitle_text(srt, known=True)
    assert fixed == 1
    assert "Lisbon. Cho και Van Pelt." in new
    assert "Feiern wir für den nächsten Kampf!" in new and "à la carte" in new
