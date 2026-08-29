#!/usr/bin/env python3
"""
Modul 2: Script Generator (Multi-Language)
Menghasilkan script video pendek motivasi dalam Bahasa Indonesia atau Inggris.
Menggunakan Gemini AI (sama seperti research.py) sebagai sumber utama,
dengan fallback template variatif kalau API gagal/tidak tersedia.
"""

import os
import json
import logging
import yaml
import random as _random
from datetime import datetime
from pathlib import Path

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = None
    genai_types = None

try:
    from .hook_patterns import HOOK_TYPES, ISLAMIC_HOOK_TYPES, pick_underused_hook_type, build_hook_prompt_fragment
except ImportError:
    from hook_patterns import HOOK_TYPES, ISLAMIC_HOOK_TYPES, pick_underused_hook_type, build_hook_prompt_fragment

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Lapis kedua keamanan konten (di luar instruksi prompt) -- daftar kata
# yang tidak boleh lolos ke video yang di-auto-publish tanpa review manusia.
UNSAFE_CONTENT_TERMS = [
    "suicide", "bunuh diri", "self-harm", "self harm", "menyakiti diri",
    "kill yourself", "kill himself", "kill herself", "mengakhiri hidup",
    "gantung diri", "overdose", "self-mutilation",
]


def _find_unsafe_term(*texts):
    """Return kata sensitif pertama yang ditemukan di gabungan teks, atau None kalau aman."""
    combined = " ".join(t for t in texts if t).lower()
    return next((t for t in UNSAFE_CONTENT_TERMS if t in combined), None)


class ScriptGenerator:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        self.script_config = self.config.get("script_generator", {})
        self.gemini_model_name = self.script_config.get("gemini_model", "gemini-3.6-flash")

        # Nama niche buat ditampilkan di prompt -- dulu hardcoded "Life
        # Motivation niche", sekarang configurable lewat
        # script_generator.niche_label.<lang> supaya ganti niche (mis. ke
        # Islamic motivation & education) tidak perlu edit kode.
        self.niche_label = self.script_config.get("niche_label", {})

        # Aktifkan lewat config.yaml: script_generator.islamic_content_guidelines: true
        # -- instruksi akurasi/sensitivitas konten Islami ditambahkan ke
        # prompt (lihat _generate_via_gemini). Konsisten dengan flag yang
        # sama di research.py.
        self.islamic_content_mode = self.script_config.get("islamic_content_guidelines", False)

        # Baca flag filter visual dari video_creator (config.yaml sama)
        # supaya visual_prompt yang di-generate SELARAS dengan filter yang
        # nanti benar-benar dijalankan creator.py -- kalau tidak, AI bisa
        # generate visual_prompt "orang berjalan di taman" padahal filter
        # avoid_human_figures_in_visuals aktif dan bakal nolak semua hasil
        # foto/video yang ada orangnya (buang-buang percobaan/kuota).
        video_creator_config = self.config.get("video_creator", {})
        self.avoid_human_figures = video_creator_config.get("avoid_human_figures_in_visuals", False)

        self._gemini_ready = False
        self._gemini_client = None
        api_key = os.environ.get("GEMINI_API_KEY")
        if genai is None:
            logger.warning(
                "Library 'google-genai' belum terinstall (tambahkan ke requirements.txt). "
                "Script akan pakai template fallback."
            )
        elif not api_key or api_key == "your_gemini_api_key_here":
            logger.warning(
                "GEMINI_API_KEY tidak diset/masih placeholder di environment. "
                "Script akan pakai template fallback."
            )
        else:
            try:
                self._gemini_client = genai.Client(api_key=api_key)
                self._gemini_ready = True
            except Exception as e:
                logger.error(f"Gagal konfigurasi Gemini API ({e}). Script akan pakai template fallback.")

    def _load_recent_patterns(self, language, limit=8):
        """
        Baca riwayat gaya hook/judul dari beberapa video terakhir -- dipakai
        supaya prompt bisa eksplisit minta Gemini JANGAN mengulang pola
        rhetorical yang sama terus-menerus. Kebijakan YouTube 2026 eksplisit
        menyasar "mass-produced content with minimal variation" -- ini
        pertahanan konkret terhadap risiko itu, bukan cuma andalkan
        keberagaman acak dari AI.
        """
        history_file = f"reports/hook_pattern_history_{language}.json"
        if not os.path.exists(history_file):
            return []
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data[-limit:]
        except Exception:
            return []

    def _save_recent_pattern(self, language, title, hook, hook_type=None):
        """Simpan judul+hook+hook_type video ini ke riwayat, supaya video
        BERIKUTNYA tahu harus beda gaya -- baik dari sisi kata-kata (title/hook)
        maupun dari sisi STRUKTUR hook (hook_type, lihat hook_patterns.py)."""
        history_file = f"reports/hook_pattern_history_{language}.json"
        os.makedirs("reports", exist_ok=True)
        history = []
        if os.path.exists(history_file):
            try:
                with open(history_file, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []
        history.append({"title": title, "hook": hook, "hook_type": hook_type})
        history = history[-20:]  # simpan 20 terakhir saja
        try:
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Gagal simpan riwayat pola hook: {e}")

    def _hook_type_pool(self):
        """Pilih pool hook type sesuai niche aktif -- ISLAMIC_HOOK_TYPES kalau
        islamic_content_guidelines aktif, HOOK_TYPES (default, stoic/motivation
        umum) kalau tidak."""
        return ISLAMIC_HOOK_TYPES if self.islamic_content_mode else HOOK_TYPES

    def _pick_hook_type(self, recent_patterns):
        """Pilih hook_type (struktur hook, bukan kata-kata) yang paling
        jarang dipakai di riwayat terakhir -- lapisan diversity TAMBAHAN di
        atas riwayat title/hook literal yang sudah ada. Kalau riwayat lama
        belum punya field hook_type (video sebelum fitur ini ada), itu
        otomatis dianggap None dan diabaikan oleh pick_underused_hook_type."""
        recent_types = [p.get("hook_type") for p in recent_patterns if p.get("hook_type")]
        return pick_underused_hook_type(recent_types, hook_types=self._hook_type_pool())

    def generate_script(self, topic_data=None, language="id"):
        """
        Menghasilkan script video short berdasarkan topik dan bahasa.
        Prioritas: Gemini AI. Fallback: template variatif per topik.
        """
        topic = topic_data.get("topic", "Motivation") if topic_data else "Motivation for success"

        # Filter keamanan di titik PALING AWAL -- topic ini datang dari
        # research.py, yang prompt Gemini-nya TIDAK punya instruksi content
        # safety sama sekali (beda dengan prompt di file ini). Kalau topic
        # kebetulan mengandung bahasa sensitif, dan nanti Gemini di sini
        # gagal/tidak tersedia sehingga jatuh ke _build_fallback_script(),
        # topic mentah itu akan disisipkan LANGSUNG ke title/narration TANPA
        # pernah melewati _find_unsafe_term() (beda dengan jalur AI yang
        # outputnya selalu di-scan). Makanya disaring di sini, di sumbernya,
        # supaya KEDUA jalur (AI maupun fallback) sama-sama terlindungi.
        unsafe_hit = _find_unsafe_term(topic)
        if unsafe_hit:
            safe_default = "Motivasi dan disiplin diri" if language == "id" else "Motivation and self-discipline"
            logger.error(
                f"Topic hasil riset mengandung bahasa sensitif ('{unsafe_hit}'): '{topic}'. "
                f"Diganti dengan topic default aman ('{safe_default}') sebelum dipakai di jalur manapun."
            )
            topic = safe_default

        logger.info(f"Membuat script video ({language.upper()}) untuk topik: '{topic}'")

        recent_patterns = self._load_recent_patterns(language)
        hook_type = self._pick_hook_type(recent_patterns)
        logger.info(f"Hook type dipilih untuk video ini: '{hook_type}' ({self._hook_type_pool()[hook_type]['description']})")

        script_json = None
        if self._gemini_ready:
            script_json = self._generate_via_gemini(topic, language, recent_patterns, hook_type)

        if script_json:
            logger.info(f"Script ({language.upper()}) berhasil di-generate menggunakan AI (Gemini).")
            self._save_recent_pattern(
                language, script_json.get("title", ""), script_json.get("hook", ""), hook_type
            )
            return script_json

        # Fallback script -- tetap dipakai kalau Gemini gagal/tidak tersedia.
        # Catatan: template fallback pakai gaya hook-nya sendiri (bukan dari
        # hook_patterns.py), jadi hook_type disimpan sebagai None supaya
        # tidak mendistorsi statistik diversity punya hook AI asli.
        logger.warning(
            "Script dibuat dari FALLBACK TEMPLATE, bukan AI. "
            "Video hasilnya akan jauh kurang bervariasi dibanding pakai AI asli."
        )
        fallback = self._build_fallback_script(topic, language)
        self._save_recent_pattern(language, fallback.get("title", ""), fallback.get("hook", ""), hook_type=None)
        return fallback

    def _generate_via_gemini(self, topic, language, recent_patterns=None, hook_type=None):
        lang_name = "Bahasa Indonesia" if language == "id" else "English (Natural native style)"
        style = self.script_config.get("style", {}).get(language, "inspiring")

        recent_patterns_block = ""
        if recent_patterns:
            examples = "\n".join(f'  - Title: "{p.get("title","")}" / Hook: "{p.get("hook","")}"' for p in recent_patterns)
            recent_patterns_block = f"""
AVOID REPETITION (critical for avoiding YouTube's "mass-produced content
with minimal variation" policy -- channels get demonetized/terminated for
this): here are the titles/hooks from this channel's recent videos. Your
new title and hook must use a GENUINELY DIFFERENT rhetorical structure,
sentence pattern, and opening word choice than ALL of these -- not just
different words, a different STYLE of opening:
{examples}
"""

        hook_type_block = ""
        if hook_type:
            hook_type_block = f"""
REQUIRED HOOK STRUCTURE for scene 1 (this is a further, more SPECIFIC
diversity constraint on top of the repetition rule above -- it dictates the
underlying rhetorical pattern, not just wording):
{build_hook_prompt_fragment(hook_type, hook_types=self._hook_type_pool())}
"""

        niche_name = self.niche_label.get(language) or "Life Motivation"

        islamic_guidance = ""
        if self.islamic_content_mode:
            islamic_guidance = """
ISLAMIC CONTENT ACCURACY (non-negotiable, this is auto-published to a
Muslim audience who will notice inaccuracies immediately -- credibility
matters more than any single video):
- Do NOT cite a specific ayat (verse) number or hadith reference/collection
  anywhere in the script. Never write things like "QS Al-Baqarah: 153" or
  "HR Bukhari no. 123" or attribute an exact quote to the Prophet ﷺ or a
  companion -- even if it sounds plausible, an AI-generated citation like
  this can be WRONG and spreading a fabricated hadith is a serious matter
  in Islam. Instead, reference general, widely-known Islamic values and
  themes (patience/sabr, gratitude/syukur, sincerity/ikhlas, trust in
  God/tawakal, good character/akhlak) in your own words.
- Do NOT take a position on sectarian/khilafiyah (disputed fiqh) issues
  that different schools of thought disagree on -- stick to universally
  agreed-upon values and character.
- Avoid political topics or anything that could be divisive across
  different Muslim communities.
- Tone must be warm, reflective, and encouraging -- never preachy,
  shaming, or judgmental. Acknowledge real struggle rather than lecturing.
"""

        prompt = f"""Create a viral short video script (30-45 seconds TOTAL --
this exact range is critical for the YouTube Shorts algorithm's watch-time
threshold, do not go under 25s or over 48s combined) in {lang_name} for the
{niche_name} niche.
Specific topic: "{topic}".
Style: {style}.

CONTENT SAFETY (non-negotiable, this content is auto-published with no
human review): NEVER use suicide, self-harm, death, or violence as a
metaphor or hyperbole, even casually (e.g. NEVER phrases like "a suicide
pact with your potential", "killing yourself over X", "dying inside").
Avoid graphic violence, hate speech, or content that could be read as
mocking mental health struggles. Keep the intensity in ambition/discipline
language, not in harm imagery. If in doubt, tone it down.
{islamic_guidance}
ORIGINALITY & AUTHENTICITY (YouTube's 2026 "inauthentic content" policy
actively terminates channels for "mass-produced content with minimal
variation" -- this is a real, enforced risk, not a formality): this script
needs a genuinely SPECIFIC angle or original insight, not a generic
platitude that could apply to any topic. Ground it in a concrete, vivid
detail (a specific scenario, a specific number, a specific moment) rather
than abstract motivational language. Avoid toxic-positivity framing that
dismisses real struggle (e.g. don't tell the audience their tiredness/pain
"isn't real" or "is a lie") -- 2026 audiences respond better to content
that acknowledges struggle honestly while still offering a real way
forward.
{recent_patterns_block}{hook_type_block}
RETENTION-FIRST WRITING (2026 Shorts algorithm ranks on watch-time
percentage, not just views -- a Short that loses viewers early gets
suppressed): every sentence must earn its place, no filler or repeated
points. Front-load the most interesting idea. Make the ending feel
COMPLETE and satisfying (not just cut off) so it invites rewatches --
rewatches and saves matter more than likes now.

NARRATION PUNCTUATION FOR TTS (important -- this is read aloud by a
text-to-speech engine, not displayed as plain text): the TTS engine reads
intonation and pacing DIRECTLY from punctuation, so flat, run-on sentences
with no punctuation produce flat, robotic-sounding speech. Write narration
with natural punctuation the way a real person would actually PAUSE and
EMPHASIZE while speaking -- use commas for short natural pauses, periods
to fully stop and reset tone, and vary sentence length (mix short punchy
sentences with longer ones) rather than a uniform run of same-length
sentences. Avoid super long single sentences with no internal punctuation.

STRUCTURE THE SCRIPT INTO EXACTLY 5 SCENES following this proven viral
storytelling arc -- each scene maps to ONE beat below, in this order:
1. HOOK -- grab attention in the first 3 seconds. Provocative statement,
   surprising claim, or a question that creates a curiosity gap.
2. STORY -- set up a short, concrete, relatable story or scenario (a
   specific moment, not generic advice) that illustrates the topic.
3. EMOTION -- the emotional turning point of that story: the struggle,
   the doubt, the low point, or the realization. Make the audience FEEL it.
4. LESSON -- the actionable takeaway or insight the audience should learn
   from the story. Concrete and specific, not a vague platitude.
5. PUNCHLINE -- a short, memorable closing line that lands hard and
   reinforces the hook -- something quotable, plus an implicit call to
   action (follow/save/share) without being cringey about it.
"""

        if self.avoid_human_figures:
            visual_guidance = """
IMPORTANT visual_prompt guidance: these prompts are used to find stock
footage/generate AI images that become the video's background. This
channel's visual filter REJECTS any image/video containing human figures
(faces, bodies, or silhouettes) -- so every visual_prompt MUST describe
scenes with NO people at all. Prefer: Islamic geometric patterns, mosque
architecture and domes/minarets, Arabic calligraphy art, nature (mountains,
sea, desert, forests, sunrise/sunset skies), prayer beads (tasbih), open
books (without readable text), candles/lanterns, gardens. AVOID defaulting
to dark, dim, night-time, or low-light scenes -- prefer BRIGHT, VISUALLY
CLEAR, well-lit scenes with good color and warm tones (gold, green, warm
white) for MOST scenes. Only use a dim/soft setting for the EMOTION beat
specifically if the story genuinely calls for a low moment -- keep it
moody rather than pitch-black (e.g. warm lamp light, golden-hour dusk).
Make each visual_prompt SPECIFIC (e.g. not just "mosque", add a detail --
"mosque dome silhouette against a pink sunset sky") -- generic prompts
return overused stock clips that YouTube's 2026 "visual uniqueness" filter
penalizes.
"""
        else:
            visual_guidance = """
IMPORTANT visual_prompt guidance: these prompts are used to find stock
footage that becomes the video's background. AVOID defaulting to dark,
dim, night-time, or low-light scenes -- footage like that renders as
mostly black/hard to see on a phone screen, especially at low
resolution. Prefer BRIGHT, VISUALLY CLEAR, well-lit scenes with good
color and contrast (daylight, warm indoor lighting, vivid colors) for
MOST scenes. Only use a dim/night setting for the EMOTION beat
specifically if the story genuinely calls for a low moment -- and even
then keep it moody rather than pitch-black (e.g. warm lamp light,
golden-hour dusk, soft blue twilight -- not "dark room" or "night").
Also make each visual_prompt SPECIFIC and unusual rather than a generic
stock phrase (e.g. not just "person walking", add a specific detail --
"person walking past a fruit stand in golden afternoon light") -- generic
prompts return the exact same overused stock clips every other AI-content
channel uses, which YouTube's 2026 "visual uniqueness" filter penalizes.
"""

        visual_prompt_example = (
            "English description for a BRIGHT scene with NO people -- e.g., "
            "mosque dome against a golden sunset, Arabic calligraphy art, "
            "mountain sunrise, prayer beads on a wooden table"
            if self.avoid_human_figures else
            "English description for BRIGHT, well-lit stock footage background "
            "(e.g., sunny city street, warm golden-hour park, vivid morning workout)"
        )

        prompt += visual_guidance + f"""
Respond with ONLY a pure JSON object (no markdown code fences, no explanation
text before or after) with this exact structure:
{{
  "title": "Clickbait and engaging title",
  "hook": "Strong 3-second hook (same as scene 1's narration, condensed)",
  "scenes": [
    {{
      "scene_number": 1,
      "story_beat": "hook",
      "narration": "Narration text in {language}...",
      "visual_prompt": "{visual_prompt_example}",
      "duration_estimate": 6
    }},
    {{
      "scene_number": 2,
      "story_beat": "story",
      "narration": "...",
      "visual_prompt": "...",
      "duration_estimate": 8
    }},
    {{
      "scene_number": 3,
      "story_beat": "emotion",
      "narration": "...",
      "visual_prompt": "...",
      "duration_estimate": 8
    }},
    {{
      "scene_number": 4,
      "story_beat": "lesson",
      "narration": "...",
      "visual_prompt": "...",
      "duration_estimate": 8
    }},
    {{
      "scene_number": 5,
      "story_beat": "punchline",
      "narration": "...",
      "visual_prompt": "...",
      "duration_estimate": 6
    }}
  ],
  "suggested_hashtags": ["#tag1", "#tag2"]
}}"""

        try:
            config = None
            if genai_types:
                # AFC dimatikan eksplisit -- tidak pakai tools/function calling,
                # cuma bikin warning "AFC is enabled" muncul percuma di log.
                config = genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
                )

            response = self._gemini_client.models.generate_content(
                model=self.gemini_model_name,
                contents=prompt,
                config=config,
            )
            text = (response.text or "").strip()

            # Jaga-jaga kalau tetap dibungkus ```json ... ``` walau sudah diminta tidak.
            if text.startswith("```"):
                text = text.strip("`").strip()
                if text.lower().startswith("json"):
                    text = text[4:].strip()

            data = json.loads(text)

            # Validasi struktur minimal yang dibutuhkan creator.py di bagian hilir.
            if not isinstance(data, dict) or not data.get("scenes"):
                raise ValueError("Respons Gemini tidak punya field 'scenes' yang valid.")
            for scene in data["scenes"]:
                if "narration" not in scene:
                    raise ValueError("Ada scene tanpa field 'narration'.")

            # Lapis kedua keamanan konten -- prompt sudah eksplisit melarang
            # bahasa self-harm/kekerasan, tapi AI generatif tetap bisa lolos
            # sesekali. Scan semua teks yang akan dipublikasikan; kalau ada
            # kata sensitif, JANGAN publikasikan -- treat sebagai kegagalan
            # generate (fallback ke template yang sudah pasti aman) daripada
            # meloloskan konten yang berisiko, mengingat semua ini auto-publish
            # tanpa review manusia.
            hit = _find_unsafe_term(
                data.get("title", ""), data.get("hook", ""),
                *[s.get("narration", "") for s in data["scenes"]]
            )
            if hit:
                raise ValueError(
                    f"Script mengandung bahasa sensitif ('{hit}') yang lolos dari prompt safety "
                    f"-- ditolak sebelum sempat dipakai/di-publish."
                )

            data.setdefault("title", f"Video Motivasi: {topic}")
            data.setdefault("hook", "")
            data.setdefault("suggested_hashtags", [])
            return data

        except Exception as e:
            logger.error(f"Gagal memanggil Gemini API ({e}). Cek GEMINI_API_KEY di environment!")
            return None

    def generate_title_and_hashtags(self, topic_data, language="id", hook=""):
        """
        Generate BEBERAPA varian judul yang dioptimasi khusus untuk viral
        (bukan sekadar judul yang nempel di script narasi), BERDASARKAN hasil
        research (topic, hook_style, hashtag rekomendasi dari research.py)
        -- supaya konsisten dengan sinyal tren yang sudah ditemukan Gemini
        di tahap riset, bukan asal generate ulang dari nol.

        Return dict: {"title": str, "title_alternatives": [str,...], "hashtags": [str,...]}
        """
        topic = (topic_data or {}).get("topic", "")
        research_hook_style = (topic_data or {}).get("hook_style", "")
        research_hashtags = (topic_data or {}).get("recommended_hashtags", []) or []

        if self._gemini_ready:
            result = self._generate_title_hashtags_via_gemini(
                topic, research_hook_style, research_hashtags, hook, language
            )
            if result:
                logger.info(
                    f"Judul & hashtag viral berhasil di-generate via Gemini "
                    f"({len(result['title_alternatives']) + 1} varian judul, {len(result['hashtags'])} hashtag)."
                )
                return result
            logger.warning("Gagal generate judul/hashtag viral via Gemini, pakai fallback template.")

        return self._fallback_title_and_hashtags(topic, language, research_hashtags)

    def _get_or_create_brand_hashtag(self, language):
        """
        Hashtag branding channel yang dipakai KONSISTEN di setiap video
        (bangun 'topic authority' YouTube dari waktu ke waktu). Prioritas:
        1. config.yaml script_generator.brand_hashtag.<lang> -- kalau diisi
           manual, itu SELALU dipakai (override, tidak pernah ditimpa oleh
           auto-generate).
        2. reports/brand_hashtag_<lang>.json -- kalau sudah pernah
           di-auto-generate sebelumnya, pakai yang SAMA lagi (supaya tetap
           konsisten -- BUKAN generate baru tiap video, itu akan
           menghilangkan tujuan branding hashtag ini).
        3. Kalau belum ada di dua tempat itu, generate SEKALI via Gemini
           (diturunkan dari niche_label), lalu SIMPAN ke file supaya video
           berikutnya pakai hasil yang sama, bukan generate ulang lagi.
        """
        manual = self.script_config.get("brand_hashtag", {}).get(language, "").strip()
        if manual:
            return manual

        persisted_file = Path("reports") / f"brand_hashtag_{language}.json"
        if persisted_file.exists():
            try:
                with open(persisted_file, "r", encoding="utf-8") as f:
                    saved = json.load(f).get("hashtag", "").strip()
                if saved:
                    return saved
            except Exception:
                pass

        niche_name = self.niche_label.get(language) or "Islamic Motivation"
        generated = None
        if self._gemini_ready:
            try:
                gen_prompt = (
                    f"Suggest ONE short, catchy YouTube/TikTok branding hashtag "
                    f"(2-4 words, no spaces, CamelCase, starting with #) for a "
                    f"channel in this niche: \"{niche_name}\" ({'Bahasa Indonesia' if language == 'id' else 'English'} "
                    f"audience). This hashtag will be used on EVERY video from "
                    f"this channel to build topic authority, so it must be "
                    f"GENERIC to the whole channel, not tied to any single "
                    f"video's topic. Respond with ONLY the hashtag itself, "
                    f"nothing else -- no explanation, no quotes."
                )
                response = self._gemini_client.models.generate_content(
                    model=self.gemini_model_name,
                    contents=gen_prompt,
                    config=genai_types.GenerateContentConfig(
                        automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True)
                    ),
                )
                candidate = (response.text or "").strip().split()[0] if response.text else ""
                candidate = candidate.strip('"\'')
                if candidate and candidate.startswith("#") and len(candidate) <= 40:
                    generated = candidate
            except Exception as e:
                logger.warning(f"Gagal auto-generate brand hashtag via Gemini ({e}), pakai fallback deterministik.")

        if not generated:
            # Fallback deterministik (tanpa AI) -- ubah niche_label jadi
            # CamelCase hashtag sederhana, supaya tetap ada hasil walau
            # Gemini tidak tersedia.
            words = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in niche_name).split()
            generated = "#" + "".join(w.capitalize() for w in words[:4]) if words else "#DailyReminders"

        try:
            os.makedirs("reports", exist_ok=True)
            with open(persisted_file, "w", encoding="utf-8") as f:
                json.dump({"hashtag": generated, "generated_at": datetime.now().isoformat()}, f, indent=2)
            logger.info(f"Brand hashtag ({language.upper()}) di-generate & disimpan permanen: {generated}")
        except Exception as e:
            logger.warning(f"Gagal simpan brand hashtag yang di-generate ({e}) -- akan generate ulang video berikutnya.")

        return generated

    def _generate_title_hashtags_via_gemini(self, topic, research_hook_style, research_hashtags, hook, language):
        lang_name = "Bahasa Indonesia" if language == "id" else "English"
        islamic_title_hashtag_guidance = ""
        if self.islamic_content_mode:
            brand_tag = self._get_or_create_brand_hashtag(language)
            brand_clause = (
                f' Also ALWAYS include this exact branding hashtag in the list: "{brand_tag}" '
                f"(consistent branding hashtag builds topic authority over time)."
                if brand_tag else ""
            )
            islamic_title_hashtag_guidance = f"""
ISLAMIC NICHE ADJUSTMENTS (based on analysis of top-performing Islamic
motivation/education channels and Shorts in this niche):
- Hashtag count: use 5-8 hashtags total, NOT 8-12 -- in this specific
  niche, more than that measurably hurts reach (over-tagging reads as
  spam to both viewers and the algorithm in this content category).{brand_clause}
- Front-load the core keyword/theme (e.g. "Sabar", "Syukur", "Tawakal",
  "Patience", "Trust in Allah") within the FIRST 40 CHARACTERS of the
  title -- this is where this niche's search-driven traffic comes from.
- Prefer a comforting/reassuring emotional angle when relevant (many
  top-performing titles/hooks in this niche follow a "relatable struggle
  -> Islamic reassurance" arc -- e.g. naming a real everyday hardship,
  then pointing to steadiness/trust/patience) over abstract or purely
  instructional framing.
"""

        prompt = f"""You are a viral YouTube Shorts / TikTok title & hashtag strategist.
Topic (from trend research): "{topic}"
Suggested hook angle (from trend research): "{research_hook_style}"
Hashtags already suggested by trend research: {research_hashtags}
Actual video hook used in this video: "{hook}"
Target language: {lang_name}
{islamic_title_hashtag_guidance}
Generate:
1. 5 DIFFERENT title variants (each under 100 characters), ranked from
   most to least likely to go viral. Each title should use a DIFFERENT
   psychological trigger (curiosity gap, controversy, number/list, direct
   callout, fear of missing out) and should build on the trend research
   above, not ignore it. IMPORTANT: Shorts now surface in YouTube's search
   results carousel, not just the swipe feed -- at least 2 of the 5 titles
   must be genuinely SEARCH-FRIENDLY (contain the actual topic keywords a
   person would type into search), not pure clickbait with no searchable
   terms. Balance virality with discoverability -- don't sacrifice all
   keyword clarity for shock value.
2. A set of {"5-8" if self.islamic_content_mode else "8-12"} hashtags optimized for reach: mix broad discovery tags
   (e.g. #shorts, #motivation) with niche/specific tags relevant to the
   topic, and feel free to reuse/refine the trend-research hashtags above.

Content safety (non-negotiable, auto-published without human review):
never use suicide, self-harm, or violence as a metaphor, even casually.

Respond with ONLY a pure JSON object (no markdown code fences, no
explanation text before or after), with this exact structure:
{{
  "titles_ranked": ["most viral title", "2nd option", "3rd option", "4th option", "5th option"],
  "hashtags": ["#tag1", "#tag2", "..."]
}}"""
        try:
            config = None
            if genai_types:
                # AFC dimatikan eksplisit -- tidak pakai tools/function calling,
                # cuma bikin warning "AFC is enabled" muncul percuma di log.
                config = genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
                )

            response = self._gemini_client.models.generate_content(
                model=self.gemini_model_name,
                contents=prompt,
                config=config,
            )
            text = (response.text or "").strip()
            if text.startswith("```"):
                text = text.strip("`").strip()
                if text.lower().startswith("json"):
                    text = text[4:].strip()

            data = json.loads(text)
            titles = data.get("titles_ranked") or []
            hashtags = data.get("hashtags") or []
            if not titles or not hashtags:
                raise ValueError("titles_ranked/hashtags kosong di respons Gemini.")

            hit = _find_unsafe_term(*titles)
            if hit:
                raise ValueError(f"Judul mengandung bahasa sensitif ('{hit}') -- ditolak.")

            return {
                "title": titles[0],
                "title_alternatives": titles[1:],
                "hashtags": hashtags,
            }
        except Exception as e:
            logger.error(f"Gagal generate judul/hashtag viral via Gemini ({e}).")
            return None

    def _fallback_title_and_hashtags(self, topic, language, research_hashtags=None):
        """
        Fallback kalau Gemini gagal/tidak tersedia -- tetap dipersonalisasi
        dengan topic (bukan template statis 100% identik), dan tetap
        memakai hashtag dari hasil research kalau ada (lebih relevan
        daripada template hashtag generik).
        """
        title_templates = {
            "id": [
                f"Jangan Lewatkan Ini Soal {topic}!",
                f"Rahasia di Balik {topic}",
                f"Ini Yang Perlu Kamu Tahu Soal {topic}",
                f"Kenapa {topic} Penting Banget?",
                f"{topic}: Yang Jarang Dibahas",
            ],
            "en": [
                f"Don't Skip This About {topic}!",
                f"The Secret Behind {topic}",
                f"What You Need to Know About {topic}",
                f"Why {topic} Actually Matters",
                f"{topic}: What Nobody Tells You",
            ],
        }
        titles = title_templates.get(language, title_templates["id"])
        _random.shuffle(titles)

        default_hashtags = (
            ["#motivasi", "#disiplin", "#shorts", "#mindset"] if language == "id"
            else ["#motivation", "#discipline", "#shorts", "#mindset"]
        )
        hashtags = research_hashtags if research_hashtags else default_hashtags

        return {
            "title": titles[0],
            "title_alternatives": titles[1:],
            "hashtags": hashtags,
        }

    def _build_fallback_script(self, topic, language="id"):
        """
        Fallback template ketika AI API gagal/tidak tersedia.
        PENTING: template ini di-parametrisasi dengan `topic` dan dipilih acak
        dari beberapa varian, supaya TIDAK selalu menghasilkan narasi/voiceover
        yang identik walau AI sedang down. Ini tetap jauh lebih terbatas
        dibanding generate AI asli — sebaiknya perbaiki GEMINI_API_KEY.
        """

        # Template fallback GENERIK (motivasi umum) -- dipakai kalau
        # islamic_content_mode TIDAK aktif. Kalau aktif, lihat blok
        # islamic_templates di bawah yang dipakai sebagai gantinya.
        templates = {
            "id": [
                {
                    "title": f"Jangan Berhenti Meski Lelah Menghadapi {topic}",
                    "hook": "Kesuksesan bukan milik orang pintar, tapi milik mereka yang tidak pernah berhenti.",
                    "scenes": [
                        ("hook", "Kesuksesan bukan milik orang pintar, tapi milik mereka yang tidak pernah berhenti.", "Cinematic bright sunlit close-up determined face"),
                        ("story", f"Bayangkan seseorang yang sudah mencoba berkali-kali soal {topic}, gagal, dicemooh, tapi tetap bangun tiap pagi untuk coba lagi.", "Cinematic vivid golden-hour person walking alone outdoors"),
                        ("emotion", "Di titik paling capek itulah, banyak yang memilih berhenti -- padahal itu justru saat paling dekat dengan titik balik.", "Cinematic soft blue twilight window reflective mood, still well-lit"),
                        ("lesson", f"{topic} mengajarkan satu hal: bukan seberapa cepat kamu mulai, tapi seberapa lama kamu mampu bertahan di saat paling sulit.", "Cinematic bright sunrise breaking through clouds hopeful"),
                        ("punchline", "Jangan berhenti sekarang. Titik balikmu mungkin cuma satu langkah lagi.", "Cinematic bright daylight silhouette standing strong mountain peak"),
                    ],
                    "hashtags": ["#motivasi", "#disiplin", "#shorts"],
                },
                {
                    "title": f"Rahasia di Balik Orang yang Bangkit Lagi ({topic})",
                    "hook": "Mereka yang sukses bukan yang tidak pernah jatuh, tapi yang selalu bangkit lagi.",
                    "scenes": [
                        ("hook", "Mereka yang sukses bukan yang tidak pernah jatuh, tapi yang selalu bangkit lagi.", "Cinematic warm daylight person standing up slowly"),
                        ("story", f"Ada masa ketika semua orang di sekitarmu menyerah soal {topic}, satu per satu berhenti, dan kamu mulai ragu apakah ini sepadan.", "Cinematic warm lamp-lit room, cozy isolation mood, still bright"),
                        ("emotion", "Rasanya sendirian, seperti usahamu tidak ada gunanya dibanding orang lain yang sudah berhenti duluan.", "Cinematic softly lit hallway, warm tones, gentle isolation mood"),
                        ("lesson", "Tapi justru di situlah bedanya -- yang bertahan bukan yang paling kuat, tapi yang paling keras kepala untuk tidak menyerah.", "Cinematic vivid sunrise mountain climb silhouette"),
                        ("punchline", "Ketika semua orang berhenti, kamu justru harus tetap berjalan. Itu yang membedakan.", "Cinematic bright triumphant silhouette against vivid golden sky"),
                    ],
                    "hashtags": ["#mentalbaja", "#sukses", "#mindset"],
                },
                {
                    "title": f"Ini Yang Membedakan Orang Sukses Soal {topic}",
                    "hook": "Bukan bakat, tapi konsistensi yang membawa mereka sampai puncak.",
                    "scenes": [
                        ("hook", "Bukan bakat, tapi konsistensi yang membawa mereka sampai puncak.", "Cinematic vivid city daylight determined walk"),
                        ("story", f"Kamu pernah lihat orang yang kelihatannya biasa saja, tapi soal {topic} dia konsisten tiap hari tanpa drama.", "Cinematic bright morning daily routine, natural light"),
                        ("emotion", "Sementara yang lain sibuk cari motivasi baru tiap minggu, dia cuma lakukan hal yang sama, berulang, tanpa banyak bicara.", "Cinematic bright quiet determination close-up"),
                        ("lesson", f"{topic} bukan soal seberapa cepat kamu mulai, tapi seberapa lama kamu mampu bertahan konsisten.", "Cinematic sunlit steady footsteps path forward"),
                        ("punchline", "Motivasi itu cuma percikan. Konsistensi yang bakar apinya sampai selesai.", "Cinematic warm glowing fire silhouette, bright determined figure"),
                    ],
                    "hashtags": ["#konsisten", "#growth", "#shorts"],
                },
            ],
            "en": [
                {
                    "title": f"Don't Stop When {topic} Gets Hard",
                    "hook": "Success doesn't belong to the smartest, it belongs to those who never quit.",
                    "scenes": [
                        ("hook", "Success doesn't belong to the smartest, it belongs to those who never quit.", "Cinematic bright sunlit close-up determined face"),
                        ("story", f"Picture someone who's tried and failed at {topic} over and over, mocked, but still gets up every single morning to try again.", "Cinematic vivid golden-hour person walking alone outdoors"),
                        ("emotion", "Right at that most exhausted point is exactly where most people choose to quit -- when they're actually closest to the turning point.", "Cinematic soft blue twilight window reflective mood, still well-lit"),
                        ("lesson", f"{topic} teaches one thing: it's not about how fast you start, it's about how long you can hold on when it's hardest.", "Cinematic bright sunrise breaking through clouds hopeful"),
                        ("punchline", "Don't stop now. Your turning point might be just one step away.", "Cinematic bright daylight silhouette standing strong mountain peak"),
                    ],
                    "hashtags": ["#motivation", "#discipline", "#shorts"],
                },
                {
                    "title": f"The Secret Behind People Who Bounce Back ({topic})",
                    "hook": "Successful people aren't the ones who never fall, they're the ones who always get back up.",
                    "scenes": [
                        ("hook", "Successful people aren't the ones who never fall, they're the ones who always get back up.", "Cinematic warm daylight person standing up slowly"),
                        ("story", f"There's a moment when everyone around you gives up on {topic}, one by one, and you start wondering if it's even worth it.", "Cinematic warm lamp-lit room, cozy isolation mood, still bright"),
                        ("emotion", "It feels lonely, like your effort doesn't matter compared to everyone who already quit.", "Cinematic softly lit hallway, warm tones, gentle isolation mood"),
                        ("lesson", "But that's exactly the difference -- the ones who make it aren't the strongest, they're just too stubborn to quit.", "Cinematic vivid sunrise mountain climb silhouette"),
                        ("punchline", "When everyone else gives up, that's exactly when you keep moving forward.", "Cinematic bright triumphant silhouette against vivid golden sky"),
                    ],
                    "hashtags": ["#mindset", "#success", "#growth"],
                },
                {
                    "title": f"What Separates Winners on {topic}",
                    "hook": "It's not talent, it's consistency that gets people to the top.",
                    "scenes": [
                        ("hook", "It's not talent, it's consistency that gets people to the top.", "Cinematic vivid city daylight determined walk"),
                        ("story", f"You've probably seen someone who looks completely ordinary, but shows up for {topic} every single day without any drama.", "Cinematic bright morning daily routine, natural light"),
                        ("emotion", "While everyone else is chasing a new burst of motivation every week, they just keep doing the same thing, quietly, over and over.", "Cinematic bright quiet determination close-up"),
                        ("lesson", f"{topic} isn't about how fast you start, it's about how long you can hold on with consistency.", "Cinematic sunlit steady footsteps path forward"),
                        ("punchline", "Motivation is just a spark. Consistency is what keeps the fire burning until it's done.", "Cinematic warm glowing fire silhouette, bright determined figure"),
                    ],
                    "hashtags": ["#consistency", "#growth", "#shorts"],
                },
            ],
        }

        # Template fallback ISLAMI -- dipakai kalau islamic_content_guidelines
        # aktif di config. SENGAJA tanpa nomor ayat/hadits (sama seperti
        # instruksi prompt AI di atas), dan visual_prompt-nya otomatis pilih
        # antara versi "tanpa figur manusia" atau versi biasa, tergantung
        # self.avoid_human_figures -- supaya konsisten dengan filter yang
        # benar-benar dijalankan creator.py.
        no_figures = self.avoid_human_figures
        islamic_templates = {
            "id": [
                {
                    "title": f"Sabar Itu Bukan Diam -- Ini Soal {topic}",
                    "hook": "Sabar bukan berarti diam menerima, tapi tetap tenang sambil terus berusaha.",
                    "scenes": [
                        ("hook", "Sabar bukan berarti diam menerima, tapi tetap tenang sambil terus berusaha.",
                         "Islamic geometric pattern close-up, warm gold tones" if no_figures else "Cinematic bright sunlit close-up determined face"),
                        ("story", f"Ada masa ketika {topic} terasa berat, seakan semua usaha belum juga membuahkan hasil.",
                         "Mountain sunrise, soft warm light, no people" if no_figures else "Cinematic vivid golden-hour person walking alone outdoors"),
                        ("emotion", "Di titik itu, hati mudah bertanya-tanya -- kenapa harus seberat ini?",
                         "Soft twilight window with warm lamp light, no people, reflective mood" if no_figures else "Cinematic soft blue twilight window reflective mood, still well-lit"),
                        ("lesson", f"Tapi {topic} justru sering jadi jalan untuk belajar lebih tenang, lebih ikhlas, dan lebih percaya bahwa semua ada waktunya.",
                         "Sunrise breaking through clouds over open field, no people" if no_figures else "Cinematic bright sunrise breaking through clouds hopeful"),
                        ("punchline", "Tenangkan hati. Yang berat hari ini, bisa jadi jalan menuju sesuatu yang lebih baik.",
                         "Mosque dome silhouette against golden sky, no people" if no_figures else "Cinematic bright daylight silhouette standing strong mountain peak"),
                    ],
                    "hashtags": ["#sabar", "#motivasiislami", "#muhasabah"],
                },
                {
                    "title": f"Syukur Meski {topic} Belum Sesuai Harapan",
                    "hook": "Syukur bukan cuma saat semua berjalan lancar, tapi justru paling berarti saat sulit.",
                    "scenes": [
                        ("hook", "Syukur bukan cuma saat semua berjalan lancar, tapi justru paling berarti saat sulit.",
                         "Warm golden light through window, prayer beads on table, no people" if no_figures else "Cinematic warm daylight person standing up slowly"),
                        ("story", f"Kadang {topic} membuat kita lupa untuk melihat hal-hal kecil yang sebenarnya masih bisa disyukuri.",
                         "Cozy warm-lit room with open book, no people" if no_figures else "Cinematic warm lamp-lit room, cozy isolation mood, still bright"),
                        ("emotion", "Fokus yang terlalu besar pada kekurangan bisa membuat hati terasa berat dan hampa.",
                         "Softly lit hallway, warm tones, gentle empty mood, no people" if no_figures else "Cinematic softly lit hallway, warm tones, gentle isolation mood"),
                        ("lesson", "Bersyukur bukan mengabaikan masalah, tapi memilih tetap melihat kebaikan di tengah kesulitan.",
                         "Sunrise over calm sea, no people" if no_figures else "Cinematic vivid sunrise mountain climb silhouette"),
                        ("punchline", "Hati yang bersyukur akan selalu menemukan cahaya, sekecil apapun itu.",
                         "Golden sky over mosque minaret, no people" if no_figures else "Cinematic bright triumphant silhouette against vivid golden sky"),
                    ],
                    "hashtags": ["#syukur", "#motivasiislami", "#ketenangan"],
                },
                {
                    "title": f"Tawakal Setelah Berusaha -- Pelajaran dari {topic}",
                    "hook": "Tawakal itu setelah ikhtiar maksimal, bukan pengganti usaha.",
                    "scenes": [
                        ("hook", "Tawakal itu setelah ikhtiar maksimal, bukan pengganti usaha.",
                         "Islamic geometric pattern, warm morning light, no people" if no_figures else "Cinematic vivid city daylight determined walk"),
                        ("story", f"Banyak yang berhenti berusaha soal {topic} dan menyebutnya tawakal, padahal tawakal datang setelah usaha, bukan sebelum.",
                         "Open book and prayer beads on wooden table, morning light, no people" if no_figures else "Cinematic bright morning daily routine, natural light"),
                        ("emotion", "Ada ketenangan berbeda ketika kita sudah berusaha sepenuh hati, lalu menyerahkan hasilnya.",
                         "Calm still lake at dawn, no people" if no_figures else "Cinematic bright quiet determination close-up"),
                        ("lesson", f"{topic} mengajarkan bahwa hasil terbaik datang dari kombinasi usaha sungguh-sungguh dan hati yang pasrah.",
                         "Sunlit path through a garden, no people" if no_figures else "Cinematic sunlit steady footsteps path forward"),
                        ("punchline", "Usahakan yang terbaik, lalu tenangkan hati dengan tawakal.",
                         "Warm glowing lantern at dusk, no people" if no_figures else "Cinematic warm glowing fire silhouette, bright determined figure"),
                    ],
                    "hashtags": ["#tawakal", "#ikhtiar", "#motivasiislami"],
                },
            ],
            "en": [
                {
                    "title": f"Patience Isn't Silence -- A Lesson from {topic}",
                    "hook": "Patience doesn't mean staying silent, it means staying calm while you keep trying.",
                    "scenes": [
                        ("hook", "Patience doesn't mean staying silent, it means staying calm while you keep trying.",
                         "Islamic geometric pattern close-up, warm gold tones" if no_figures else "Cinematic bright sunlit close-up determined face"),
                        ("story", f"There are moments when {topic} feels heavy, like your effort hasn't paid off yet.",
                         "Mountain sunrise, soft warm light, no people" if no_figures else "Cinematic vivid golden-hour person walking alone outdoors"),
                        ("emotion", "In that moment, it's easy for your heart to ask -- why does it have to be this hard?",
                         "Soft twilight window with warm lamp light, no people, reflective mood" if no_figures else "Cinematic soft blue twilight window reflective mood, still well-lit"),
                        ("lesson", f"But {topic} often becomes the path to learning real calm, real sincerity, and real trust that everything has its time.",
                         "Sunrise breaking through clouds over open field, no people" if no_figures else "Cinematic bright sunrise breaking through clouds hopeful"),
                        ("punchline", "Steady your heart. What feels heavy today may be the path to something better.",
                         "Mosque dome silhouette against golden sky, no people" if no_figures else "Cinematic bright daylight silhouette standing strong mountain peak"),
                    ],
                    "hashtags": ["#patience", "#islamicmotivation", "#selfreflection"],
                },
                {
                    "title": f"Gratitude Even When {topic} Isn't Going as Planned",
                    "hook": "Gratitude isn't just for when things go well -- it matters most when they don't.",
                    "scenes": [
                        ("hook", "Gratitude isn't just for when things go well -- it matters most when they don't.",
                         "Warm golden light through window, prayer beads on table, no people" if no_figures else "Cinematic warm daylight person standing up slowly"),
                        ("story", f"Sometimes {topic} makes us forget the small things we could still be thankful for.",
                         "Cozy warm-lit room with open book, no people" if no_figures else "Cinematic warm lamp-lit room, cozy isolation mood, still bright"),
                        ("emotion", "Focusing too much on what's missing can leave the heart feeling heavy and empty.",
                         "Softly lit hallway, warm tones, gentle empty mood, no people" if no_figures else "Cinematic softly lit hallway, warm tones, gentle isolation mood"),
                        ("lesson", "Gratitude doesn't mean ignoring the problem -- it means choosing to still see the good in the middle of difficulty.",
                         "Sunrise over calm sea, no people" if no_figures else "Cinematic vivid sunrise mountain climb silhouette"),
                        ("punchline", "A grateful heart always finds light, however small it is.",
                         "Golden sky over mosque minaret, no people" if no_figures else "Cinematic bright triumphant silhouette against vivid golden sky"),
                    ],
                    "hashtags": ["#gratitude", "#islamicmotivation", "#innerpeace"],
                },
                {
                    "title": f"Trusting God After You've Tried -- A Lesson from {topic}",
                    "hook": "Trusting God comes after your best effort, not instead of it.",
                    "scenes": [
                        ("hook", "Trusting God comes after your best effort, not instead of it.",
                         "Islamic geometric pattern, warm morning light, no people" if no_figures else "Cinematic vivid city daylight determined walk"),
                        ("story", f"Many people stop trying on {topic} and call it trust, when real trust in God comes after the effort, not before it.",
                         "Open book and prayer beads on wooden table, morning light, no people" if no_figures else "Cinematic bright morning daily routine, natural light"),
                        ("emotion", "There's a different kind of calm when you've truly given your best, then let go of the outcome.",
                         "Calm still lake at dawn, no people" if no_figures else "Cinematic bright quiet determination close-up"),
                        ("lesson", f"{topic} teaches that the best results come from real effort paired with a heart at peace.",
                         "Sunlit path through a garden, no people" if no_figures else "Cinematic sunlit steady footsteps path forward"),
                        ("punchline", "Do your best, then let your heart rest in trust.",
                         "Warm glowing lantern at dusk, no people" if no_figures else "Cinematic warm glowing fire silhouette, bright determined figure"),
                    ],
                    "hashtags": ["#tawakkul", "#islamicmotivation", "#muslimmindset"],
                },
            ],
        }

        template_pool = islamic_templates if self.islamic_content_mode else templates
        lang_templates = template_pool.get(language, template_pool["id"])
        picked = _random.choice(lang_templates)

        scene_count = len(picked["scenes"])
        scenes = []
        for i, (beat, narration, visual_prompt) in enumerate(picked["scenes"], start=1):
            # Hook & punchline dibuat lebih pendek, story/emotion/lesson lebih
            # panjang -- mengikuti pacing alami pola hook-story-emotion-lesson-punchline.
            duration = 6 if beat in ("hook", "punchline") else 8
            scenes.append({
                "scene_number": i,
                "story_beat": beat,
                "narration": narration,
                "visual_prompt": visual_prompt,
                "duration_estimate": duration,
            })

        return {
            "title": picked["title"],
            "hook": picked["hook"],
            "scenes": scenes,
            "suggested_hashtags": picked["hashtags"],
        }

if __name__ == "__main__":
    generator = ScriptGenerator()
    print("--- INDONESIA ---")
    print(json.dumps(generator.generate_script(language="id"), indent=2, ensure_ascii=False))
    print("\n--- ENGLISH ---")
    print(json.dumps(generator.generate_script(language="en"), indent=2, ensure_ascii=False))
