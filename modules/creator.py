#!/usr/bin/env python3
"""
Modul 3: Video Creator (Multi-Language) - Fixed Version
"""

import os
import json
import logging
import yaml
import asyncio
import requests
import random
import shutil
import subprocess
import wave
import re
import warnings
import time
from io import BytesIO
from pathlib import Path
from moviepy import VideoFileClip, AudioFileClip, AudioClip, TextClip, CompositeVideoClip, CompositeAudioClip, ColorClip, ImageClip, vfx, afx
from moviepy.video.fx import Resize, Crop
from PIL import ImageFont, Image, ImageDraw
import edge_tts
from datetime import datetime

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = None
    genai_types = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("VideoCreator")

# Font fallback yang umum tersedia di image Linux/Docker (Debian/Ubuntu).
# Kalau tidak ada satupun yang ketemu, subtitle akan pakai wrap kata biasa
# (kurang presisi tapi tetap jalan) dan sebuah warning akan muncul di log.
DEFAULT_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def resolve_font_path(configured_path=None):
    """Cari path font .ttf yang valid: prioritas config, lalu fallback umum."""
    candidates = ([configured_path] if configured_path else []) + DEFAULT_FONT_CANDIDATES
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def wrap_text_pixel_accurate(text, font_path, font_size, max_width):
    """
    Wrap teks berdasarkan lebar pixel AKTUAL (bukan jumlah kata), supaya
    tiap baris benar-benar muat dalam `max_width`. method='caption' bawaan
    MoviePy terbukti tidak akurat saat dikombinasikan dengan stroke_width
    (baris jadi overflow/terpotong) -- makanya wrapping dilakukan manual
    di sini, lalu dirender dengan method='label' yang menghormati baris
    yang sudah kita hitung, tanpa auto-wrap ulang.
    """
    if not font_path:
        # Tidak ada font file yang ketemu -> fallback kasar per-kata,
        # lebih baik daripada crash, tapi kurang presisi.
        words = text.split()
        lines, current = [], ""
        for i, word in enumerate(words):
            current += word + " "
            if (i + 1) % 5 == 0:
                lines.append(current.strip())
                current = ""
        if current:
            lines.append(current.strip())
        return "\n".join(lines)

    font = ImageFont.truetype(font_path, font_size)
    words = text.split()
    lines, current = [], ""
    for word in words:
        trial = (current + " " + word).strip()
        if font.getlength(trial) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)

class VideoCreator:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f) or {}
        self.video_config = self.config.get("video_creator", {})
        self.assets_dir = Path("assets")
        self.temp_dir = self.assets_dir / "temp"
        self.output_dir = self.assets_dir / "output"
        self.ai_images_dir = self.assets_dir / "ai_images"
        
        for d in [self.temp_dir, self.output_dir, self.assets_dir / "footage", self.assets_dir / "music", self.ai_images_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Gemini image generation -- dipakai sebagai visual alternatif kalau
        # download stock footage Pexels gagal, supaya video tidak jatuh ke
        # background warna solid polos. Pakai GEMINI_API_KEY yang sama
        # dengan research.py/script_generator.py (satu API key untuk semua).
        self.gemini_image_model = self.video_config.get("gemini_image_model", "gemini-3.1-flash-image")
        self._gemini_image_ready = False
        self._gemini_client = None
        api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
        if genai is None:
            logger.warning(
                "Library 'google-genai' belum terinstall -- generate gambar AI "
                "untuk visual pengganti tidak akan tersedia (fallback ke warna solid)."
            )
        elif not api_key or api_key == "your_gemini_api_key_here":
            logger.warning(
                "GEMINI_API_KEY tidak diset -- generate gambar AI untuk visual "
                "pengganti tidak akan tersedia (fallback ke warna solid)."
            )
        elif not (api_key.startswith("AIza") or api_key.startswith("AQ")) or len(api_key) < 20:
            logger.error(
                f"GEMINI_API_KEY tampak TIDAK VALID (panjang: {len(api_key)} karakter, "
                f"awalan: '{api_key[:6]}...') -- API key Gemini/Google Cloud biasanya diawali "
                f"'AIza' atau 'AQ'. Kemungkinan ada whitespace tersembunyi "
                f"saat copy-paste, key salah/tidak lengkap, atau key sudah di-revoke. "
                f"Cek ulang GEMINI_API_KEY di Railway -> Variables. Gambar AI/filter "
                f"visual/TTS Gemini tidak akan tersedia sampai ini diperbaiki."
            )
        else:
            try:
                self._gemini_client = genai.Client(api_key=api_key)
                self._gemini_image_ready = True
            except Exception as e:
                logger.error(f"Gagal konfigurasi Gemini image client ({e}).")

    def _avoid_women_filter_active(self):
        """Cek apakah filter 'hindari gambar wanita' aktif lewat config.yaml:
        video_creator: { avoid_women_in_visuals: true }. Default OFF supaya
        tidak mengubah perilaku pipeline yang sudah ada tanpa disengaja."""
        return bool(self.video_config.get("avoid_women_in_visuals", False))

    def _avoid_human_figures_active(self):
        """Cek apakah filter 'hindari SEMUA figur manusia' aktif lewat
        config.yaml: video_creator: { avoid_human_figures_in_visuals: true }.
        Ini SUPERSET dari avoid_women_in_visuals (kalau aktif, otomatis
        mencakup wanita juga) -- dipakai untuk niche yang butuh visual tanpa
        figur manusia sama sekali (mis. motivasi/edukasi Islam: kaligrafi,
        masjid, alam, pola geometris)."""
        return bool(self.video_config.get("avoid_human_figures_in_visuals", False))

    def _visual_filter_active(self):
        """True kalau SALAH SATU filter visual (wanita ATAU semua figur manusia) aktif."""
        return self._avoid_human_figures_active() or self._avoid_women_filter_active()

    def _visual_filter_question(self):
        """Pertanyaan klasifikasi yang dikirim ke Gemini Vision -- pilih yang
        PALING LUAS cakupannya kalau kedua filter entah bagaimana aktif
        bersamaan (avoid_human_figures mencakup avoid_women)."""
        if self._avoid_human_figures_active():
            return (
                "Does this image show any human figure or person -- a face, "
                "body, or clear human silhouette -- even partially (e.g. just "
                "hands, or a small figure in the background)? Answer with "
                "ONLY one word: YES or NO."
            )
        return (
            "Does this image show a woman or girl (a female human), even "
            "partially -- e.g. just hands, hair, silhouette, or a small figure "
            "in the background? Answer with ONLY one word: YES or NO."
        )

    def _image_bytes_violates_visual_filter(self, image_bytes):
        """
        Klasifikasi lewat Gemini Vision: apakah gambar ini melanggar filter
        visual yang sedang aktif (wanita saja, ATAU semua figur manusia --
        lihat _visual_filter_question())?

        FAIL-OPEN BY DESAIN: kalau client Gemini tidak siap atau request
        gagal (network/quota/dsb), gambar dianggap LOLOS (return False),
        bukan ditolak.
        """
        if not self._gemini_image_ready:
            return False
        try:
            response = self._gemini_client.models.generate_content(
                model=self.video_config.get("vision_check_model", "gemini-3.5-flash-lite"),
                contents=[
                    genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    self._visual_filter_question(),
                ],
                config=genai_types.GenerateContentConfig(
                    automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True)
                ),
            )
            answer = (response.text or "").strip().upper()
            return answer.startswith("YES")
        except Exception as e:
            logger.warning(f"Gagal cek filter konten (avoid_women_in_visuals) ({e}), gambar tetap dipakai (fail-open).")
            return False

    def _image_path_violates_visual_filter(self, image_path):
        """Wrapper _image_bytes_violates_visual_filter() untuk file gambar di disk."""
        try:
            with Image.open(image_path) as img:
                thumb = img.convert("RGB")
                thumb.thumbnail((512, 512))
                buf = BytesIO()
                thumb.save(buf, format="JPEG", quality=80)
                return self._image_bytes_violates_visual_filter(buf.getvalue())
        except Exception as e:
            logger.warning(f"Gagal buka gambar untuk filter konten ({image_path}): {e}, gambar tetap dipakai (fail-open).")
            return False

    def _thumbnail_url_violates_visual_filter(self, thumbnail_url):
        """Wrapper _image_bytes_violates_visual_filter() untuk thumbnail Pexels video."""
        try:
            r = requests.get(thumbnail_url, timeout=10)
            r.raise_for_status()
            return self._image_bytes_violates_visual_filter(r.content)
        except Exception as e:
            logger.warning(f"Gagal ambil thumbnail Pexels untuk filter konten ({e}), video tetap dipakai (fail-open).")
            return False

    def download_stock_photo(self, visual_prompt):
        """Cari FOTO dari Pexels Photos API, orientasi portrait."""
        api_key = os.environ.get("PEXELS_API_KEY")
        if not api_key:
            return None

        headers = {"Authorization": api_key.strip()}
        clean_prompt = visual_prompt.replace("'", "").replace('"', "")

        try:
            url = f"https://api.pexels.com/v1/search?query={clean_prompt}&per_page=15&orientation=portrait&size=large"
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                logger.warning(f"Pexels Photos Response Error [{response.status_code}]: {response.text}")
                return None

            photos = response.json().get('photos', [])
            logger.info(f"Pexels photo search '{clean_prompt}': ditemukan {len(photos)} foto.")
            if not photos:
                return None

            avoid_filter = self._visual_filter_active()
            candidates = list(photos)
            random.shuffle(candidates)
            max_candidates = self.video_config.get("visual_filter_max_candidates", 2) if avoid_filter else 1

            for photo_choice in candidates[:max_candidates]:
                img_url = (photo_choice.get('src', {}).get('large2x')
                           or photo_choice.get('src', {}).get('portrait')
                           or photo_choice.get('src', {}).get('original'))
                if not img_url:
                    continue

                img_path = self.assets_dir / "footage" / f"photo_{random.randint(1000, 9999)}.jpg"
                start_time = time.monotonic()
                r = requests.get(img_url, stream=True, timeout=(10, 20))
                r.raise_for_status()
                bytes_written = 0
                with open(img_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            bytes_written += len(chunk)
                elapsed = time.monotonic() - start_time

                if bytes_written == 0:
                    logger.warning("Download foto Pexels menghasilkan file kosong. Dibuang.")
                    img_path.unlink(missing_ok=True)
                    continue

                try:
                    with Image.open(img_path) as test_img:
                        test_img.verify()
                except Exception as e:
                    logger.warning(f"Foto Pexels korup/tidak bisa dibuka ({e}). Dibuang.")
                    img_path.unlink(missing_ok=True)
                    continue

                if avoid_filter and self._image_path_violates_visual_filter(img_path):
                    logger.info(f"Foto Pexels {img_path} melanggar filter visual -- ditolak, coba kandidat lain.")
                    img_path.unlink(missing_ok=True)
                    continue

                logger.info(f"Download foto Pexels BERHASIL: {img_path} ({bytes_written/1024:.0f} KB dalam {elapsed:.1f}s).")
                return img_path

            if avoid_filter:
                logger.warning(f"Semua {max_candidates} kandidat foto Pexels untuk '{clean_prompt}' ditolak filter visual, coba fallback lain.")
            return None

        except Exception as e:
            logger.warning(f"Gagal download foto dari Pexels: {e}")
            return None

    def _apply_ken_burns(self, image_clip, duration, zoom_end=1.12):
        """Efek zoom perlahan (Ken Burns)."""
        try:
            return image_clip.resized(lambda t: 1.0 + (zoom_end - 1.0) * (t / max(duration, 0.01)))
        except Exception as e:
            logger.warning(f"Gagal apply Ken Burns effect ({e}), pakai gambar statis biasa.")
            return image_clip

    def generate_scene_image(self, visual_prompt, language="id"):
        """Generate 1 gambar AI (Gemini image model) sesuai visual_prompt scene."""
        if not self._gemini_image_ready:
            logger.warning(
                "generate_scene_image() dipanggil tapi Gemini image client belum siap "
                "(GEMINI_API_KEY tidak ada / library belum terinstall) -- fallback ke warna solid."
            )
            return None

        if getattr(self, "_gemini_image_quota_exhausted", False):
            logger.info("Skip percobaan gambar AI -- kuota Gemini image sudah diketahui habis di run ini.")
            return None

        try:
            logger.info(f"Meminta Gemini generate gambar untuk prompt: '{visual_prompt[:80]}'...")
            avoid_human_figures = self._avoid_human_figures_active()
            avoid_women = self._avoid_women_filter_active()
            if avoid_human_figures:
                figure_clause = (
                    " Do not depict any human figures, faces, or silhouettes at all -- "
                    "use nature, architecture, calligraphy, geometric patterns, or objects only."
                )
            elif avoid_women:
                figure_clause = (
                    " Do not depict any women or girls in the image -- if showing people, "
                    "only show men, or prefer nature/objects/abstract visuals with no visible people."
                )
            else:
                figure_clause = ""
            prompt = (
                f"Cinematic vertical background image for a motivational short video, "
                f"high quality, no text, no watermark, no people's faces close-up: "
                f"{visual_prompt}.{figure_clause}"
            )
            response = self._gemini_client.models.generate_content(
                model=self.gemini_image_model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=genai_types.ImageConfig(aspect_ratio="9:16"),
                    automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            for part in response.candidates[0].content.parts:
                if part.inline_data is not None:
                    img = Image.open(BytesIO(part.inline_data.data)).convert("RGB")
                    img = self._fit_image_to_canvas(img, 1080, 1920)
                    img_path = self.ai_images_dir / f"scene_{random.randint(1000, 9999)}.jpg"
                    img.save(img_path, quality=90)

                    if self._visual_filter_active() and self._image_path_violates_visual_filter(img_path):
                        logger.info(f"Gambar AI {img_path} melanggar filter visual -- ditolak, fallback ke Pexels.")
                        img_path.unlink(missing_ok=True)
                        return None

                    logger.info(f"Gambar AI berhasil di-generate untuk scene: {img_path}")
                    return img_path

            logger.warning("Respons Gemini image tidak mengandung data gambar.")
            return None

        except Exception as e:
            error_str = str(e)
            if "limit: 0" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                self._gemini_image_quota_exhausted = True
                logger.warning(
                    f"Gagal generate gambar AI ({e}). Kuota image-gen tampaknya habis/tidak "
                    f"tersedia di tier API key ini -- skip percobaan gambar AI untuk sisa scene "
                    f"di video ini, fallback ke Pexels/warna solid."
                )
            else:
                logger.warning(f"Gagal generate gambar AI untuk scene ({e}), lanjut ke fallback warna solid.")
            return None

    def _fit_image_to_canvas(self, img, target_w, target_h):
        """Resize+center-crop gambar supaya PAS mengisi kanvas target."""
        src_ratio = img.width / img.height
        target_ratio = target_w / target_h
        if src_ratio > target_ratio:
            new_h = target_h
            new_w = int(new_h * src_ratio)
        else:
            new_w = target_w
            new_h = int(new_w / src_ratio)
        img = img.resize((new_w, new_h))
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        return img.crop((left, top, left + target_w, top + target_h))

    _GEMINI_TTS_STYLE_BY_BEAT = {
        "hook": "Say in an attention-grabbing, slightly urgent tone:",
        "story": "Say in a warm, storytelling tone, like sharing a personal memory:",
        "emotion": "Say slowly and with genuine heartfelt emotion:",
        "lesson": "Say clearly and thoughtfully, like explaining something important:",
        "punchline": "Say with quiet conviction, like the closing line of a speech:",
    }

    async def _generate_scene_audio_gemini_tts(self, text, voice, story_beat=None):
        """Generate audio narasi 1 scene lewat Gemini TTS NATIVE."""
        if not self._gemini_image_ready:
            raise RuntimeError("Gemini client tidak siap (GEMINI_API_KEY tidak diset/gagal konfigurasi).")

        style_instruction = self._GEMINI_TTS_STYLE_BY_BEAT.get(story_beat, "Say in a natural, warm, conversational tone:")

        if self.video_config.get("tts", {}).get("gemini_tts_arabic_pronunciation_hint", True):
            style_instruction += (
                " Pronounce any Arabic religious terms (such as 'Allah') with correct "
                "Arabic phonetic emphasis, not an anglicized/flattened pronunciation."
            )

        full_text = f"{style_instruction} {text}"
        model = self.video_config.get("gemini_tts_model", "gemini-2.5-flash-preview-tts")

        def _call_gemini_tts():
            return self._gemini_client.models.generate_content(
                model=model,
                contents=full_text,
                config=genai_types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=genai_types.SpeechConfig(
                        voice_config=genai_types.VoiceConfig(
                            prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(voice_name=voice)
                        )
                    ),
                    automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )

        response = await asyncio.to_thread(_call_gemini_tts)

        pcm_data = response.candidates[0].content.parts[0].inline_data.data
        if not pcm_data:
            raise RuntimeError("Respons Gemini TTS tidak mengandung data audio.")

        seg_path = self.temp_dir / f"voiceover_gemini_{random.randint(10000,99999)}.wav"
        with wave.open(str(seg_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(pcm_data)

        return seg_path

    def _apply_pronunciation_fixes(self, text, language):
        """Ganti kata-kata tertentu dengan ejaan alternatif sebelum dikirim ke TTS."""
        fixes = self.video_config.get("tts", {}).get("pronunciation_fixes", {}).get(language, {})
        if not fixes:
            return text
        result = text
        for original, replacement in fixes.items():
            result = re.sub(rf"\b{re.escape(original)}\b", replacement, result, flags=re.IGNORECASE)
        return result

    async def generate_voiceover(self, script_data, language="id"):
        """Menghasilkan voiceover PER SCENE."""
        voices_config = self.video_config.get("tts", {}).get("voices", {})
        default_voices = {
            "id": ["id-ID-ArdiNeural", "id-ID-GadisNeural"],
            "en": ["en-US-ChristopherNeural", "en-US-GuyNeural", "en-US-EricNeural",
                   "en-US-RogerNeural", "en-US-JennyNeural", "en-US-AriaNeural",
                   "en-US-AndrewNeural", "en-US-EmmaNeural",
                   "en-US-EmmaMultilingualNeural", "en-US-AvaMultilingualNeural",
                   "en-GB-RyanNeural"],
        }
        voice_list = voices_config.get(language)
        if isinstance(voice_list, str):
            voice_list = [voice_list]
        if not voice_list:
            voice_list = default_voices.get(language, default_voices["id"])

        candidates = voice_list.copy()
        random.shuffle(candidates)
        voice = candidates[0]

        tts_config = self.video_config.get("tts", {})
        base_rate_str = tts_config.get("rate", "+0%")
        base_pitch_str = tts_config.get("pitch", "+0Hz")
        rate_jitter_pct = tts_config.get("rate_jitter_percent", 4)
        pitch_jitter_hz = tts_config.get("pitch_jitter_hz", 3)

        def _parse_numeric(value_str, suffix):
            try:
                return float(value_str.replace(suffix, "").replace("+", ""))
            except (ValueError, AttributeError):
                return 0.0

        base_rate_num = _parse_numeric(base_rate_str, "%")
        base_pitch_num = _parse_numeric(base_pitch_str, "Hz")

        scenes = script_data.get("scenes", [])
        tts_engine = self.video_config.get("tts_engine", "edge_tts")
        logger.info(f"Menghasilkan voiceover ({language.upper()}) per-scene ({len(scenes)} segmen) -- engine: {tts_engine}, suara edge-tts cadangan: {voice}")

        gemini_voices_config = self.video_config.get("tts", {}).get("gemini_voices", {})
        gemini_voice_list = gemini_voices_config.get(language) or ["Kore", "Puck", "Charon", "Fenrir", "Aoede"]
        gemini_voice = random.choice(gemini_voice_list)

        segment_paths = []
        for i, scene in enumerate(scenes):
            text = scene.get("narration", "").strip()
            if not text:
                continue
            
            text = text.replace("Allah", "Awlloh").replace("Rasulullah", "Rosululloh")
            text = text.replace("allah", "awlloh").replace("rasulullah", "rosululloh")
            
            seg_path = self.temp_dir / f"voiceover_{language}_seg{i}.mp3"
            tts_text = self._apply_pronunciation_fixes(text, language)

            if tts_engine == "gemini":
                try:
                    gemini_seg_path = await self._generate_scene_audio_gemini_tts(
                        tts_text, gemini_voice, story_beat=scene.get("story_beat")
                    )
                    segment_paths.append(gemini_seg_path)
                    continue
                except Exception as e:
                    logger.warning(f"Scene {i}: Gemini TTS gagal ({e}), fallback ke edge-tts untuk scene ini.")

            scene_rate_num = base_rate_num + random.uniform(-rate_jitter_pct, rate_jitter_pct)
            scene_pitch_num = base_pitch_num + random.uniform(-pitch_jitter_hz, pitch_jitter_hz)
            scene_rate = f"{'+' if scene_rate_num >= 0 else ''}{scene_rate_num:.0f}%"
            scene_pitch = f"{'+' if scene_pitch_num >= 0 else ''}{scene_pitch_num:.0f}Hz"

            last_error = None
            for attempt_voice in [voice] + [v for v in candidates if v != voice]:
                try:
                    communicate = edge_tts.Communicate(
                        tts_text, attempt_voice, rate=scene_rate, pitch=scene_pitch, volume="+0%"
                    )
                    await communicate.save(str(seg_path))
                    if attempt_voice != voice:
                        logger.warning(f"Suara '{voice}' gagal dipakai, beralih ke '{attempt_voice}' untuk sisa video.")
                        voice = attempt_voice
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    continue
            if last_error:
                raise RuntimeError(f"Semua kandidat suara TTS gagal untuk scene {i}: {last_error}")

            segment_paths.append(seg_path)

        return segment_paths

    def _is_footage_playable(self, clip):
        """Validasi apakah file footage benar-benar bisa dibaca."""
        try:
            test_t = max(0, clip.duration - 0.3)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                clip.get_frame(test_t)
                for w in caught:
                    msg = str(w.message)
                    if "bytes wanted" in msg or "last valid frame" in msg:
                        return False
            return True
        except Exception:
            return False

    def download_stock_footage(self, visual_prompt):
        """Mengunduh video stock dari Pexels API."""
        api_key = os.environ.get("PEXELS_API_KEY")
        if not api_key:
            logger.warning(
                "PEXELS_API_KEY tidak terbaca dari environment. "
                "Video akan selalu fallback ke background warna polos (ColorClip)."
            )
            return None

        headers = {"Authorization": api_key.strip()}
        random_page = random.randint(1, 8)
        clean_prompt = visual_prompt.replace("'", "").replace('"', "")
        if self._avoid_human_figures_active():
            fallback_terms = ["nature landscape", "geometric pattern", "calligraphy art"]
        else:
            fallback_terms = ["motivation", "nature", "sunrise"]
        prompts = [clean_prompt] + fallback_terms

        for p in prompts:
            url = f"https://api.pexels.com/videos/search?query={p}&per_page=10&page={random_page}"
            try:
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    videos = data.get('videos', [])
                    logger.info(f"Pexels search '{p}': ditemukan {len(videos)} video.")
                    if videos:
                        avoid_filter = self._visual_filter_active()
                        video_pool = list(videos)
                        random.shuffle(video_pool)
                        max_candidates = self.video_config.get("visual_filter_max_candidates", 2) if avoid_filter else 1

                        chosen_file = None
                        selected_url = None
                        mp4_files = []
                        for video_choice in video_pool[:max_candidates]:
                            candidate_mp4_files = [
                                f for f in video_choice.get('video_files', [])
                                if f.get('file_type') == 'video/mp4' and f.get('link')
                            ]
                            if not candidate_mp4_files:
                                continue

                            thumbnail_url = video_choice.get('image')
                            if avoid_filter and thumbnail_url and self._thumbnail_url_violates_visual_filter(thumbnail_url):
                                logger.info(f"Video Pexels (thumbnail: {thumbnail_url}) melanggar filter visual -- ditolak, coba kandidat lain.")
                                continue

                            mp4_files = candidate_mp4_files
                            break

                        if not mp4_files:
                            if avoid_filter:
                                logger.warning(f"Semua kandidat video Pexels untuk '{p}' ditolak filter visual/tidak ada mp4, coba prompt lain.")
                            continue

                        candidates = [f for f in mp4_files if (f.get('width') or 0) >= 480]
                        if not candidates:
                            candidates = mp4_files
                        candidates.sort(key=lambda f: f.get('width') or 999999)
                        chosen_file = candidates[0]
                        selected_url = chosen_file.get('link')
                        logger.info(
                            f"Pilih varian {chosen_file.get('width')}x{chosen_file.get('height')} "
                            f"(dari {len(mp4_files)} varian tersedia) -- prioritas file kecil."
                        )

                        filename = f"footage_{random.randint(1000, 9999)}.mp4"
                        filepath = self.assets_dir / "footage" / filename

                        downloaded_path = self._download_video_file(selected_url, filepath)
                        if downloaded_path:
                            logger.info(f"Download footage BERHASIL: {downloaded_path}")
                            return downloaded_path
                        logger.warning(f"Download footage gagal/tidak lengkap untuk prompt '{p}', coba prompt lain.")
                        continue
                else:
                    logger.warning(f"Pexels Response Error [{response.status_code}]: {response.text}")
            except Exception as e:
                logger.warning(f"Gagal mendownload dari Pexels: {e}")
                continue
        return None

    def _download_video_file(self, url, filepath, min_completeness=0.98, connect_timeout=10, read_timeout=30):
        """Download file video dengan verifikasi kelengkapan."""
        try:
            start_time = time.monotonic()
            r = requests.get(url, stream=True, timeout=(connect_timeout, read_timeout))
            r.raise_for_status()
            expected_size = int(r.headers.get('content-length', 0))

            bytes_written = 0
            with open(filepath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        bytes_written += len(chunk)

            elapsed = time.monotonic() - start_time

            if expected_size and bytes_written < expected_size * min_completeness:
                logger.warning(
                    f"Download footage terpotong: {bytes_written}/{expected_size} bytes "
                    f"({bytes_written / expected_size:.0%}) dalam {elapsed:.1f}s. File dibuang."
                )
                filepath.unlink(missing_ok=True)
                return None

            if bytes_written == 0:
                logger.warning("Download footage menghasilkan file kosong (0 bytes). File dibuang.")
                filepath.unlink(missing_ok=True)
                return None

            logger.info(f"Download footage selesai: {bytes_written/1024:.0f} KB dalam {elapsed:.1f}s.")
            return filepath

        except Exception as e:
            logger.warning(f"Gagal download footage dari {url}: {e}")
            try:
                filepath.unlink(missing_ok=True)
            except Exception:
                pass
            return None

    def _cleanup_old_outputs(self, keep_days=3):
        """Hapus video output lama (>keep_days) dari assets/output."""
        try:
            cutoff = datetime.now().timestamp() - (keep_days * 86400)
            removed = 0
            for pattern in ("video_*.mp4", "thumb_*.jpg"):
                for f in self.output_dir.glob(pattern):
                    if f.stat().st_mtime < cutoff:
                        f.unlink(missing_ok=True)
                        removed += 1
            if removed:
                logger.info(f"Cleanup: {removed} file output lama (>{keep_days} hari) dihapus.")
        except Exception as e:
            logger.warning(f"Gagal cleanup video output lama: {e}")

    def _try_load_ai_image_clip(self, visual_prompt, language, scene_duration, downloaded_footage_paths):
        """Coba generate & load gambar AI sebagai ImageClip."""
        ai_image_path = self.generate_scene_image(visual_prompt, language)
        if not ai_image_path:
            return None
        try:
            clip = ImageClip(str(ai_image_path)).with_duration(scene_duration)
            clip = self._apply_ken_burns(clip, scene_duration)
            downloaded_footage_paths.append(ai_image_path)
            return clip
        except Exception as e:
            logger.warning(f"Gagal load gambar AI sebagai clip ({e}).")
            return None

    def _log_available_memory(self, context=""):
        """Log sisa RAM container."""
        try:
            limit_mb = None
            usage_mb = None

            cgroup_v2_max = Path("/sys/fs/cgroup/memory.max")
            cgroup_v2_current = Path("/sys/fs/cgroup/memory.current")
            if cgroup_v2_max.exists():
                raw = cgroup_v2_max.read_text().strip()
                if raw != "max":
                    limit_mb = int(raw) / (1024 * 1024)
                if cgroup_v2_current.exists():
                    usage_mb = int(cgroup_v2_current.read_text().strip()) / (1024 * 1024)
            else:
                cgroup_v1_limit = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
                cgroup_v1_usage = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")
                if cgroup_v1_limit.exists():
                    raw_limit = int(cgroup_v1_limit.read_text().strip())
                    if raw_limit < (1 << 62):
                        limit_mb = raw_limit / (1024 * 1024)
                    if cgroup_v1_usage.exists():
                        usage_mb = int(cgroup_v1_usage.read_text().strip()) / (1024 * 1024)

            if limit_mb is not None and usage_mb is not None:
                available_mb = limit_mb - usage_mb
                pct_free = available_mb / limit_mb * 100
                logger.info(
                    f"Memori container {context}: {available_mb:.0f} MB tersedia dari "
                    f"{limit_mb:.0f} MB limit container ({pct_free:.0f}% bebas)."
                )
                if available_mb < 200 or pct_free < 15:
                    logger.warning(
                        f"RAM container tersisa sangat rendah ({available_mb:.0f} MB / "
                        f"{pct_free:.0f}%)."
                    )
            else:
                with open("/proc/meminfo") as f:
                    meminfo = {}
                    for line in f:
                        key, val = line.split(":", 1)
                        meminfo[key.strip()] = val.strip()
                available_mb = int(meminfo.get("MemAvailable", "0 kB").split()[0]) / 1024
                total_mb = int(meminfo.get("MemTotal", "0 kB").split()[0]) / 1024
                logger.info(
                    f"Memori (fallback /proc/meminfo) {context}: "
                    f"{available_mb:.0f} MB / {total_mb:.0f} MB."
                )
        except Exception as e:
            logger.warning(f"Gagal membaca info memori ({e}).")

    _BACKGROUND_AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".ogg", ".flac")
    _MAX_BACKGROUND_AUDIO_DURATION = 180

    def _background_audio_dir(self):
        return self.assets_dir / "music"

    def _list_background_audio_files(self):
        music_dir = self._background_audio_dir()
        if not music_dir.exists():
            return []
        return sorted([
            p for p in music_dir.iterdir()
            if p.is_file() and p.suffix.lower() in self._BACKGROUND_AUDIO_EXTENSIONS
        ])

    def _load_nasheed_track_history(self):
        history_file = Path("reports") / "nasheed_track_history.json"
        if not history_file.exists():
            return []
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save_nasheed_track_history(self, filename):
        history_file = Path("reports") / "nasheed_track_history.json"
        os.makedirs("reports", exist_ok=True)
        history = self._load_nasheed_track_history()
        history.append(filename)
        history = history[-20:]
        try:
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.warning(f"Gagal simpan riwayat track nasheed: {e}")

    def _pick_background_audio_path(self):
        files = self._list_background_audio_files()
        if not files:
            return None
        history = self._load_nasheed_track_history()
        filenames = [f.name for f in files]
        recent = history[-len(filenames):] if history else []
        from collections import Counter
        counts = Counter(recent)
        unused = [f for f in files if counts[f.name] == 0]
        chosen = random.choice(unused) if unused else random.choice(files)
        return chosen

    def _get_background_audio_slice(self, bg_track_full, cursor, duration):
        available = bg_track_full.duration
        start = min(cursor, max(0, available - 0.1))
        end = min(cursor + duration, available)
        if end <= start:
            start = max(0, available - duration)
            end = available
        return bg_track_full.subclipped(start, end)

    def _logo_position_xy(self, position, logo_w, logo_h, margin, canvas_w=1080, canvas_h=1920):
        if position == "top-left":
            return (margin, margin)
        elif position == "top-right":
            return (canvas_w - logo_w - margin, margin)
        elif position == "bottom-left":
            return (margin, canvas_h - logo_h - margin)
        else:
            return (canvas_w - logo_w - margin, canvas_h - logo_h - margin)

    def _load_logo_watermark_clip(self):
        cfg = self.video_config.get("logo_watermark", {})
        if not cfg.get("enabled", False):
            return None

        logo_path = cfg.get("path", "assets/branding/logo.png")
        if not os.path.exists(logo_path):
            logger.warning(
                f"logo_watermark.enabled=true tapi file '{logo_path}' tidak ditemukan."
            )
            return None

        try:
            width_fraction = cfg.get("width_fraction", 0.16)
            target_width = max(20, int(1080 * width_fraction))

            logo_clip = ImageClip(logo_path).with_effects([Resize(width=target_width)])

            opacity = cfg.get("opacity", 1.0)
            if opacity < 1.0:
                logo_clip = logo_clip.with_opacity(opacity)

            position = cfg.get("position", "top-right")
            margin = cfg.get("margin", 30)
            xy = self._logo_position_xy(position, logo_clip.w, logo_clip.h, margin)
            logo_clip = logo_clip.with_position(xy)

            logger.info(f"Logo watermark dimuat: {logo_path} ({logo_clip.w}x{logo_clip.h}px @ {position}).")
            return logo_clip
        except Exception as e:
            logger.warning(f"Gagal load logo watermark ({e}), video dibuat TANPA logo (fail-open).")
            return None

    def _loudness_normalize_track(self, input_path):
        output_path = self.temp_dir / f"bg_normalized_{Path(input_path).stem}.wav"
        try:
            cmd = [
                "ffmpeg", "-y", "-i", str(input_path),
                "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                "-ar", "44100",
                str(output_path),
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode != 0 or not output_path.exists():
                logger.warning(
                    f"ffmpeg loudnorm gagal untuk {input_path}, pakai file asli."
                )
                return input_path
            return output_path
        except Exception as e:
            logger.warning(f"Gagal jalankan ffmpeg loudnorm untuk {input_path} ({e}), pakai file asli.")
            return input_path

    def _estimate_reading_duration(self, text, language="id"):
        tts_cfg = self.video_config.get("tts", {})
        reading_wpm = tts_cfg.get("text_only_reading_wpm", 170)
        padding_sec = tts_cfg.get("text_only_reading_padding_sec", 0.7)
        min_duration = tts_cfg.get("text_only_min_scene_duration", 2.2)

        word_count = len(text.split()) if text else 0
        duration = (word_count / max(reading_wpm, 1)) * 60 + padding_sec
        return max(min_duration, duration)

    def create_video(self, script_data, voiceover_segments=None, language="id", variant="voice"):
        logger.info(f"Merakit video final ({language.upper()}, variant={variant})...")
        self._cleanup_old_outputs()

        scenes = script_data.get("scenes", [])
        if variant == "voice" and not voiceover_segments:
            raise ValueError("voiceover_segments kosong -- tidak ada audio untuk dirakit (variant='voice').")
        if variant == "text_only" and not scenes:
            raise ValueError("scenes kosong -- tidak ada apapun untuk dirakit (variant='text_only').")

        fps = self.video_config.get("fps", 30)
        downloaded_footage_paths = []
        scene_video_paths = []

        if variant == "text_only":
            background_audio_enabled = True
            background_audio_volume = self.video_config.get("background_audio_volume_text_only", 0.7)
        else:
            background_audio_enabled = self.video_config.get("background_audio_enabled", False)
            background_audio_volume = self.video_config.get("background_audio_volume", 0.18)
        bg_track_full = None
        bg_track_path = None
        bg_cursor = 0.0
        total_scene_count = len(scenes) if variant == "text_only" else min(len(voiceover_segments), len(scenes))

        if background_audio_enabled:
            bg_track_path = self._pick_background_audio_path()
            if bg_track_path is None:
                logger.warning("background_audio_enabled=true tapi tidak ada file audio di assets/music/.")
            else:
                try:
                    normalized_path = self._loudness_normalize_track(bg_track_path)
                    bg_track_full = AudioFileClip(str(normalized_path))
                    if bg_track_full.duration < self._MAX_BACKGROUND_AUDIO_DURATION:
                        bg_track_full = bg_track_full.with_effects(
                            [afx.AudioLoop(duration=self._MAX_BACKGROUND_AUDIO_DURATION)]
                        )
                    logger.info(f"Musik latar dipakai: {bg_track_path.name}")
                except Exception as e:
                    logger.warning(f"Gagal load track musik latar {bg_track_path} ({e}).")
                    bg_track_full = None

        self._log_available_memory("sebelum mulai render per-scene")

        logo_base_clip = self._load_logo_watermark_clip()

        loop_range = range(len(scenes)) if variant == "text_only" else range(min(len(voiceover_segments), len(scenes)))

        for idx in loop_range:
            scene = scenes[idx]

            if variant == "voice":
                seg_path = voiceover_segments[idx]
                seg_audio = AudioFileClip(str(seg_path))
                scene_duration = seg_audio.duration
                if scene_duration <= 0:
                    seg_audio.close()
                    continue
            else:
                seg_audio = None
                narration_text_for_duration = scene.get("narration", "").strip()
                scene_duration = self._estimate_reading_duration(narration_text_for_duration, language)

            ai_image_ratio = self.video_config.get("ai_image_ratio", 0.35)
            force_ai_image = random.random() < ai_image_ratio

            visual_prompt = scene.get("visual_prompt", "motivation")
            clip_loaded = False
            raw_clip_to_close = None
            bg_clip = None
            is_static_image = False
            ai_image_already_tried = False

            if force_ai_image:
                logger.info(f"Scene {idx}: coba gambar AI (Gemini) duluan...")
                bg_clip = self._try_load_ai_image_clip(visual_prompt, language, scene_duration, downloaded_footage_paths)
                ai_image_already_tried = True
                if bg_clip is not None:
                    clip_loaded = True
                    is_static_image = True
                else:
                    logger.warning(f"Scene {idx}: gambar AI gagal, fallback ke Pexels.")

            if not clip_loaded:
                photo_path = self.download_stock_photo(visual_prompt)
                if photo_path:
                    try:
                        img_clip = ImageClip(str(photo_path))
                        resized = img_clip.with_effects([Resize(height=1920)])
                        cropped = resized.with_effects([
                            Crop(x_center=resized.w // 2, y_center=resized.h // 2, width=1080, height=1920)
                        ])
                        if cropped.size == (1080, 1920):
                            bg_clip = cropped.with_duration(scene_duration)
                            bg_clip = self._apply_ken_burns(bg_clip, scene_duration)
                            clip_loaded = True
                            is_static_image = True
                            downloaded_footage_paths.append(photo_path)
                            logger.info(f"Foto Pexels valid & terpakai untuk scene {idx}: {photo_path}")
                        else:
                            logger.warning(f"Foto Pexels {photo_path} hasil crop salah ukuran {cropped.size}.")
                    except Exception as e:
                        logger.warning(f"Gagal proses foto Pexels ({e}), coba video Pexels.")

            footage_path = None
            if not clip_loaded:
                try:
                    footage_path = self.download_stock_footage(visual_prompt)
                    if footage_path:
                        downloaded_footage_paths.append(footage_path)
                except Exception as e:
                    logger.warning(f"download_stock_footage gagal tak terduga ({e}).")
                    footage_path = None

            if not clip_loaded and footage_path and os.path.exists(footage_path):
                try:
                    raw_clip = VideoFileClip(str(footage_path))
                    raw_clip_to_close = raw_clip

                    if not self._is_footage_playable(raw_clip):
                        logger.warning(f"Footage {footage_path} TIDAK bisa dibaca penuh. Coba gambar AI.")
                    else:
                        if raw_clip.duration < scene_duration:
                            raw_clip = raw_clip.with_effects([vfx.Loop(duration=scene_duration)])

                        resized_clip = raw_clip.with_effects([Resize(height=1920)])
                        bg_clip = resized_clip.with_effects([
                            Crop(
                                x_center=resized_clip.w // 2,
                                y_center=resized_clip.h // 2,
                                width=1080, height=1920
                            )
                        ])
                        bg_clip = bg_clip.subclipped(0, scene_duration)

                        if bg_clip.size != (1080, 1920):
                            logger.warning(f"Footage {footage_path} hasil crop ukurannya salah {bg_clip.size}.")
                        else:
                            clip_loaded = True
                            logger.info(f"Footage Pexels valid & terpakai untuk scene {idx}: {footage_path}")
                except Exception as e:
                    logger.warning(f"Error memproses footage ({e}), coba gambar AI.")

            if not clip_loaded and not ai_image_already_tried:
                logger.info(f"Scene {idx}: mencoba generate gambar AI sebagai fallback terakhir...")
                bg_clip = self._try_load_ai_image_clip(visual_prompt, language, scene_duration, downloaded_footage_paths)
                if bg_clip is not None:
                    clip_loaded = True
                    is_static_image = True

            if not clip_loaded:
                bg_color = random.choice([(25, 30, 60), (40, 20, 50), (20, 40, 40)])
                bg_clip = ColorClip(size=(1080, 1920), color=bg_color, duration=scene_duration)

            try:
                sample_frame = bg_clip.get_frame(min(0.3, scene_duration / 2))
                if sample_frame.size == 0:
                    raise ValueError(f"Frame kosong (shape={sample_frame.shape}).")
                avg_brightness = sample_frame.mean()
                if avg_brightness < 60:
                    boost = min(4.0, 90 / max(avg_brightness, 1))
                    logger.warning(
                        f"Scene {idx}: visual terlalu gelap (brightness={avg_brightness:.0f}/255), "
                        f"menaikkan kecerahan otomatis {boost:.1f}x."
                    )
                    bg_clip = bg_clip.image_transform(
                        lambda frame: (frame.astype("float32") * boost).clip(0, 255).astype("uint8")
                    )
            except Exception as e:
                logger.warning(f"Scene {idx}: gagal cek/koreksi kecerahan ({e}).")

            scene_layers = [bg_clip]

            try:
                sub_config = self.video_config.get("subtitles", {})
                raw_text = scene.get("narration", "").strip()

                font_size = sub_config.get("font_size", 60)
                if variant == "text_only":
                    font_size = int(font_size * sub_config.get("text_only_font_size_multiplier", 1.35))
                text_max_width = sub_config.get("max_width", 900)
                font_path = resolve_font_path(sub_config.get("font"))

                wrapped_text = wrap_text_pixel_accurate(raw_text, font_path, font_size, text_max_width)

                text_kwargs = dict(
                    text=wrapped_text,
                    font_size=font_size,
                    color=sub_config.get("font_color", "white"),
                    stroke_color=sub_config.get("stroke_color", "black"),
                    stroke_width=sub_config.get("stroke_width", 3),
                    method='label',
                    text_align='center',
                    margin=(20, 20),
                )
                if font_path:
                    text_kwargs["font"] = font_path

                txt_clip = TextClip(**text_kwargs)

                video_height = 1920
                vertical_center_fraction = sub_config.get("vertical_center_fraction", 0.55)
                safe_top_margin = sub_config.get("safe_top_margin", 220)
                safe_bottom_margin = sub_config.get("safe_bottom_margin", 380)

                target_center_y = video_height * vertical_center_fraction
                y_pos = target_center_y - (txt_clip.h / 2)
                y_pos = max(safe_top_margin, min(y_pos, video_height - safe_bottom_margin - txt_clip.h))
                y_pos = max(0, y_pos)

                txt_clip = txt_clip.with_duration(scene_duration).with_position(('center', y_pos))
                scene_layers.append(txt_clip)
            except Exception as e:
                logger.warning(f"Gagal membuat subtitle TextClip: {e}")

            if logo_base_clip is not None:
                scene_layers.append(logo_base_clip.with_duration(scene_duration))

            scene_composite = CompositeVideoClip(scene_layers, size=(1080, 1920)).with_duration(scene_duration)

            if variant == "text_only":
                if bg_track_full is not None:
                    try:
                        bg_slice = self._get_background_audio_slice(bg_track_full, bg_cursor, scene_duration)
                        bg_slice = bg_slice.with_effects([afx.MultiplyVolume(background_audio_volume)])
                        if idx == 0:
                            bg_slice = bg_slice.with_effects([afx.AudioFadeIn(1.0)])
                        if idx == total_scene_count - 1:
                            bg_slice = bg_slice.with_effects([afx.AudioFadeOut(1.5)])
                        scene_composite = scene_composite.with_audio(bg_slice)
                    except Exception as e:
                        logger.warning(f"Scene {idx}: gagal mixing musik latar.")
                        silent = AudioClip(lambda t: [0, 0], duration=scene_duration, fps=44100)
                        scene_composite = scene_composite.with_audio(silent)
                else:
                    silent = AudioClip(lambda t: [0, 0], duration=scene_duration, fps=44100)
                    scene_composite = scene_composite.with_audio(silent)
            elif bg_track_full is not None:
                try:
                    bg_slice = self._get_background_audio_slice(bg_track_full, bg_cursor, scene_duration)
                    bg_slice = bg_slice.with_effects([afx.MultiplyVolume(background_audio_volume)])
                    if idx == 0:
                        bg_slice = bg_slice.with_effects([afx.AudioFadeIn(1.0)])
                    if idx == total_scene_count - 1:
                        bg_slice = bg_slice.with_effects([afx.AudioFadeOut(1.5)])
                    final_audio = CompositeAudioClip([seg_audio, bg_slice])
                    scene_composite = scene_composite.with_audio(final_audio)
                except Exception as e:
                    logger.warning(f"Scene {idx}: gagal mixing musik latar ({e}).")
                    scene_composite = scene_composite.with_audio(seg_audio)
            else:
                scene_composite = scene_composite.with_audio(seg_audio)
            bg_cursor += scene_duration

            scene_output_path = self.temp_dir / f"scene_{language}_{idx}.mp4"
            try:
                scene_composite.write_videofile(
                    str(scene_output_path),
                    fps=fps,
                    codec="libx264",
                    audio_codec="aac",
                    bitrate="4000k",
                    preset="ultrafast",
                    threads=1,
                    logger=None,
                )
                scene_video_paths.append(scene_output_path)
            except Exception as e:
                logger.error(f"Gagal me-render scene ke-{idx}: {e}")
                scene_composite.close()
                if raw_clip_to_close:
                    try: raw_clip_to_close.close()
                    except: pass
                if seg_audio is not None:
                    seg_audio.close()
                if bg_track_full is not None:
                    try: bg_track_full.close()
                    except: pass
                if logo_base_clip is not None:
                    try: logo_base_clip.close()
                    except: pass
                raise
            finally:
                scene_composite.close()
                if raw_clip_to_close:
                    try: raw_clip_to_close.close()
                    except: pass
                if seg_audio is not None:
                    seg_audio.close()

            self._log_available_memory(f"setelah render scene {idx}")

        if bg_track_full is not None:
            try: bg_track_full.close()
            except Exception: pass
            if bg_track_path is not None:
                self._save_nasheed_track_history(bg_track_path.name)

        if logo_base_clip is not None:
            try: logo_base_clip.close()
            except Exception: pass

        for fp in downloaded_footage_paths:
            try: Path(fp).unlink(missing_ok=True)
            except Exception: pass
        for p in (voiceover_segments or []):
            try: Path(p).unlink(missing_ok=True)
            except Exception: pass

        if not scene_video_paths:
            raise ValueError("Tidak ada scene yang berhasil di-render.")

        output_filename = f"video_{language}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        output_path = self.output_dir / output_filename
        concat_list_path = self.temp_dir / f"concat_list_{language}.txt"

        with open(concat_list_path, "w") as f:
            for p in scene_video_paths:
                f.write(f"file '{Path(p).resolve()}'\n")

        self._log_available_memory("sebelum ffmpeg concat scene")

        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list_path),
                 "-c", "copy", str(output_path)],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg concat exit code {result.returncode}: {result.stderr[-500:]}")
        except Exception as e:
            try:
                usage = shutil.disk_usage(self.output_dir)
                free_mb = usage.free / (1024 * 1024)
                logger.error(
                    f"Gagal menggabungkan scene jadi video final ({e}). Sisa disk: {free_mb:.0f} MB."
                )
            except Exception:
                logger.error(f"Gagal menggabungkan scene jadi video final ({e}).")
            if output_path.exists():
                try: output_path.unlink()
                except Exception: pass
            raise
        finally:
            for p in scene_video_paths:
                try: Path(p).unlink(missing_ok=True)
                except Exception: pass
            try: concat_list_path.unlink(missing_ok=True)
            except Exception: pass

        return output_path

if __name__ == "__main__":
    creator = VideoCreator()
    async def test():
        script = {"scenes": [{"narration": "Disiplin adalah kunci kesuksesan.", "visual_prompt": "workout", "duration_estimate": 4}]}
        vo = await creator.generate_voiceover(script, "id")
        out = creator.create_video(script, vo, "id")
        print(f"Video berhasil dibuat di: {out}")
    asyncio.run(test())
