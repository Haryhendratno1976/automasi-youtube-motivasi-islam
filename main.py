#!/usr/bin/env python3
"""
Main Entry Point v2.5 - AI Agent Multi-Language & Multi-Channel
"""

import os
import sys
import time
import argparse
import logging

# PALING ATAS, sebelum apapun lain: paksa timezone proses ke WIB (Asia/Jakarta).
# Sengaja TIDAK bergantung sepenuhnya pada env var Railway (rawan salah nama/
# case -- "tz" huruf kecil TIDAK dikenali sistem sebagai timezone, harus "TZ"
# huruf besar dengan value persis "Asia/Jakarta"). Ambil dari config kalau ada,
# fallback ke Asia/Jakarta. Ini menjamin jadwal posting selalu WIB terlepas
# dari apapun yang diset (atau lupa diset) di Railway Variables.
_configured_tz = os.environ.get("TZ") or "Asia/Jakarta"
os.environ["TZ"] = _configured_tz
try:
    time.tzset()  # Linux/Mac only -- Railway selalu Linux jadi aman
except AttributeError:
    pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("TokenBootstrap").info(
    f"Timezone proses di-set ke '{_configured_tz}' -> waktu lokal sekarang: "
    f"{time.strftime('%Y-%m-%d %H:%M:%S %Z')}"
)

# Logger sementara dipakai khusus untuk bootstrap token, sebelum
# logging.basicConfig() di bawah dipanggil (supaya pesan ini tetap
# muncul dengan format yang konsisten, bukan print() polos).
_bootstrap_logger = logging.getLogger("TokenBootstrap")


def bootstrap_youtube_token(language_code, env_var_name):
    """
    Sinkronkan file token_<lang>.json dengan environment variable Railway
    SETIAP KALI proses start -- BUKAN cuma sekali kalau file belum ada.

    PENTING (bug yang pernah terjadi): versi sebelumnya skip total kalau
    file sudah ada di disk, jadi kalau file itu pernah ke-generate salah
    (mis. token_en.json ternyata isinya credentials channel ID karena
    kesalahan copy saat testing), file yang salah itu TERUS dipakai
    selamanya walau env var Railway sudah diperbaiki -- video ID ke-upload
    ke channel EN meski YOUTUBE_TOKEN_ID/EN sudah diisi benar-benar beda.

    Sekarang: kalau env var ADA isinya, file SELALU ditimpa supaya konsisten
    dengan Railway Variable (sumber kebenaran satu-satunya). Kalau env var
    kosong tapi file sudah ada, file lama dipakai apa adanya (asumsi sudah
    benar, cuma tidak perlu di-refresh).
    """
    token_path = f"token_{language_code}.json"
    token_value = os.getenv(env_var_name)

    if not token_value:
        if os.path.exists(token_path):
            _bootstrap_logger.info(
                f"Env var '{env_var_name}' kosong, tapi {token_path} sudah ada di disk -- dipakai apa adanya."
            )
        else:
            _bootstrap_logger.error(
                f"Env var '{env_var_name}' TIDAK diset (atau kosong) di Railway, "
                f"dan {token_path} tidak ada di disk. Upload YouTube untuk bahasa "
                f"'{language_code}' akan GAGAL sampai ini diperbaiki. "
                f"Cek: Railway Dashboard -> Project -> Variables -> pastikan "
                f"'{env_var_name}' berisi isi lengkap file token OAuth JSON kamu."
            )
        return

    try:
        # Cek apakah isi file lama BEDA dari env var sekarang -- kalau beda,
        # ini kemungkinan besar kasus token stale seperti yang dijelaskan
        # di atas, log jelas supaya ketahuan bukan cuma diam-diam ke-timpa.
        if os.path.exists(token_path):
            with open(token_path) as f:
                existing = f.read()
            if existing.strip() != token_value.strip():
                _bootstrap_logger.warning(
                    f"{token_path} yang ada di disk BERBEDA dari env var '{env_var_name}' "
                    f"saat ini -- kemungkinan file lama/stale dari sesi sebelumnya. Menimpa "
                    f"dengan isi env var terbaru sekarang."
                )

        with open(token_path, "w") as f:
            f.write(token_value)
        _bootstrap_logger.info(f"✅ {token_path} disinkronkan dari Railway Variable '{env_var_name}'.")
    except Exception as e:
        _bootstrap_logger.error(f"Gagal menulis {token_path} ke disk: {e}")


# Import modul standar dulu sebelum bootstrap dijalankan.
import json
import asyncio
import re
import yaml
import schedule
import time
import subprocess
import random
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

# PENTING: load_dotenv() harus dipanggil SEBELUM bootstrap_youtube_token(),
# supaya kalau env var-nya didefinisikan lewat file .env (bukan lewat
# Railway dashboard var), nilainya sudah tersedia di os.environ saat
# bootstrap membacanya. Urutan terbalik sebelumnya bikin bootstrap selalu
# gagal membaca token dari .env lokal.
load_dotenv()

bootstrap_youtube_token("en", "YOUTUBE_TOKEN_EN")
bootstrap_youtube_token("id", "YOUTUBE_TOKEN_ID")

# Import modul internal
from modules.uploader import VideoUploader
from modules.research import ContentResearcher
from modules.script_generator import ScriptGenerator
from modules.creator import VideoCreator
from modules.poster import ContentPoster
from modules.analytics import AnalyticsManager
from modules.telegram_bot import TelegramNotifier

logger = logging.getLogger("AI-Agent-Main")


def _sanitize_for_telegram_markdown(text):
    """
    Hapus karakter spesial Markdown Telegram (*, _, `, [, ]) dari teks
    dinamis (judul video dari AI) sebelum disisipkan ke pesan notifikasi.
    Tanpa ini, kalau judul mengandung salah satu karakter tsb, Telegram
    akan gagal parse SELURUH pesan ("can't find end of the entity") dan
    notifikasi tidak terkirim sama sekali -- bukan cuma bagian judulnya.
    """
    if not text:
        return ""
    text = str(text).replace("_", " ")  # ganti spasi biar kata tidak nyambung
    return re.sub(r"[*`\[\]]", "", text)


class MotivationAgent:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f) or {}
        
        self.researcher = ContentResearcher(config_path)
        self.generator = ScriptGenerator(config_path)
        self.creator = VideoCreator(config_path)
        self.poster = ContentPoster(config_path)
        self.analytics = AnalyticsManager(config_path)
        self.notifier = TelegramNotifier(config_path)
        
        self.posted_data = {"id": [], "en": []}

    def get_unique_topic(self, language="id"):
        """Mengambil topik tren baru dan mencegah penggunaan topik duplikat."""
        history_file = "reports/topic_history.json"
        os.makedirs("reports", exist_ok=True)
        
        history = []
        if os.path.exists(history_file):
            try:
                with open(history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    history = data.get(language, [])
            except Exception as e:
                logger.warning(f"Gagal membaca riwayat topik: {e}")

        insights = self.researcher.fetch_trending_topics(language)
        
        # Filter topik yang belum pernah dipakai sebelumnya
        unused_topics = [i for i in insights if i.get("topic") not in history] if isinstance(insights, list) else []
        
        if unused_topics:
            selected = random.choice(unused_topics)
        elif insights and isinstance(insights, list):
            selected = random.choice(insights)
        else:
            selected = {"topic": f"Disiplin & Mental Steel {datetime.now().strftime('%d%m%H%M')}", "language": language}

        # Catat topik terpilih ke dalam file riwayat
        history.append(selected.get("topic"))
        try:
            existing_history = {}
            if os.path.exists(history_file):
                with open(history_file, "r", encoding="utf-8") as f:
                    existing_history = json.load(f)
            existing_history[language] = history[-30:] # Simpan 30 topik terakhir
            
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(existing_history, f, indent=2)
        except Exception as e:
            logger.warning(f"Gagal menyimpan riwayat topik: {e}")

        return selected

    async def _queue_for_review(self, script, video_path, language, description, native_tags, hashtags_str):
        """
        Simpan video + metadata ke antrian review manual (bukan langsung
        upload). Dipakai kalau publishing.require_manual_review aktif di
        config.yaml -- lihat catatan di run_pipeline soal kenapa ini penting
        untuk mitigasi risiko kebijakan YouTube.

        Return: True kalau notifikasi Telegram BENAR-BENAR terkirim, False
        kalau gagal (video tetap masuk antrian di reports/pending_review.json
        baik terkirim maupun tidak -- cuma status notifikasi Telegram-nya
        yang dilaporkan lewat return value ini).
        """
        import uuid
        os.makedirs("reports", exist_ok=True)
        pending_file = "reports/pending_review.json"
        pending = []
        if os.path.exists(pending_file):
            try:
                with open(pending_file, "r", encoding="utf-8") as f:
                    pending = json.load(f)
            except Exception as e:
                logger.warning(f"Gagal membaca {pending_file} lama: {e}")
                pending = []

        review_id = str(uuid.uuid4())[:8]
        pending.append({
            "id": review_id,
            "language": language,
            "video_path": str(video_path),
            "title": script.get("title", ""),
            "hook": script.get("hook", ""),
            "description": description,
            "tags": native_tags,
            "hashtags_str": hashtags_str,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending",
        })

        try:
            with open(pending_file, "w", encoding="utf-8") as f:
                json.dump(pending, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Gagal menyimpan antrian review ke {pending_file}: {e}")
            return False

        if not hasattr(self.notifier, 'send_review_request'):
            return False

        safe_title = _sanitize_for_telegram_markdown(script.get("title", ""))
        safe_hook = _sanitize_for_telegram_markdown(script.get("hook", ""))
        try:
            # PENTING: tangkap & pakai return value-nya -- send_review_request()
            # return False (BUKAN melempar exception) kalau token/chat_id
            # Telegram tidak ditemukan di environment proses ini. Sebelumnya
            # return value ini diabaikan sama sekali, jadi caller (run_pipeline)
            # selalu log "berhasil terkirim" walau sebenarnya GAGAL total --
            # video masuk antrian tanpa notifikasi apapun, tanpa ada yang tahu.
            sent = await self.notifier.send_review_request(review_id, safe_title, safe_hook, language, video_path=str(video_path))
            return bool(sent)
        except Exception as e:
            logger.error(f"Gagal kirim permintaan review ke Telegram: {e}")
            return False

    async def run_pipeline(self, language="id"):
        """Menjalankan pipeline pembuatan dan posting video untuk bahasa tertentu."""
        logger.info(f"--- MEMULAI PIPELINE ({language.upper()}) ---")
        try:
            # 1. Research Topik Unik
            top_insight = self.get_unique_topic(language)
            logger.info(f"Topik terpilih: {top_insight.get('topic')}")
            
            # 2. Script (via Gemini AI, lengkap dengan hook -- lihat script_generator.py)
            script = self.generator.generate_script(top_insight, language)

            # 2b. Judul & hashtag yang DIOPTIMASI KHUSUS untuk potensi viral,
            # berdasarkan hasil research (bukan cuma judul "seadanya" dari
            # generate_script yang fokusnya ke narasi). Override title &
            # suggested_hashtags dari script dengan hasil yang lebih tajam ini.
            title_data = self.generator.generate_title_and_hashtags(
                top_insight, language, hook=script.get("hook", "")
            )
            script["title"] = title_data["title"]
            script["suggested_hashtags"] = title_data["hashtags"]
            logger.info(f"Judul dipakai: '{script['title']}' | Hashtags: {script['suggested_hashtags']}")
            
            # 3. Creator: voiceover -> video
            vo_segments = await self.creator.generate_voiceover(script, language)
            video_path = self.creator.create_video(script, vo_segments, language)

            hashtags = script.get("suggested_hashtags") or ["#shorts", "#motivation"]
            hashtags_str = " ".join(hashtags)
            native_tags = [h.lstrip("#").replace(" ", "") for h in hashtags if h.lstrip("#")]
            description = f"{script.get('hook', '')}\n\n{hashtags_str}"

            # PENTING (mitigasi risiko kebijakan "inauthentic content"
            # YouTube 2026): riset kebijakan berulang kali menyebut "human
            # oversight"/"editorial judgment" sebagai faktor yang membedakan
            # channel yang selamat dari yang di-terminate. Kalau
            # publishing.require_manual_review AKTIF di config.yaml, video
            # TIDAK auto-upload -- masuk antrian review dulu, kamu approve/
            # tolak LANGSUNG lewat tombol di pesan Telegram (lihat
            # send_telegram_review_request), tidak perlu buka dashboard.
            require_review = self.config.get("publishing", {}).get("require_manual_review", False)
            if require_review:
                notif_sent = await self._queue_for_review(script, video_path, language, description, native_tags, hashtags_str)
                if notif_sent:
                    logger.info(f"Video ({language.upper()}) masuk antrian review manual, TIDAK auto-upload. Notifikasi + tombol approve/tolak dikirim ke Telegram.")
                else:
                    # JANGAN pura-pura sukses -- video TETAP masuk antrian
                    # (tersimpan di reports/pending_review.json), tapi tanpa
                    # notifikasi Telegram kamu tidak akan tahu ada video
                    # menunggu kecuali cek dashboard /review manual. Paling
                    # sering disebabkan proses ini (mis. service 'agent' yang
                    # terpisah dari 'web' di Railway) tidak punya env var
                    # TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID -- tiap service
                    # Railway punya environment SENDIRI-SENDIRI, tidak
                    # otomatis ikut service lain.
                    logger.warning(
                        f"Video ({language.upper()}) masuk antrian review (tersimpan di "
                        f"reports/pending_review.json), TAPI notifikasi Telegram GAGAL "
                        f"terkirim -- cek TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ada di "
                        f"environment proses INI (bukan cuma di service lain kalau kamu "
                        f"pakai lebih dari 1 service Railway). Buka dashboard /review "
                        f"untuk approve/tolak video ini secara manual sementara ini."
                    )
                logger.info(f"Pipeline {language.upper()} Selesai (menunggu review manual)!")
                return

            # Initialize Uploader
            uploader = VideoUploader()

            # 4. Posting YouTube (judul & hashtag hasil optimasi viral di atas)
            yt_id = uploader.upload_to_youtube(
                video_path=video_path,
                title=script.get("title", "Motivation Shorts"),
                description=description,
                language=language,
                tags=native_tags,
            )

            # Jika berhasil upload, simpan data ke database JSON untuk Web Dashboard
            if yt_id: 
                self.posted_data[language].append(yt_id)
                
                os.makedirs("reports", exist_ok=True)
                db_file = "reports/posted_videos.json"
                
                existing_data = {"id": [], "en": []}
                if os.path.exists(db_file):
                    try:
                        with open(db_file, "r", encoding="utf-8") as f:
                            data_loaded = json.load(f)
                            if isinstance(data_loaded, dict):
                                existing_data = data_loaded
                    except Exception as json_err:
                        logger.warning(f"Gagal membaca file JSON lama: {json_err}")

                if language not in existing_data:
                    existing_data[language] = []

                existing_data[language].append({
                    "video_id": yt_id,
                    "title": script.get("title", "Motivation"),
                    "posted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "url": f"https://youtube.com/shorts/{yt_id}",
                })
                
                with open(db_file, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, indent=2)    
        
            # 5. Posting Facebook Reels
            uploader.upload_to_facebook_reels(video_path, description, language)
            
            # Notifikasi Telegram -- judul video (dari script/AI) dibersihkan dulu
            # dari karakter spesial Markdown (*, _, `, [) sebelum disisipkan.
            # Sebelumnya kalau judul mengandung salah satu karakter itu,
            # Telegram gagal parse pesan ("can't find end of the entity")
            # dan notifikasi GAGAL terkirim total.
            if hasattr(self.notifier, 'send_message'):
                safe_title = _sanitize_for_telegram_markdown(script.get('title', ''))
                status_msg = f"✅ *Video {language.upper()} Posted!*\nTitle: {safe_title}"
                if yt_id:
                    status_msg += f"\nLink: https://youtube.com/shorts/{yt_id}"
                try:
                    await self.notifier.send_message(status_msg)
                except Exception as notif_err:
                    logger.error(f"Gagal mengirim notifikasi Telegram: {notif_err}")
            
        except Exception as e:
            logger.error(f"Error pada pipeline {language}: {e}")
            if hasattr(self.notifier, 'send_message'):
                # Pesan exception mentah (termasuk output ffmpeg, traceback,
                # dsb) SERING mengandung karakter markdown (*, _, `, [, ])
                # yang merusak parsing Telegram -- sama seperti judul video
                # sebelumnya, harus disanitasi dulu, bukan cuma judul.
                safe_error = _sanitize_for_telegram_markdown(str(e))[:500]
                try:
                    await self.notifier.send_message(f"❌ *Error {language.upper()}!* {safe_error}")
                except Exception as notif_err:
                    logger.error(f"Gagal mengirim notifikasi telegram error: {notif_err}")

        logger.info(f"Pipeline {language.upper()} Selesai!")

    def run_analytics(self):
        try:
            self.analytics.fetch_stats(self.posted_data)
        except Exception as e:
            logger.error(f"Error saat mengambil analytics: {e}")

def job(language):
    agent = MotivationAgent()
    asyncio.run(agent.run_pipeline(language))


# --- Penjadwalan ACAK dalam rentang jam, di-acak ULANG setiap hari ---
# Bukan jam tetap seperti sebelumnya -- tiap hari waktu postingnya beda
# (dalam rentang yang ditentukan), supaya polanya tidak terlalu mekanis/
# gampang ditebak.
_scheduled_random_jobs = {}  # language -> list of schedule.Job, utk di-cancel & diganti tiap hari


def _random_time_in_range(start_str, end_str):
    """Pilih 1 waktu acak (format 'HH:MM') di antara start_str dan end_str."""
    start_dt = datetime.strptime(start_str, "%H:%M")
    end_dt = datetime.strptime(end_str, "%H:%M")
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)  # jaga-jaga kalau range melewati tengah malam
    delta_seconds = int((end_dt - start_dt).total_seconds())
    random_offset = random.randint(0, max(delta_seconds, 0))
    result_dt = start_dt + timedelta(seconds=random_offset)
    return result_dt.strftime("%H:%M")


def _schedule_random_jobs_for_language(language, time_ranges):
    """
    Jadwalkan job untuk 1 bahasa dengan waktu ACAK di tiap rentang yang
    diberikan. Membatalkan job random hari sebelumnya dulu (kalau ada)
    supaya tidak menumpuk jadi dobel setiap kali dipanggil ulang.
    """
    for old_job in _scheduled_random_jobs.get(language, []):
        try:
            schedule.cancel_job(old_job)
        except Exception:
            pass

    new_jobs = []
    for start_str, end_str in time_ranges:
        picked_time = _random_time_in_range(start_str, end_str)
        j = schedule.every().day.at(picked_time).do(job, language=language)
        new_jobs.append(j)
        logger.info(f"Jadwal random {language.upper()} hari ini: {picked_time} (rentang {start_str}-{end_str})")

    _scheduled_random_jobs[language] = new_jobs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Agent Multi-Channel Video Pipeline")
    parser.add_argument(
        "--once", action="store_true",
        help="Jalankan pipeline SEKALI untuk 1 bahasa lalu keluar (skip scheduler) -- "
             "dipakai untuk trigger manual, mis. dari tombol 'Generate All' di Telegram."
    )
    parser.add_argument(
        "--language", choices=["id", "en"],
        help="Bahasa yang mau dijalankan (WAJIB kalau --once dipakai)."
    )
    args = parser.parse_args()

    if args.once:
        if not args.language:
            logger.error("--once butuh --language (id/en). Contoh: python main.py --once --language id")
            sys.exit(1)
        logger.info(f"Mode --once: jalankan pipeline {args.language.upper()} SEKALI lalu keluar (skip scheduler).")
        job(args.language)
        logger.info(f"Mode --once selesai untuk {args.language.upper()}.")
        sys.exit(0)

    logger.info("AI Agent Multi-Channel Aktif...")
    
    config_data = {}
    if os.path.exists("config.yaml"):
        with open("config.yaml", "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}

    schedule_config = config_data.get("schedule", {})

    # Default rentang jam kalau tidak ada di config.yaml:
    # ID: 19:30-20:00 dan 06:00-07:00 | EN: 06:30-07:30 dan 23:00-23:59
    default_id_ranges = [["16:00", "16:30"], ["05:00", "05:30"]]
    default_en_ranges = [["06:30", "07:00"], ["18:00", "18:30"]]

    id_ranges = [tuple(r) for r in schedule_config.get("id", {}).get("time_ranges", default_id_ranges)]
    en_ranges = [tuple(r) for r in schedule_config.get("en", {}).get("time_ranges", default_en_ranges)]

    # Jadwal hari ini (langsung saat proses start)
    _schedule_random_jobs_for_language("id", id_ranges)
    _schedule_random_jobs_for_language("en", en_ranges)

    # Acak ULANG tiap hari jam 00:05 (ID) & 00:06 (EN) -- supaya besok
    # waktu postingnya beda lagi dalam rentang yang sama, tidak jam yang
    # persis sama tiap hari.
    schedule.every().day.at("00:05").do(lambda: _schedule_random_jobs_for_language("id", id_ranges))
    schedule.every().day.at("00:06").do(lambda: _schedule_random_jobs_for_language("en", en_ranges))
    
    # Schedule Analytics Malam Hari
    schedule.every().day.at("23:30").do(lambda: MotivationAgent().run_analytics())
    
    logger.info("Scheduler siap. Menunggu waktu eksekusi...")
    while True:
        schedule.run_pending()
        time.sleep(30)
