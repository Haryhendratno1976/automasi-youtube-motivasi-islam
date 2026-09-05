#!/usr/bin/env python3
"""
Modul 6: Review Actions (Shared)
Logika approve/tolak video yang menunggu review manual -- dipakai BERSAMA
oleh dashboard web (app.py, /review) dan Telegram bot (telegram_bot.py,
tombol inline) supaya perilakunya selalu konsisten, tidak ada duplikasi
logika yang bisa saling berbeda seiring waktu.
"""

import os
import json
import logging
from datetime import datetime

logger = logging.getLogger("ReviewActions")

PENDING_FILE = "reports/pending_review.json"
POSTED_FILE = "reports/posted_videos.json"


def load_pending_reviews():
    if not os.path.exists(PENDING_FILE):
        return []
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Gagal membaca {PENDING_FILE}: {e}")
        return []


def save_pending_reviews(items):
    os.makedirs("reports", exist_ok=True)
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)


def approve_review_item(review_id):
    """
    Upload video yang di-approve ke YouTube + Facebook Reels, catat ke
    database dashboard. Return (success: bool, message: str, item: dict|None).
    """
    items = load_pending_reviews()
    item = next((p for p in items if p["id"] == review_id and p.get("status") == "pending"), None)
    if not item:
        return False, "Video review tidak ditemukan (mungkin sudah diproses).", None

    if not os.path.exists(item["video_path"]):
        item["status"] = "expired"
        save_pending_reviews(items)
        return False, (
            f"File video sudah tidak ada di disk ({item['video_path']}) -- kemungkinan "
            f"kena cleanup otomatis karena terlalu lama menunggu review."
        ), item

    try:
        try:
            # Kasus produksi: review_actions.py diimpor sebagai
            # modules.review_actions (dipanggil dari modules.telegram_bot
            # dan modules.app). uploader.py harus ada di folder yang SAMA
            # (modules/) supaya relative import ini berhasil.
            from .uploader import VideoUploader
        except ImportError:
            # Kasus dijalankan langsung berdiri sendiri (mis. testing) --
            # relative import di atas gagal karena tidak ada package induk,
            # fallback ke absolute import (uploader.py harus ada di folder
            # yang sama dengan file ini).
            from uploader import VideoUploader
        uploader = VideoUploader()
        yt_id = uploader.upload_to_youtube(
            video_path=item["video_path"],
            title=item["title"],
            description=item["description"],
            language=item["language"],
            tags=item.get("tags"),
        )
        uploader.upload_to_facebook_reels(item["video_path"], item["description"], item["language"])

        if not yt_id:
            reason = getattr(uploader, "last_youtube_error", None) or "Penyebab tidak terdeteksi -- cek log Railway."
            return False, f"Upload gagal -- {reason}", item

        item["status"] = "approved"
        item["youtube_id"] = yt_id
        save_pending_reviews(items)

        existing_data = {"id": [], "en": []}
        if os.path.exists(POSTED_FILE):
            try:
                with open(POSTED_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        existing_data = loaded
            except Exception:
                pass
        existing_data.setdefault(item["language"], [])
        existing_data[item["language"]].append({
            "video_id": yt_id,
            "title": item["title"],
            "posted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "url": f"https://youtube.com/shorts/{yt_id}",
        })
        with open(POSTED_FILE, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, indent=2)

        return True, f"Video '{item['title']}' berhasil di-approve & upload! Video ID: {yt_id}", item

    except Exception as e:
        logger.error(f"Gagal approve & upload review {review_id}: {e}")
        return False, f"Gagal upload: {e}", item


def reject_review_item(review_id):
    """Tolak & hapus video yang direview. Return (success: bool, message: str, item: dict|None)."""
    items = load_pending_reviews()
    item = next((p for p in items if p["id"] == review_id and p.get("status") == "pending"), None)
    if not item:
        return False, "Video review tidak ditemukan.", None

    item["status"] = "rejected"
    try:
        if os.path.exists(item["video_path"]):
            os.remove(item["video_path"])
    except Exception as e:
        logger.warning(f"Gagal hapus file video yang ditolak: {e}")
    save_pending_reviews(items)
    return True, f"Video '{item['title']}' ditolak & dihapus.", item
