#!/usr/bin/env python3
"""
Modul 1: Survey & Research (Multi-Language)
Melakukan pencarian dan analisis video motivasi yang sedang trending di YouTube dan TikTok
untuk pasar Indonesia maupun Internasional (English).
"""

import os
import json
import logging
import yaml
import glob
from datetime import datetime

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = None
    genai_types = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ContentResearcher:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        self.research_config = self.config.get("research", {})
        self.gemini_model_name = self.research_config.get("gemini_model", "gemini-3.6-flash")
        # Aktifkan lewat config.yaml: research.islamic_content_guidelines: true
        # -- kalau aktif, prompt riset topik dapat instruksi tambahan soal
        # akurasi/sensitivitas konten Islami (lihat _fetch_from_gemini).
        self.islamic_content_mode = self.research_config.get("islamic_content_guidelines", False)

        self._gemini_ready = False
        self._gemini_client = None
        api_key = os.environ.get("GEMINI_API_KEY")
        if genai is None:
            logger.warning(
                "Library 'google-genai' belum terinstall (tambahkan ke requirements.txt). "
                "Riset topik akan pakai bank topik statis fallback."
            )
        elif not api_key or api_key == "your_gemini_api_key_here":
            logger.warning(
                "GEMINI_API_KEY tidak diset/masih placeholder di environment. "
                "Riset topik akan pakai bank topik statis fallback."
            )
        else:
            try:
                self._gemini_client = genai.Client(api_key=api_key)
                self._gemini_ready = True
            except Exception as e:
                logger.error(f"Gagal konfigurasi Gemini API ({e}). Pakai bank topik statis fallback.")

    def _prune_old_reports(self, language, keep=20):
        """
        Hapus file reports/research_<lang>_*.json lama, sisakan `keep` yang
        PALING BARU saja. Tanpa ini, tiap panggilan fetch_trending_topics()
        (dipanggil berulang-ulang tiap hari oleh scheduler di main.py) bikin
        file baru yang TIDAK PERNAH dihapus -- di kontainer Railway dengan
        disk terbatas yang jalan terus-menerus, ini lama-lama menghabiskan
        disk. Pola retention-nya sama seperti hook_pattern_history_*.json
        dan topic_history.json yang sudah dibatasi ke N entri terakhir,
        cuma di sini per-FILE bukan per-entri-dalam-1-file.
        """
        pattern = f"reports/research_{language}_*.json"
        files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        for old_file in files[keep:]:
            try:
                os.remove(old_file)
            except Exception as e:
                logger.warning(f"Gagal hapus report lama {old_file}: {e}")

    def fetch_trending_topics(self, language="id"):
        """
        Mengambil topik/tema video yang berpotensi viral untuk niche, bahasa,
        DAN WILAYAH/PASAR tertentu. Prioritas: riset AI via Gemini (dinamis,
        benar-benar mengikuti tren terkini sesuai prompt & wilayah). Kalau
        Gemini tidak tersedia/gagal, otomatis fallback ke bank topik statis
        supaya pipeline tidak pernah berhenti total gara-gara riset.
        """
        keywords = self.research_config.get("keywords", {}).get(language, [])

        # Fallback niche kalau research.niche.<bahasa> HILANG dari config --
        # dibuat CONTEXT-AWARE terhadap islamic_content_mode supaya kalau
        # config.yaml ada typo/key hilang, sistem tetap fallback ke niche
        # ISLAM (bukan diam-diam balik ke niche motivasi umum lama), plus
        # warning eksplisit supaya masalah konfigurasinya ketahuan, bukan
        # cuma silently generate topik dengan niche yang salah.
        niche_config = self.research_config.get("niche", {})
        if language not in niche_config:
            default_niche = (
                "Islamic motivation and Islamic education for everyday life (character, worship, practical wisdom)"
                if self.islamic_content_mode else
                "life motivation & self-discipline"
            )
            logger.warning(
                f"research.niche.{language} tidak ditemukan di config.yaml -- "
                f"pakai fallback default: '{default_niche}'. Cek config.yaml, "
                f"kemungkinan ada typo di key 'niche'."
            )
            niche = default_niche
        else:
            niche = niche_config[language]

        region = self.research_config.get("region", {}).get(
            language, "Indonesia" if language == "id" else "United States"
        )
        logger.info(f"Memulai riset konten trending untuk bahasa: {language.upper()} (wilayah: {region}) dengan keywords: {keywords}")

        insights = None
        if self._gemini_ready:
            insights = self._fetch_from_gemini(niche, keywords, language, region)

        if not insights:
            if self._gemini_ready:
                logger.warning(f"Riset Gemini AI gagal/kosong untuk {language.upper()}, pakai bank topik statis fallback.")
            insights = self._static_fallback_topics(language)

        logger.info(f"Berhasil menganalisis {len(insights)} pola viral untuk bahasa {language.upper()}.")

        # Simpan hasil riset
        os.makedirs("reports", exist_ok=True)
        report_path = f"reports/research_{language}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(insights, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Gagal simpan laporan riset ke {report_path}: {e}")

        self._prune_old_reports(language)

        return insights

    def _fetch_from_gemini(self, niche, keywords, language, region):
        """
        Minta Gemini AI generate ide topik yang berpotensi viral SAAT INI
        untuk niche, bahasa, DAN WILAYAH tertentu -- ini yang membuat riset
        benar-benar dinamis/mengikuti tren pasar yang relevan, bukan daftar
        statis generik yang sama untuk semua audiens.
        """
        lang_name = "Bahasa Indonesia" if language == "id" else "English"

        islamic_guidance = ""
        if self.islamic_content_mode:
            islamic_guidance = """
IMPORTANT CONTENT GUIDELINES for this Islamic niche:
- Focus on universally-accepted Islamic values and character (patience,
  gratitude, sincerity, trust in God, good character, self-reflection) --
  NOT sectarian/khilafiyah (disputed fiqh) topics that different schools
  of thought disagree on.
- Do NOT propose topics that require citing a specific ayat number or
  hadith reference/collection -- topic ideas should center on general
  wisdom/themes, not scripture citations (citation accuracy will be
  handled carefully in a separate script-writing step, with no specific
  verse/hadith numbers used).
- Avoid political topics, sectarian debates, or anything that could be
  divisive across different Muslim communities/schools of thought.
- Keep tone respectful, warm, and reflective -- never preachy, judgmental,
  or shaming.
"""

        prompt = f"""You are a viral short-video trend researcher for the niche "{niche}".
Target audience region/market: {region}.
Target output language: {lang_name}.
Related keywords: {', '.join(keywords) if keywords else niche}.
{islamic_guidance}
Give me 6 DIFFERENT, specific, concrete video topic ideas that are likely to
perform well as 30-60 second vertical short videos (YouTube Shorts / TikTok
style) SPECIFICALLY for viewers in {region} right now. Take into account
what's culturally relevant, currently trending, or top-of-mind for audiences
in that region -- don't give generic topics that ignore the target market.
Avoid generic topics -- make each one a specific angle or story hook.

Respond with ONLY a pure JSON array (no markdown code fences, no explanation
text before or after), where each item has this exact shape:
{{
  "topic": "specific concrete topic phrase written in {lang_name}",
  "hook_style": "short description of a strong opening hook idea for this topic",
  "recommended_hashtags": ["#tag1", "#tag2", "#tag3"]
}}"""
        try:
            response = self._gemini_client.models.generate_content(
                model=self.gemini_model_name,
                contents=prompt,
                # AFC (automatic function calling) tidak relevan -- kita tidak
                # pakai tools/function calling sama sekali. Matikan eksplisit
                # supaya tidak muncul warning "AFC is enabled" di log (noise,
                # tidak memengaruhi hasil, tapi bikin log kotor).
                config=genai_types.GenerateContentConfig(
                    automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True)
                ),
            )
            text = (response.text or "").strip()

            # Jaga-jaga kalau Gemini tetap membungkus dengan ```json ... ```
            # walau sudah diminta tidak, supaya json.loads tidak meledak.
            if text.startswith("```"):
                text = text.strip("`").strip()
                if text.lower().startswith("json"):
                    text = text[4:].strip()

            data = json.loads(text)
            if not isinstance(data, list) or not data:
                raise ValueError("Respons Gemini bukan JSON array yang valid/tidak kosong.")

            normalized = []
            for item in data:
                if isinstance(item, dict) and item.get("topic"):
                    normalized.append({
                        "topic": item["topic"],
                        "avg_views": item.get("avg_views", "-"),
                        "duration": item.get("duration", "-"),
                        "hook_style": item.get("hook_style", ""),
                        "recommended_hashtags": item.get("recommended_hashtags", []),
                        "engagement_score": item.get("engagement_score", 0),
                    })

            if not normalized:
                raise ValueError("Tidak ada item topik valid di respons Gemini.")

            logger.info(f"Gemini AI berhasil generate {len(normalized)} topik trending untuk {language.upper()}.")
            return normalized

        except Exception as e:
            logger.error(f"Gagal riset topik via Gemini AI ({e}).")
            return None

    def _static_fallback_topics(self, language):
        """
        Bank topik STATIS/curated -- dipakai HANYA kalau Gemini tidak
        tersedia/gagal. get_unique_topic() di main.py menyimpan histori 30
        topik terakhir dan menghindari topik yang berulang, jadi bank ini
        perlu cukup banyak variasi -- kalau cuma 2 topik per bahasa, setelah
        2x jalan "unused_topics" akan kosong dan sistem muter di topik yang
        itu-itu saja.

        CATATAN NICHE ISLAM: topik-topik di bawah SENGAJA hanya berupa TEMA/
        HIKMAH UMUM (sabar, syukur, tawakal, dll), TANPA menyebut nomor ayat
        Al-Qur'an atau hadits spesifik -- sesuai keputusan untuk menghindari
        risiko AI salah kutip referensi keagamaan. script_generator.py yang
        menulis narasi lengkap dari topik ini juga diberi instruksi yang sama.
        """
        if language == "id":
            return [
                {
                    "topic": "Sabar ketika ujian hidup terasa berat",
                    "avg_views": "1.2M", "duration": "42 detik",
                    "hook_style": "Pertanyaan reflektif ('Kenapa ujian datang justru saat kita merasa paling lemah?')",
                    "recommended_hashtags": ["#motivasiislami", "#sabar", "#hijrah", "#muhasabah"],
                    "engagement_score": 9.5
                },
                {
                    "topic": "Syukur di tengah kekurangan, bukan cuma saat berlimpah",
                    "avg_views": "2.1M", "duration": "40 detik",
                    "hook_style": "Kontras dua orang dengan kondisi berbeda tapi sama-sama bersyukur",
                    "recommended_hashtags": ["#syukur", "#motivasiislami", "#ketenangan"],
                    "engagement_score": 9.4
                },
                {
                    "topic": "Tawakal setelah berusaha maksimal, bukan pengganti usaha",
                    "avg_views": "1.8M", "duration": "45 detik",
                    "hook_style": "Meluruskan kesalahpahaman umum soal makna tawakal",
                    "recommended_hashtags": ["#tawakal", "#ikhtiar", "#motivasiislami"],
                    "engagement_score": 9.3
                },
                {
                    "topic": "Menjaga hati dari iri dan dengki di era media sosial",
                    "avg_views": "2.6M", "duration": "48 detik",
                    "hook_style": "Statistik/observasi soal dampak media sosial pada rasa cukup diri",
                    "recommended_hashtags": ["#akhlakmulia", "#hasad", "#muhasabahdiri"],
                    "engagement_score": 9.6
                },
                {
                    "topic": "Adab kecil sehari-hari yang sering dilupakan",
                    "avg_views": "1.5M", "duration": "38 detik",
                    "hook_style": "Daftar singkat kebiasaan sederhana dengan makna besar",
                    "recommended_hashtags": ["#adabislami", "#akhlak", "#edukasiislam"],
                    "engagement_score": 9.2
                },
                {
                    "topic": "Muhasabah diri sebelum tidur -- kebiasaan yang mengubah hidup",
                    "avg_views": "1.9M", "duration": "44 detik",
                    "hook_style": "Ajakan reflektif menutup hari dengan introspeksi",
                    "recommended_hashtags": ["#muhasabah", "#hijrah", "#ketenangan hati"],
                    "engagement_score": 9.4
                },
            ]
        else:  # language == "en"
            return [
                {
                    "topic": "Patience when life's trials feel unbearable",
                    "avg_views": "3.2M", "duration": "42s",
                    "hook_style": "Reflective question ('Why do trials come exactly when we feel weakest?')",
                    "recommended_hashtags": ["#islamicmotivation", "#patience", "#muslim", "#selfreflection"],
                    "engagement_score": 9.5
                },
                {
                    "topic": "Gratitude in scarcity, not just abundance",
                    "avg_views": "4.1M", "duration": "40s",
                    "hook_style": "Contrast between two people in different circumstances, both grateful",
                    "recommended_hashtags": ["#gratitude", "#islamicmotivation", "#innerpeace"],
                    "engagement_score": 9.4
                },
                {
                    "topic": "Trusting God after doing your part, not instead of it",
                    "avg_views": "3.6M", "duration": "45s",
                    "hook_style": "Correcting a common misunderstanding about trust in God",
                    "recommended_hashtags": ["#tawakkul", "#muslimmindset", "#faith"],
                    "engagement_score": 9.3
                },
                {
                    "topic": "Guarding your heart from envy in the social media age",
                    "avg_views": "5.0M", "duration": "48s",
                    "hook_style": "Observation about social media's effect on contentment",
                    "recommended_hashtags": ["#goodcharacter", "#muslimlife", "#selfreflection"],
                    "engagement_score": 9.6
                },
                {
                    "topic": "Small daily manners people forget",
                    "avg_views": "2.9M", "duration": "38s",
                    "hook_style": "Short list of simple habits with deep meaning",
                    "recommended_hashtags": ["#islamicetiquette", "#character", "#muslimeducation"],
                    "engagement_score": 9.2
                },
                {
                    "topic": "A nightly self-reflection habit that changes everything",
                    "avg_views": "3.7M", "duration": "44s",
                    "hook_style": "Reflective invitation to end the day with introspection",
                    "recommended_hashtags": ["#selfreflection", "#muslimmindset", "#peace"],
                    "engagement_score": 9.4
                },
            ]
        # Simpan hasil riset
if __name__ == "__main__":
    researcher = ContentResearcher()
    print("--- INDONESIA ---")
    print(json.dumps(researcher.fetch_trending_topics("id"), indent=2, ensure_ascii=False))
    print("\n--- ENGLISH ---")
    print(json.dumps(researcher.fetch_trending_topics("en"), indent=2, ensure_ascii=False))
