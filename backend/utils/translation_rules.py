"""
Translation style rules — the language guidance every translator follows.

Pure text helpers with no model/ADK dependencies, so the same rules drive both the
API translator (``adk_agents.translator_agent``) and the brief handed to external
AI agents (``services.agent_workspace``). Keep them here so the two paths can never
drift apart.

The rules sit in the part of every prompt that stays the same for a show, so the CLIs' and
Gemini's prompt caching serve them from cache: a precise rule costs little, a wrong one
(a calque offered as an idiom, a Greek-only remark sent to every language) is repeated in
every request. Keep the examples few and idiomatic; never use "->" in them (the purity rule
forbids arrows in answers, and models copy what the prompt shows).
"""

_GREEK_NAMES = ("greek", "el", "ell", "gre", "ελληνικά", "ελληνικα", "modern greek")


def _is_greek(target_language: str) -> bool:
    """"Greek", "el", "el-GR", "Ελληνικά", "Greek (Modern)" ..."""
    lang = (target_language or "").strip().lower()
    if not lang:
        return False
    return (lang in _GREEK_NAMES or lang.startswith(("el-", "el_", "greek ", "greek(")) or
            lang.startswith("ελληνικ"))


def _spoken_greek_idioms_rule(target_language: str) -> str:
    """Explicit guidance for spoken Modern Greek (δημοτική) naturalness over literal calques."""
    if _is_greek(target_language):
        return (
            '- SPOKEN GREEK IDIOMATIC NATURALNESS (ΔΗΜΟΤΙΚΗ): Translate conversational dialogue into natural, '
            'flowing spoken Modern Greek. NEVER produce literal word-for-word calques of English idioms, phrasal verbs, '
            'or discourse markers. Translate the intended meaning, conversational tone, sarcasm, and register:\n'
            '  * "fair enough" = "πάσο" / "σύμφωνοι" / "λογικό" / "δεκτό" (NOT "αρκετά δίκαιο")\n'
            '  * "give me a break" = "έλα τώρα!" / "έλεος!" / "άσε μας!" (NOT "δώσε μου ένα διάλειμμα")\n'
            '  * "come on" = "έλα" / "έλα τώρα" / "άντε" / "πάμε" / "σιγά"\n'
            '  * "you know" / "you know what?" = "ξέρεις κάτι;" / "πώς να στο πω..." / "κοίτα..."\n'
            '  * "for real?" / "no way!" = "σοβαρά;" / "αποκλείεται!" / "με τίποτα!" (NOT "για πραγματικό;")\n'
            '  * "are you kidding me?" = "πλάκα μου κάνεις;" / "σοβαρολογείς;" (NOT "μου κάνεις αστείο;")\n'
            '  * "what the hell?" = "τι στο καλό;" / "τι διάολο;" (NOT "τι η κόλαση;")\n'
            '  * "cut it out" = "κόψ\' το!" / "σταμάτα!" (NOT "κόψ\' το έξω")\n'
            '  * "get out of here" = "έλα ρε!" / "αποκλείεται!" (when expressing disbelief)\n'
            '  * "hold on" / "hang on" = "περίμενε" / "στάσου" / "μισό" (wait); "κρατήσου" only for holding on to something\n'
            '  * "got it?" / "got it" = "κατάλαβες;" / "το \'πιασα"\n'
            '  * "my bad" = "δικό μου λάθος" / "σόρι" / "έφταιξα" (NOT "το κακό μου")\n'
            '  * "on it" / "I\'m on it" = "το \'χω" / "το αναλαμβάνω" (NOT "πάνω σε αυτό")\n'
            '  * "I got this" = "το \'χω" / "άσ\' το πάνω μου"; "I got you" = "σε κατάλαβα" (understood) / '
            '"σε καλύπτω" (I\'ve got your back)\n'
            '  * "take it easy" = "χαλάρωσε" / "ήρεμα"; as a goodbye "να προσέχεις"\n'
            '  * "make sense" / "makes no sense" = "βγάζει νόημα" / "στέκει" / "δεν έχει λογική" (NOT "κάνει νόημα")\n'
            '  * "tell you what" = "άκου να δεις" / "ξέρεις τι;"\n'
            '  * "take your time" = "με την ησυχία σου" / "μη βιάζεσαι" (NOT "πάρε τον χρόνο σου")\n'
            '  * "don\'t sweat it" = "μη σκας" / "δεν πειράζει" / "χαλαρά"\n'
            '  * "hear me out" = "άκουσέ με λίγο" / "άσε με να σου πω"\n'
            '  * "no hard feelings" = "περασμένα ξεχασμένα" / "χωρίς κακία"\n'
            '  * "whatever you say" = "ό,τι πεις" / "πάσο"\n'
            '  * "are you out of your mind?" = "πας καλά;" / "τρελάθηκες;" / "έχασες τα λογικά σου;"\n'
            '  * "looking forward to" = "το περιμένω πώς και πώς" / "ανυπομονώ" (NOT "κοιτάζω μπροστά σε")\n'
            '  * "catch you later" = "τα λέμε" / "τα λέμε μετά"\n'
            '  * "no offense" / "none taken" = "χωρίς παρεξήγηση" / "δεν παρεξηγήθηκα"\n'
            '  * "keep an eye on" = "να έχεις τον νου σου" / "πρόσεχε" (NOT "κράτα ένα μάτι")\n'
            '  * "give it a shot" = "δοκίμασέ το" / "κάνε μια προσπάθεια"\n'
            '  * "it\'s up to you" = "στο χέρι σου είναι" / "εσύ αποφασίζεις" (NOT "είναι πάνω σε σένα")\n'
            '  * "beat around the bush" = "τα φέρνω γύρω γύρω" / "μη μασάς τα λόγια σου"\n'
            '  * "I\'m sorry for your loss" = "συλλυπητήρια" / "λυπάμαι πολύ"\n'
            '  * "you\'re welcome" = "τίποτα" / "παρακαλώ"; "here you go" = "ορίστε"; "what\'s up?" = "τι λέει;" / '
            '"τι γίνεται;"; "nice to meet you" = "χάρηκα"\n'
            'Adapt sarcasm, humor, and rhetorical expressions to sound authentic in spoken Greek subtitles.'
        )
    return (
        f"- IDIOMATIC NATURALNESS: Prioritize natural spoken {target_language} over literal word-for-word translations "
        f"for conversational idioms, phrasal verbs, discourse markers, sarcasm, and register."
    )


def _pro_drop_rule(target_language: str) -> str:
    """Guidance on omitting redundant nominative personal pronouns in pro-drop languages."""
    if _is_greek(target_language):
        return (
            '- PRO-DROP (NULL SUBJECT): Greek verbs conjugate for person and number; omit nominative subject pronouns '
            '(εγώ, εσύ, αυτός/αυτή, εμείς, εσείς, αυτοί) unless needed for deliberate contrast or strong rhetorical emphasis '
            '("I told him, but he refused" = "Του το είπα, μα αρνήθηκε", NOT "Εγώ του είπα αλλά αυτός αρνήθηκε").'
        )
    return ""


def _active_voice_rule(target_language: str) -> str:
    """Guidance on rendering English passives as natural active voice."""
    if _is_greek(target_language):
        return (
            '- ACTIVE OVER PASSIVE VOICE: English passive constructions ("he was told", "it was agreed", "they were attacked") '
            'sound stiff and bureaucratic in spoken Greek. Translate them into natural active voice ("του είπαν", "συμφώνησαν", "τους επιτέθηκαν").'
        )
    return ""


def _contractions_rule(target_language: str) -> str:
    """Guidance on natural spoken contractions and elisions."""
    if _is_greek(target_language):
        return (
            '- NATURAL CONTRACTIONS & ELISIONS (ΕΚΘΛΙΨΗ / ΑΠΟΚΟΠΗ): Use natural conversational contractions where appropriate '
            'to mirror everyday speech and improve subtitle reading cadence '
            '(e.g. "το \'χω", "σ\' ακούω", "μ\' αρέσει", "γι\' αυτό", "κι εγώ", "απ\' το", "πού \'σαι", "να \'σαι καλά", "δεν τ\' αντέχω").'
        )
    return ""


def _register_boundaries_and_slang_rule(target_language: str) -> str:
    """Guidance on register boundaries: allowing casual particles among peers while guarding formal/serious scenes."""
    if _is_greek(target_language):
        return (
            '- REGISTER BOUNDARIES & CONVERSATIONAL PARTICLES:\n'
            '  * CASUAL / PEERS / CLOSE FRIENDS / STREET: When characters are close friends, peers, youth, or in informal street settings, authentic conversational particles and colloquialisms are encouraged (ρε, άντε, άσε, σιγά, ρε συ, φίλε, τύπε).\n'
            '  * FORMAL / SERIOUS / DRAMA / ELDERS / AUTHORITIES: NEVER use casual slang (NEVER use "ρε", "τύπε", or street slang) during solemn moments, dramatic confrontations, or when addressing elders, superiors, or strangers. Use respectful, dignified spoken Greek (κύριε, σας παρακαλώ, μάλιστα) without sounding like a parody or textbook.'
        )
    return ""


def _profanity_rule(target_language: str) -> str:
    """Guidance on translating profanity into authentic Greek colloquial equivalents."""
    if _is_greek(target_language):
        return (
            '- AUTHENTIC COLLOQUIAL PROFANITY: Translate English swear words and vulgarity into authentic, natural Greek colloquial '
            'equivalents matching the emotional intensity and scene context (e.g. "shit" = "σκατά", "fuck" / "fucking" = "γαμώτο" / "γαμημένο", '
            '"asshole" / "dick" = "μαλάκας" / "κάθαρμα" / "αρχίδι", "bitch" = "σκύλα" / "βρώμα", "son of a bitch" = "κάθαρμα" / "πουτάνας γιε"). '
            'NEVER produce literal or sanitized calques (NEVER "γαμώ αυτό" or "σκατά σε αυτό").'
        )
    return ""


def _subtitle_cadence_rule(target_language: str) -> str:
    """Guidance on subtitle conciseness and reading speed."""
    longer = ("Modern Greek text is naturally longer than English" if _is_greek(target_language)
              else f"{target_language} text often runs longer than English")
    return (
        '- SUBTITLE CADENCE & CONCISENESS: Subtitles must be readable in seconds without cognitive overload. '
        f'{longer}; synthesize wordy subordinate clauses into punchy, '
        'idiomatic phrasing without omitting narrative or emotional meaning.'
    )


def _second_person_rule(target_language: str) -> str:
    """Guidance for resolving English 'you' (which marks neither number nor register)
    into the correct {target_language} form, using the surrounding dialogue."""
    return (
        f'- SECOND PERSON ("you"): English "you" shows neither number nor formality. '
        f"Infer from the surrounding dialogue whether ONE person or SEVERAL are being "
        f"addressed, and whether the tone is familiar or formal/respectful, then use the "
        f"matching {target_language} pronoun and verb conjugation. Default to SINGULAR "
        f"familiar; use the plural or formal form ONLY when the context clearly calls for "
        f"it (several listeners, a crowd, or a deferential/formal relationship). Keep the "
        f"chosen form consistent for the same speaker-listener pair across the scene."
    )


# Languages whose adjectives, participles or verb forms agree with the speaker or the person spoken to.
_GENDERED = {"el", "es", "fr", "it", "pt", "ro", "ru", "uk", "be", "pl", "cs", "sk", "sl", "hr", "sr", "bs", "bg",
             "mk", "lt", "lv", "he", "ar", "hi", "ur", "ca", "gl"}


def _gender_agreement_rule(target_language: str) -> str:
    """Adjectives and participles that agree with the speaker or the person spoken to: English hides the gender
    ("I'm ready", "you look tired"), the target language does not."""
    from utils.language_codes import to_code
    if _is_greek(target_language):
        example = (' ("I\'m ready" = "Είμαι έτοιμος" from a man, "Είμαι έτοιμη" from a woman; "are you sure?" '
                   'to a woman = "είσαι σίγουρη;")')
    elif to_code(target_language) in _GENDERED:
        example = ""
    else:
        return ""
    return (
        "- SPEAKER AND LISTENER GENDER: English rarely shows whether the speaker or the person spoken to is male or "
        f"female, but {target_language} adjectives, participles and some verb forms must agree with them{example}. "
        "Work out who speaks and to whom from the scene, the names around the line, the characters listed and the "
        "show context, and keep the same choice for the same person. When nothing tells you, prefer a wording "
        "that needs no gender over a guess."
    )


def _greek_punctuation_rule(target_language: str) -> str:
    """Greek punctuation that models trained mostly on English get wrong."""
    if _is_greek(target_language):
        return ('- GREEK PUNCTUATION: the question mark is ";" (never "?"), quotations use «...», and the source\'s '
                "ellipses and the dashes that mark two speakers stay.")
    return ""


GLOSSARY_JUDGMENT_RULE = (
    "- GLOSSARY WITH JUDGMENT: use the glossary rendering wherever a line uses the word as that name or term, with "
    "the right grammatical gender for articles and adjectives. The same English word can also be an everyday word, "
    "a verb or part of an idiom (\"hold on\", \"a heavy load\", \"the pillar collapsed\"): then translate it by its "
    "meaning in the sentence. A term marked \"only as …\" applies only in that sense."
)


def _morphological_guidance_rule(target_language: str) -> str:
    """Explicit guidance on declensions, grammatical cases, and articles for glossary terms."""
    if _is_greek(target_language):
        return (
            '- GLOSSARY MORPHOLOGY & DECLENSIONS: Match glossary translations faithfully, but grammatically DECLINE '
            'nouns, proper names, and adjectives to fit sentence syntax (Nominative, Genitive, Accusative, Vocative) '
            'with appropriate definite/indefinite articles (ο/του/τον, η/της/την, το/του/το, etc.). Do NOT force the lemma/nominative '
            'form where an inflected case is grammatically required.'
        )
    return (
        f"- GLOSSARY MORPHOLOGY: Match glossary translations faithfully while inflecting grammatical cases, pluralization, "
        f"and gender agreements as required by {target_language} sentence syntax."
    )


def _script_integrity_rule(target_language: str) -> str:
    """Explicit script hygiene rule to prevent homoglyph contamination and broken characters."""
    if _is_greek(target_language):
        return "- SCRIPT HYGIENE: Write ONLY in standard Modern Greek monotonic script (Α-Ω, α-ω with standard tonos ά, έ, ή, ί, ό, ύ, ώ). NEVER mix Latin, Cyrillic, or foreign alphabet characters inside Greek words."
    return ""


def _japanese_names_greek_rule(target_language: str) -> str:
    """Explicit guidance for placing Greek accent (τόνος) on the first syllable of Japanese names."""
    if _is_greek(target_language):
        return (
            '- JAPANESE NAMES TONOS ACCENTUATION (anime and other Japanese works only; Western and Greek names keep their '
            'usual accent): In Greek translations and transliterations of Japanese character names '
            'and proper nouns, the Greek accent (τόνος) is placed on the FIRST syllable (e.g. Tanjiro is written Τάντζιρο, NOT Ταντζίρο; '
            'Zenitsu is written Ζένιτσου, NOT Ζενίτσου; Nezuko is written Νέζουκο, NOT Νεζούκο; Naruto is written Νάρουτο; Sasuke is written Σάσκε; Sakura is written Σάκουρα; '
            'Sanji is written Σάντζι; Inosuke is written Ίνοσουκε; Kibutsuji is written Κίμπουτσουτζι). NEVER accent the final or middle syllable of Japanese names in Greek subtitles.'
        )
    return ""


def build_style_guide(target_language: str, japanese_work: bool = True) -> str:
    """All language rules as one bullet list, without any output-format instructions.

    This is the rule set of ``_build_instruction`` minus the JSON / numbered-output
    mechanics, for translators that write their output some other way (e.g. an agent
    editing a work file). ``japanese_work``: include the rule for Japanese names (callers that
    know the show pass ``utils.work_origin.is_japanese_work``; it is noise for any other show).
    """
    rules = [
        GLOSSARY_JUDGMENT_RULE,
        _morphological_guidance_rule(target_language),
        _spoken_greek_idioms_rule(target_language),
        _pro_drop_rule(target_language),
        _active_voice_rule(target_language),
        _contractions_rule(target_language),
        _register_boundaries_and_slang_rule(target_language),
        _profanity_rule(target_language),
        _subtitle_cadence_rule(target_language),
        _second_person_rule(target_language),
        _gender_agreement_rule(target_language),
        _greek_punctuation_rule(target_language),
        _script_integrity_rule(target_language),
        _japanese_names_greek_rule(target_language) if japanese_work else "",
        '- Translate only what appears in the source: "Rin" is not "Rin Tohsaka" unless the full name is present.',
        f"- TRANSLATION PURITY: write only the {target_language} text. Never keep the English next to it, "
        f'never write paired text ("English = {target_language}"), never use arrows to separate source and '
        'translation.',
    ]
    return "\n".join(r for r in rules if r)
