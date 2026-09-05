#!/usr/bin/env python3
"""
Modul Uploader Multi-Channel (YouTube & TikTok)
Mendukung otentikasi berbasis file token JSON lokal maupun Environment Variables (Railway).
"""

import os
import json
import logging
import yaml
import requests
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("VideoUploader")

class VideoUploader:
    def __init__(self, config_path="config.yaml"):
        # expected_channel_ids: opsional, kalau diisi di config.yaml jadi
        # pengaman ekstra -- upload akan DIBATALKAN otomatis kalau token
        # yang dipakai ternyata mengarah ke channel ID yang tidak sesuai.
        self.expected_channel_ids = {}
        # Alasan spesifik kegagalan upload YOUTUBE TERAKHIR -- diisi di
        # tiap titik kegagalan upload_to_youtube(), dibaca oleh
        # review_actions.py supaya pesan di Telegram BISA langsung bilang
        # alasan sebenarnya (kredensial hilang/salah channel/dll), bukan
        # cuma "Upload gagal" generik yang mengharuskan cek log Railway
        # setiap kali.
        self.last_youtube_error = None
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            self.expected_channel_ids = config.get("youtube_channels", {}) or {}
        except Exception as e:
            logger.warning(f"Tidak bisa baca config.yaml untuk verifikasi channel ({e}), verifikasi dilewati.")

    def get_youtube_service(self, language="id"):
        """Mendapatkan service Google API YouTube dengan fallback ke Environment Variables."""
        token_file = f"token_{language}.json"
        creds = None

        # 1. Cek apakah ada file token fisik lokal
        if os.path.exists(token_file):
            logger.info(f"Menggunakan file token lokal: {token_file}")
            try:
                creds = Credentials.from_authorized_user_file(token_file)
            except Exception as e:
                # File ada tapi isinya rusak/tidak lengkap (mis. JSON dari
                # env var YOUTUBE_TOKEN_EN tidak valid). Jangan biarkan
                # exception ini menjalar dan menghentikan seluruh pipeline
                # -- log jelas lalu coba fallback ke env var terpisah.
                logger.error(
                    f"{token_file} ada tapi GAGAL dibaca sebagai kredensial ({e}). "
                    f"Cek isi file/env var YOUTUBE_TOKEN_{language.upper()} -- "
                    f"harus JSON valid berisi client_id, client_secret, refresh_token, token_uri."
                )
                creds = None

        # 2. Fallback membaca Kredensial dari Environment Variables terpisah (Railway Deployment)
        if creds is None:
            prefix = language.upper()
            logger.info(f"Mencoba membaca kredensial {prefix} dari Environment Variables terpisah (YT_{prefix}_*)...")
            client_id = os.environ.get(f"YT_{prefix}_CLIENT_ID")
            client_secret = os.environ.get(f"YT_{prefix}_CLIENT_SECRET")
            refresh_token = os.environ.get(f"YT_{prefix}_REFRESH_TOKEN")

            missing = [
                name for name, val in [
                    (f"YT_{prefix}_CLIENT_ID", client_id),
                    (f"YT_{prefix}_CLIENT_SECRET", client_secret),
                    (f"YT_{prefix}_REFRESH_TOKEN", refresh_token),
                ] if not val
            ]
            if missing:
                logger.error(
                    f"Kredensial YouTube {prefix} tidak lengkap. Tidak ada {token_file} yang valid, "
                    f"dan env var berikut kosong/tidak diset: {', '.join(missing)}. "
                    f"Upload YouTube {prefix} dibatalkan."
                )
                self.last_youtube_error = (
                    f"Env var berikut KOSONG/tidak diset di Railway: {', '.join(missing)}. "
                    f"Isi di Railway -> Variables untuk service yang menjalankan main.py."
                )
                return None

            token_data = {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "token_uri": "https://oauth2.googleapis.com/token"
            }
            try:
                creds = Credentials.from_authorized_user_info(token_data)
            except Exception as e:
                logger.error(f"Gagal membuat kredensial dari Environment Variables {prefix}: {e}")
                self.last_youtube_error = f"Gagal membuat kredensial dari env var {prefix}: {e}"
                return None

        try:
            return build("youtube", "v3", credentials=creds)
        except Exception as e:
            logger.error(f"Gagal membangun service YouTube untuk bahasa {language}: {e}")
            self.last_youtube_error = f"Gagal membangun service YouTube ({language}): {e}"
            return None

    def _verify_channel(self, youtube, language):
        """
        Ambil info channel yang akan dipakai upload, LOG JELAS supaya bisa
        dicek manual di log kapan saja, dan kalau config.yaml punya
        expected_channel_id untuk bahasa ini, BATALKAN upload kalau tidak
        cocok -- pengaman terakhir supaya video tidak pernah nyasar ke
        channel yang salah lagi (kasus ID ke-upload ke channel EN).
        """
        try:
            resp = youtube.channels().list(part="snippet", mine=True).execute()
            items = resp.get("items", [])
            if not items:
                logger.warning(f"Tidak bisa ambil info channel untuk verifikasi ({language.upper()}), tetap lanjut.")
                return True

            channel_id = items[0].get("id")
            channel_title = items[0].get("snippet", {}).get("title", "?")
            logger.info(f"Upload {language.upper()} akan menyasar channel: '{channel_title}' (ID: {channel_id})")

            expected_id = (self.expected_channel_ids.get(language) or {}).get("expected_channel_id")
            if expected_id and channel_id != expected_id:
                logger.error(
                    f"❌ CHANNEL MISMATCH! Upload {language.upper()} seharusnya ke channel ID "
                    f"'{expected_id}', tapi token yang dipakai mengarah ke '{channel_title}' "
                    f"(ID: {channel_id}). UPLOAD DIBATALKAN untuk mencegah video nyasar ke channel salah. "
                    f"Cek token_{language}.json / env var YOUTUBE_TOKEN_{language.upper()} di Railway."
                )
                return False
            return True
        except Exception as e:
            logger.warning(f"Gagal verifikasi channel ({e}), tetap lanjut upload tanpa verifikasi.")
            return True

    def upload_to_youtube(self, video_path, title, description, language="id", tags=None):
        """Mengunggah video MP4 ke YouTube Shorts."""
        logger.info(f"Mempersiapkan upload ke YouTube ({language.upper()})...")
        self.last_youtube_error = None  # reset tiap panggilan baru

        try:
            youtube = self.get_youtube_service(language)
        except Exception as e:
            logger.error(f"Gagal tak terduga saat menyiapkan service YouTube {language.upper()}: {e}")
            self.last_youtube_error = f"Gagal menyiapkan service YouTube: {e}"
            youtube = None

        if not youtube:
            logger.error(f"Upload YouTube {language.upper()} dibatalkan karena kredensial tidak valid/ditemukan.")
            if not self.last_youtube_error:
                self.last_youtube_error = (
                    f"Kredensial YouTube {language.upper()} tidak valid/tidak ditemukan -- "
                    f"cek token_{language}.json atau env var YT_{language.upper()}_CLIENT_ID/"
                    f"CLIENT_SECRET/REFRESH_TOKEN di Railway Variables."
                )
            return None

        if not self._verify_channel(youtube, language):
            self.last_youtube_error = (
                f"Channel MISMATCH -- token yang dipakai mengarah ke channel YouTube yang "
                f"BEDA dari yang diharapkan untuk bahasa {language.upper()}. Cek "
                f"expected_channel_id di config.yaml vs token yang sedang aktif."
            )
            return None

        # YouTube API batasi total panjang tags gabungan ~500 karakter --
        # potong defensif supaya tidak reject request kalau daftar tags panjang.
        final_tags = tags if tags else ['shorts', 'motivation', 'mindset', 'success']
        final_tags = final_tags[:15]

        try:
            body = {
                'snippet': {
                    'title': title[:100], # Maksimal 100 karakter
                    'description': description,
                    'tags': final_tags,
                    'categoryId': '22' # People & Blogs / Education
                },
                'status': {
                    'privacyStatus': 'public',
                    'selfDeclaredMadeForKids': False,
                    # Disclosure resmi konten AI (field API sejak Okt 2024,
                    # sama dengan toggle "Altered or synthetic content" di
                    # YouTube Studio). Video ini pakai suara TTS AI dan
                    # kadang visual AI-generated -- riset kebijakan 2026
                    # bilang jelas: konten AI yang di-DISCLOSE dapat RPM
                    # setara non-AI, yang TIDAK di-disclose yang kena
                    # penalti/strike. Jadi selalu nyalakan ini, bukan opsional.
                    'containsSyntheticMedia': True,
                }
            }

            media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype='video/mp4')
            request = youtube.videos().insert(
                part=','.join(body.keys()),
                body=body,
                media_body=media
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.info(f"Upload YouTube Progress: {int(status.progress() * 100)}%")

            video_id = response.get("id")
            logger.info(f"✅ Berhasil upload ke YouTube! Video ID: {video_id}")
            return video_id

        except Exception as e:
            logger.error(f"Gagal mengunggah video ke YouTube ({language}): {e}")
            self.last_youtube_error = f"Error saat proses upload ke YouTube: {e}"
            return None

    def upload_to_facebook_reels(self, video_path, description, language="id"):
        """
        Upload video sebagai Facebook Reel ke Page yang terhubung, lewat
        Resumable Upload API resmi Meta (3 tahap):
          1. Init   : POST /<PAGE_ID>/video_reels (upload_phase=start) -> video_id + upload_url
          2. Upload : POST file video ke rupload.facebook.com
          3. Publish: POST /<PAGE_ID>/video_reels (upload_phase=finish, video_state=PUBLISHED)

        Butuh env var FB_<LANG>_PAGE_ID dan FB_<LANG>_PAGE_ACCESS_TOKEN
        (Page Access Token dengan permission pages_manage_posts).
        Video kita (1080x1920, rasio 9:16) sudah sesuai syarat minimum
        Facebook Reels (540x960, 9:16) tanpa perlu diubah.
        """
        prefix = language.upper()
        page_id = os.environ.get(f"FB_{prefix}_PAGE_ID")
        access_token = os.environ.get(f"FB_{prefix}_PAGE_ACCESS_TOKEN")

        if not page_id or not access_token:
            logger.warning(
                f"FB_{prefix}_PAGE_ID / FB_{prefix}_PAGE_ACCESS_TOKEN tidak ditemukan. "
                f"Upload Facebook Reels ({prefix}) dilewati."
            )
            return None

        api_version = "v25.0"
        try:
            # 1. Init upload session
            init_resp = requests.post(
                f"https://graph.facebook.com/{api_version}/{page_id}/video_reels",
                data={"upload_phase": "start", "access_token": access_token},
                timeout=15,
            )
            init_resp.raise_for_status()
            init_data = init_resp.json()
            video_id = init_data.get("video_id")
            upload_url = init_data.get("upload_url")
            if not video_id or not upload_url:
                logger.error(f"Gagal init upload Facebook Reels ({prefix}): {init_data}")
                return None

            # 2. Upload file video (endpoint rupload.facebook.com, BUKAN graph.facebook.com)
            file_size = os.path.getsize(video_path)
            with open(video_path, "rb") as f:
                file_data = f.read()
            upload_resp = requests.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {access_token}",
                    "offset": "0",
                    "file_size": str(file_size),
                },
                data=file_data,
                timeout=120,
            )
            upload_resp.raise_for_status()
            upload_result = upload_resp.json()
            if not upload_result.get("success"):
                logger.error(f"Gagal upload file Facebook Reels ({prefix}): {upload_result}")
                return None

            # 3. Publish
            publish_resp = requests.post(
                f"https://graph.facebook.com/{api_version}/{page_id}/video_reels",
                data={
                    "upload_phase": "finish",
                    "video_id": video_id,
                    "description": description[:2200],  # batas kasar caption FB
                    "video_state": "PUBLISHED",
                    "access_token": access_token,
                },
                timeout=15,
            )
            publish_resp.raise_for_status()
            publish_data = publish_resp.json()
            if publish_data.get("success"):
                logger.info(f"✅ Berhasil upload Facebook Reels ({prefix})! Video ID: {video_id}")
                return video_id
            logger.error(f"Gagal publish Facebook Reels ({prefix}): {publish_data}")
            return None

        except Exception as e:
            logger.error(f"Gagal upload Facebook Reels ({prefix}): {e}")
            return None
