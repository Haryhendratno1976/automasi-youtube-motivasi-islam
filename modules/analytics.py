#!/usr/bin/env python3
"""
Modul 5: Analytics & Reporting (Multi-Channel)
Melacak performa dan membandingkan channel Indonesia vs Inggris.
"""

import os
import json
import logging
import yaml
from datetime import datetime
from pathlib import Path

try:
    # Reuse VideoUploader.get_youtube_service() -- sudah menangani OAuth
    # (file token lokal ATAU env var YT_<LANG>_* di Railway) dengan benar,
    # jadi tidak perlu duplikasi logic auth di sini.
    from .uploader import VideoUploader
except ImportError:
    from uploader import VideoUploader

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AnalyticsManager:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        self.report_dir = Path(self.config.get("analytics", {}).get("report_output_dir", "reports"))
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = config_path
        self._uploader = None  # dibuat lazy, cuma kalau benar-benar butuh fetch statistik

    def _load_posted_video_ids(self, video_data=None):
        """
        Ambil daftar video ID per bahasa yang benar-benar sudah pernah
        di-upload. SUMBER UTAMA: reports/posted_videos.json -- file ini
        persisten di disk dan terus bertambah lintas proses/restart
        (ditulis oleh main.py setiap kali upload berhasil). PENTING:
        `video_data` yang dikirim run_analytics() di main.py TIDAK bisa
        diandalkan sendirian -- MotivationAgent() dibuat baru tiap kali
        job() dipanggil scheduler, jadi self.posted_data selalu ke-reset
        kosong tiap run. Makanya di sini dibaca dari file JSON persisten,
        bukan cuma parameter in-memory yang sering kosong.

        `video_data` (kalau ada isinya) tetap digabung sebagai tambahan --
        supaya video yang baru saja sukses di-upload di run YANG SAMA ikut
        terhitung walau reports/posted_videos.json belum sempat dibaca ulang
        dari disk (race condition kecil, jaga-jaga saja).
        """
        result = {"id": set(), "en": set()}

        db_file = self.report_dir / "posted_videos.json"
        if db_file.exists():
            try:
                with open(db_file, "r", encoding="utf-8") as f:
                    persisted = json.load(f)
                for lang in ("id", "en"):
                    for entry in persisted.get(lang, []):
                        vid = entry.get("video_id") if isinstance(entry, dict) else entry
                        if vid:
                            result[lang].add(vid)
            except Exception as e:
                logger.warning(f"Gagal membaca {db_file} untuk analytics ({e}), lanjut dengan data yang ada.")

        if video_data:
            for lang in ("id", "en"):
                for vid in video_data.get(lang, []) or []:
                    if vid:
                        result[lang].add(vid)

        return {lang: list(ids) for lang, ids in result.items()}

    def _get_youtube_service(self, language):
        """Bikin service YouTube (via VideoUploader, reuse auth yang sudah ada) sekali saja, dipakai untuk kedua bahasa."""
        if self._uploader is None:
            self._uploader = VideoUploader(self.config_path)
        try:
            return self._uploader.get_youtube_service(language)
        except Exception as e:
            logger.error(f"Gagal membuat YouTube service untuk analytics ({language.upper()}): {e}")
            return None

    def _fetch_video_statistics(self, youtube, video_ids):
        """
        Ambil statistik (views/likes/comments) sungguhan dari YouTube Data
        API v3 untuk daftar video_ids, di-chunk 50 per panggilan (limit API
        untuk videos.list(id=...)). Video yang gagal diambil (mis. sudah
        dihapus/private) di-skip dengan warning, bukan bikin seluruh fetch
        gagal -- konsisten dengan filosofi defensive coding di modul lain.
        """
        total_views = 0
        total_likes = 0
        total_comments = 0
        videos_found = 0

        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i:i + 50]
            try:
                response = youtube.videos().list(
                    part="statistics",
                    id=",".join(chunk),
                ).execute()
            except Exception as e:
                logger.warning(f"Gagal fetch statistik untuk batch video ({len(chunk)} ID): {e}")
                continue

            for item in response.get("items", []):
                stats = item.get("statistics", {})
                total_views += int(stats.get("viewCount", 0))
                # likeCount/commentCount bisa TIDAK ADA sama sekali di
                # response kalau creator menonaktifkan like/comment count
                # publik -- .get(...) dengan default "0" mencegah KeyError.
                total_likes += int(stats.get("likeCount", 0))
                total_comments += int(stats.get("commentCount", 0))
                videos_found += 1

        missing = len(video_ids) - videos_found
        if missing > 0:
            logger.warning(
                f"{missing} dari {len(video_ids)} video ID tidak ditemukan di respons YouTube "
                f"(kemungkinan sudah dihapus/private)."
            )

        return {
            "total_views": total_views,
            "total_likes": total_likes,
            "total_comments": total_comments,
            "total_videos": videos_found,
            "engagement_rate": round((total_likes / total_views * 100), 2) if total_views > 0 else 0.0,
        }

    def fetch_stats(self, video_data=None):
        """
        Mengambil statistik performa SUNGGUHAN dari YouTube Data API untuk
        video ID yang sudah pernah di-upload (dibaca dari
        reports/posted_videos.json + `video_data` kalau ada).
        """
        logger.info("Mengambil statistik performa multi-channel dari YouTube API...")

        video_ids_by_lang = self._load_posted_video_ids(video_data)

        stats = {"timestamp": datetime.now().isoformat()}
        for language in ("id", "en"):
            ids = video_ids_by_lang.get(language, [])
            if not ids:
                logger.info(f"Belum ada video {language.upper()} yang tercatat -- statistik diisi nol.")
                stats[language] = {
                    "total_views": 0, "total_likes": 0, "total_comments": 0,
                    "total_videos": 0, "engagement_rate": 0.0,
                }
                continue

            youtube = self._get_youtube_service(language)
            if youtube is None:
                logger.warning(
                    f"Tidak bisa fetch statistik {language.upper()} (kredensial YouTube tidak tersedia) "
                    f"-- hanya total_videos dari catatan lokal yang diisi, sisanya nol."
                )
                stats[language] = {
                    "total_views": 0, "total_likes": 0, "total_comments": 0,
                    "total_videos": len(ids), "engagement_rate": 0.0,
                }
                continue

            stats[language] = self._fetch_video_statistics(youtube, ids)

        # Bandingkan channel berdasarkan data SUNGGUHAN yang baru diambil,
        # bukan nilai hardcoded -- pemenangnya bisa ID atau EN tergantung
        # performa aktual, dan growth_diff dihitung dari selisih views nyata.
        id_views = stats["id"]["total_views"]
        en_views = stats["en"]["total_views"]
        if id_views == 0 and en_views == 0:
            top_channel = None
            growth_diff = "N/A (belum ada data views)"
        else:
            top_channel = "en" if en_views >= id_views else "id"
            smaller = min(id_views, en_views)
            larger = max(id_views, en_views)
            pct = ((larger - smaller) / smaller * 100) if smaller > 0 else 100.0
            growth_diff = f"+{pct:.0f}% ({top_channel.upper()} vs {'ID' if top_channel == 'en' else 'EN'})"

        stats["comparison"] = {
            "top_performing_channel": top_channel,
            "growth_diff": growth_diff,
        }

        report_path = self.report_dir / f"performance_report_{datetime.now().strftime('%Y%m%d')}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=4)

        logger.info(f"Laporan statistik multi-channel disimpan ke {report_path}")
        return stats

if __name__ == "__main__":
    manager = AnalyticsManager()
    data = {"id": ["v1", "v2"], "en": ["v3", "v4", "v5"]}
    print(json.dumps(manager.fetch_stats(data), indent=2))
