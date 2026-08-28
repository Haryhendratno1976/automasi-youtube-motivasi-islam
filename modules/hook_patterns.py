"""
hook_patterns.py

Extends the existing pattern-history system in script_generator.py with a
HOOK-TYPE taxonomy. Two pools are provided:
"""
hook_patterns.py

Extends the existing pattern-history system in script_generator.py with a
HOOK-TYPE taxonomy. Two pools are provided:

- HOOK_TYPES: derived from research on top-performing US motivation and
  stoicism channels (Daily Stoic, Fearless Motivation, Motiversity,
  MulliganBrothers, EveryDay Stoic).
- ISLAMIC_HOOK_TYPES: equivalent structural patterns adapted for the
  Islamic motivation/education niche -- SENGAJA tanpa contoh yang mengutip
  nomor ayat/hadits spesifik (paraphrase/tema umum saja), sesuai keputusan
  untuk menghindari risiko AI salah kutip referensi keagamaan.

Usage in script_generator.py:
    from hook_patterns import HOOK_TYPES, ISLAMIC_HOOK_TYPES, pick_underused_hook_type

    pool = ISLAMIC_HOOK_TYPES if islamic_content_mode else HOOK_TYPES
    used_types = load_hook_type_history()
    hook_type = pick_underused_hook_type(used_types, hook_types=pool)
    prompt = build_hook_prompt_fragment(hook_type, hook_types=pool)
"""

import random
from collections import Counter

# Each hook type includes:
# - description: what the pattern is
# - template: skeleton to feed into the Gemini prompt
# - examples: real-world reference lines (for prompt few-shot, NOT for reuse verbatim)
HOOK_TYPES = {
    "quote_cold_open": {
        "description": (
            "Open with a 5-12 word quote from a stoic/historical figure with "
            "no preamble, then reveal who said it plus a surprising context."
        ),
        "template": "\"{quote}\" — {figure} said this while {surprising_context}.",
        "examples": [
            "Marcus Aurelius wrote this while ruling during a plague.",
            "Seneca said this the same year he was ordered to die.",
        ],
    },
    "biographical_contrast": {
        "description": (
            "Highlight that several historical figures had wildly different "
            "lives but share one surprising trait or habit."
        ),
        "template": "{figure_a}. {figure_b}. {figure_c}. Different lives, one habit that saved them all.",
        "examples": [
            "A slave. An emperor. A playwright who advised a tyrant.",
        ],
    },
    "rhetorical_gut_punch": {
        "description": "Open with a sharp rhetorical question that implies discomfort.",
        "template": "What if {uncomfortable_possibility}?",
        "examples": [
            "What if the thing you're avoiding is the only thing that can save you?",
            "Why do the people who suffer most end up the strongest?",
        ],
    },
    "delayed_reveal": {
        "description": (
            "Withhold the subject/punchline for 2-3 seconds using a visual or "
            "verbal cliffhanger structure to force continued watching."
        ),
        "template": "{setup_without_naming_subject}... and it's not what you think.",
        "examples": [],
    },
    "professional_angle": {
        "description": (
            "Frame ancient wisdom through a modern business/career lens to "
            "attract the higher-RPM professional demographic."
        ),
        "template": "The {ancient_principle} Roman emperors used — that {modern_authority} still use today.",
        "examples": [
            "The Stoic principle Roman emperors used — that Fortune 500 CEOs still use today.",
        ],
    },
    "raw_testimony": {
        "description": (
            "MulliganBrothers-style: real person recounting hardship in a "
            "grounded, unhyped tone. Best for longer-form or interview-style scripts."
        ),
        "template": "{first_person_hardship_statement}",
        "examples": [],
    },
}

# Pool khusus niche motivasi & edukasi Islam. PENTING: tidak ada contoh yang
# mengklaim mengutip ayat/hadits spesifik dengan nomor/rujukan -- semua
# paraphrase tema/nilai umum (sabar, syukur, tawakal, dll), konsisten dengan
# instruksi "ISLAMIC CONTENT ACCURACY" di prompt utama script_generator.py.
ISLAMIC_HOOK_TYPES = {
    "wisdom_cold_open": {
        "description": (
            "Open with a short (5-12 word) statement of general Islamic "
            "wisdom/value, paraphrased in plain language -- NOT a claimed "
            "direct citation of a specific ayat or hadith -- then reveal "
            "why it matters right now."
        ),
        "template": "{wisdom_statement} -- and here's why that matters more than you think.",
        "examples": [
            "Patience isn't passive. It's the quietest form of strength.",
            "Gratitude changes what you see, not what you have.",
        ],
    },
    "companion_story_contrast": {
        "description": (
            "Highlight that several figures from early Islamic history had "
            "very different life circumstances but shared one common value "
            "or trait -- described in general terms, without claiming exact "
            "quoted hadith text."
        ),
        "template": "{figure_a}. {figure_b}. {figure_c}. Different lives, one value that carried them all.",
        "examples": [
            "A former slave. A wealthy merchant. A young boy. Different lives, one unshakable trust in God.",
        ],
    },
    "rhetorical_gut_punch": {
        "description": "Open with a sharp, reflective question that invites honest self-examination (muhasabah), without being judgmental.",
        "template": "What if {uncomfortable_possibility}?",
        "examples": [
            "What if the thing testing your patience right now is exactly what's building it?",
            "Why does gratitude feel hardest right when we need it most?",
        ],
    },
    "delayed_reveal": {
        "description": (
            "Withhold the subject/punchline for 2-3 seconds using a visual or "
            "verbal cliffhanger structure to force continued watching."
        ),
        "template": "{setup_without_naming_subject}... and it's not what you'd expect.",
        "examples": [],
    },
    "daily_life_application": {
        "description": (
            "Frame a timeless Islamic value through a very concrete, modern "
            "everyday-life scenario (work stress, social media, family "
            "friction) so it feels immediately practical, not abstract."
        ),
        "template": "This is the same principle you need the next time {modern_everyday_scenario}.",
        "examples": [
            "This is the same principle you need the next time your phone won't stop buzzing with bad news.",
        ],
    },
    "reflection_invitation": {
        "description": (
            "A warm, direct invitation to pause and reflect (muhasabah) on "
            "a specific aspect of the viewer's day/heart -- reflective "
            "rather than instructional in tone."
        ),
        "template": "Before you scroll away, ask yourself: {reflective_question}",
        "examples": [
            "Before you scroll away, ask yourself: when was the last time you truly felt at peace?",
        ],
    },
    "struggle_to_reassurance": {
        "description": (
            "Name a specific, relatable everyday struggle (anxiety, feeling "
            "lost, overwhelmed, comparing yourself to others) in the first "
            "line, then pivot to a steadying Islamic value (trust, patience, "
            "gratitude) as reassurance. This is the SINGLE MOST common "
            "structural pattern found across top-performing Islamic "
            "motivation/education Shorts -- research consistently shows "
            "content following a 'relatable struggle -> reassurance' arc "
            "outperforms purely instructional or abstract framing in this niche."
        ),
        "template": "{relatable_struggle_statement}. {reassurance_pivot}.",
        "examples": [
            "You feel like you're the only one struggling right now. You're not -- and that feeling has a purpose.",
            "Some nights the anxiety doesn't make sense. That's exactly when trust matters most.",
        ],
    },
}


def pick_underused_hook_type(recent_hook_types: list[str], lookback: int = 10, hook_types: dict = None) -> str:
    """
    Given a list of the most recently used hook_type keys (most recent last),
    return a hook type weighted toward ones NOT used in the lookback window.
    Mirrors the diversity logic already used for hook text in script_generator.py.

    `hook_types`: pool to pick from (defaults to HOOK_TYPES for backward
    compatibility). Pass ISLAMIC_HOOK_TYPES for the Islamic niche.
    """
    pool = hook_types if hook_types is not None else HOOK_TYPES
    recent = recent_hook_types[-lookback:]
    counts = Counter(recent)
    all_types = list(pool.keys())

    # Types never used recently get priority
    unused = [t for t in all_types if counts[t] == 0]
    if unused:
        return random.choice(unused)

    # Otherwise pick the least-used type
    min_count = min(counts[t] for t in all_types)
    least_used = [t for t in all_types if counts[t] == min_count]
    return random.choice(least_used)


def build_hook_prompt_fragment(hook_type: str, hook_types: dict = None) -> str:
    """Return a prompt fragment describing the chosen hook type + template,
    to inject into the Gemini script-generation prompt for the Hook scene.

    `hook_types`: pool the hook_type belongs to (defaults to HOOK_TYPES).
    """
    pool = hook_types if hook_types is not None else HOOK_TYPES
    entry = pool[hook_type]
    return (
        f"Hook style: {hook_type}\n"
        f"Description: {entry['description']}\n"
        f"Template: {entry['template']}\n"
        "Write an original hook line for THIS video's topic following this "
        "pattern. Do not reuse example wording verbatim."
    )


if __name__ == "__main__":
    # quick manual test
    history = ["quote_cold_open", "quote_cold_open", "rhetorical_gut_punch"]
    chosen = pick_underused_hook_type(history)
    print("Chosen hook type (default pool):", chosen)
    print(build_hook_prompt_fragment(chosen))
    print()
    chosen_islamic = pick_underused_hook_type([], hook_types=ISLAMIC_HOOK_TYPES)
    print("Chosen hook type (Islamic pool):", chosen_islamic)
    print(build_hook_prompt_fragment(chosen_islamic, hook_types=ISLAMIC_HOOK_TYPES))
- HOOK_TYPES: derived from research on top-performing US motivation and
  stoicism channels (Daily Stoic, Fearless Motivation, Motiversity,
  MulliganBrothers, EveryDay Stoic).
- ISLAMIC_HOOK_TYPES: equivalent structural patterns adapted for the
  Islamic motivation/education niche -- SENGAJA tanpa contoh yang mengutip
  nomor ayat/hadits spesifik (paraphrase/tema umum saja), sesuai keputusan
  untuk menghindari risiko AI salah kutip referensi keagamaan.

Usage in script_generator.py:
    from hook_patterns import HOOK_TYPES, ISLAMIC_HOOK_TYPES, pick_underused_hook_type

    pool = ISLAMIC_HOOK_TYPES if islamic_content_mode else HOOK_TYPES
    used_types = load_hook_type_history()
    hook_type = pick_underused_hook_type(used_types, hook_types=pool)
    prompt = build_hook_prompt_fragment(hook_type, hook_types=pool)
"""

import random
from collections import Counter

# Each hook type includes:
# - description: what the pattern is
# - template: skeleton to feed into the Gemini prompt
# - examples: real-world reference lines (for prompt few-shot, NOT for reuse verbatim)
HOOK_TYPES = {
    "quote_cold_open": {
        "description": (
            "Open with a 5-12 word quote from a stoic/historical figure with "
            "no preamble, then reveal who said it plus a surprising context."
        ),
        "template": "\"{quote}\" — {figure} said this while {surprising_context}.",
        "examples": [
            "Marcus Aurelius wrote this while ruling during a plague.",
            "Seneca said this the same year he was ordered to die.",
        ],
    },
    "biographical_contrast": {
        "description": (
            "Highlight that several historical figures had wildly different "
            "lives but share one surprising trait or habit."
        ),
        "template": "{figure_a}. {figure_b}. {figure_c}. Different lives, one habit that saved them all.",
        "examples": [
            "A slave. An emperor. A playwright who advised a tyrant.",
        ],
    },
    "rhetorical_gut_punch": {
        "description": "Open with a sharp rhetorical question that implies discomfort.",
        "template": "What if {uncomfortable_possibility}?",
        "examples": [
            "What if the thing you're avoiding is the only thing that can save you?",
            "Why do the people who suffer most end up the strongest?",
        ],
    },
    "delayed_reveal": {
        "description": (
            "Withhold the subject/punchline for 2-3 seconds using a visual or "
            "verbal cliffhanger structure to force continued watching."
        ),
        "template": "{setup_without_naming_subject}... and it's not what you think.",
        "examples": [],
    },
    "professional_angle": {
        "description": (
            "Frame ancient wisdom through a modern business/career lens to "
            "attract the higher-RPM professional demographic."
        ),
        "template": "The {ancient_principle} Roman emperors used — that {modern_authority} still use today.",
        "examples": [
            "The Stoic principle Roman emperors used — that Fortune 500 CEOs still use today.",
        ],
    },
    "raw_testimony": {
        "description": (
            "MulliganBrothers-style: real person recounting hardship in a "
            "grounded, unhyped tone. Best for longer-form or interview-style scripts."
        ),
        "template": "{first_person_hardship_statement}",
        "examples": [],
    },
}

# Pool khusus niche motivasi & edukasi Islam. PENTING: tidak ada contoh yang
# mengklaim mengutip ayat/hadits spesifik dengan nomor/rujukan -- semua
# paraphrase tema/nilai umum (sabar, syukur, tawakal, dll), konsisten dengan
# instruksi "ISLAMIC CONTENT ACCURACY" di prompt utama script_generator.py.
ISLAMIC_HOOK_TYPES = {
    "wisdom_cold_open": {
        "description": (
            "Open with a short (5-12 word) statement of general Islamic "
            "wisdom/value, paraphrased in plain language -- NOT a claimed "
            "direct citation of a specific ayat or hadith -- then reveal "
            "why it matters right now."
        ),
        "template": "{wisdom_statement} -- and here's why that matters more than you think.",
        "examples": [
            "Patience isn't passive. It's the quietest form of strength.",
            "Gratitude changes what you see, not what you have.",
        ],
    },
    "companion_story_contrast": {
        "description": (
            "Highlight that several figures from early Islamic history had "
            "very different life circumstances but shared one common value "
            "or trait -- described in general terms, without claiming exact "
            "quoted hadith text."
        ),
        "template": "{figure_a}. {figure_b}. {figure_c}. Different lives, one value that carried them all.",
        "examples": [
            "A former slave. A wealthy merchant. A young boy. Different lives, one unshakable trust in God.",
        ],
    },
    "rhetorical_gut_punch": {
        "description": "Open with a sharp, reflective question that invites honest self-examination (muhasabah), without being judgmental.",
        "template": "What if {uncomfortable_possibility}?",
        "examples": [
            "What if the thing testing your patience right now is exactly what's building it?",
            "Why does gratitude feel hardest right when we need it most?",
        ],
    },
    "delayed_reveal": {
        "description": (
            "Withhold the subject/punchline for 2-3 seconds using a visual or "
            "verbal cliffhanger structure to force continued watching."
        ),
        "template": "{setup_without_naming_subject}... and it's not what you'd expect.",
        "examples": [],
    },
    "daily_life_application": {
        "description": (
            "Frame a timeless Islamic value through a very concrete, modern "
            "everyday-life scenario (work stress, social media, family "
            "friction) so it feels immediately practical, not abstract."
        ),
        "template": "This is the same principle you need the next time {modern_everyday_scenario}.",
        "examples": [
            "This is the same principle you need the next time your phone won't stop buzzing with bad news.",
        ],
    },
    "reflection_invitation": {
        "description": (
            "A warm, direct invitation to pause and reflect (muhasabah) on "
            "a specific aspect of the viewer's day/heart -- reflective "
            "rather than instructional in tone."
        ),
        "template": "Before you scroll away, ask yourself: {reflective_question}",
        "examples": [
            "Before you scroll away, ask yourself: when was the last time you truly felt at peace?",
        ],
    },
}


def pick_underused_hook_type(recent_hook_types: list[str], lookback: int = 10, hook_types: dict = None) -> str:
    """
    Given a list of the most recently used hook_type keys (most recent last),
    return a hook type weighted toward ones NOT used in the lookback window.
    Mirrors the diversity logic already used for hook text in script_generator.py.

    `hook_types`: pool to pick from (defaults to HOOK_TYPES for backward
    compatibility). Pass ISLAMIC_HOOK_TYPES for the Islamic niche.
    """
    pool = hook_types if hook_types is not None else HOOK_TYPES
    recent = recent_hook_types[-lookback:]
    counts = Counter(recent)
    all_types = list(pool.keys())

    # Types never used recently get priority
    unused = [t for t in all_types if counts[t] == 0]
    if unused:
        return random.choice(unused)

    # Otherwise pick the least-used type
    min_count = min(counts[t] for t in all_types)
    least_used = [t for t in all_types if counts[t] == min_count]
    return random.choice(least_used)


def build_hook_prompt_fragment(hook_type: str, hook_types: dict = None) -> str:
    """Return a prompt fragment describing the chosen hook type + template,
    to inject into the Gemini script-generation prompt for the Hook scene.

    `hook_types`: pool the hook_type belongs to (defaults to HOOK_TYPES).
    """
    pool = hook_types if hook_types is not None else HOOK_TYPES
    entry = pool[hook_type]
    return (
        f"Hook style: {hook_type}\n"
        f"Description: {entry['description']}\n"
        f"Template: {entry['template']}\n"
        "Write an original hook line for THIS video's topic following this "
        "pattern. Do not reuse example wording verbatim."
    )


if __name__ == "__main__":
    # quick manual test
    history = ["quote_cold_open", "quote_cold_open", "rhetorical_gut_punch"]
    chosen = pick_underused_hook_type(history)
    print("Chosen hook type (default pool):", chosen)
    print(build_hook_prompt_fragment(chosen))
    print()
    chosen_islamic = pick_underused_hook_type([], hook_types=ISLAMIC_HOOK_TYPES)
    print("Chosen hook type (Islamic pool):", chosen_islamic)
    print(build_hook_prompt_fragment(chosen_islamic, hook_types=ISLAMIC_HOOK_TYPES))
