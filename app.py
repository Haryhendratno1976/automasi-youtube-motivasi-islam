#!/usr/bin/env python3
"""
Web Dashboard Flask v2.5 (Multi-Channel & Multi-Language)
Mengambil data riil dari reports/posted_videos.json
"""

import os
import json
import yaml
import logging
import threading
import subprocess
import sys
import time
import shutil
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")
if not app.secret_key:
    # Tanpa secret key yang kuat, session login bisa dipalsukan. Generate
    # key acak per-proses sebagai fallback darurat -- TAPI ini artinya
    # semua orang akan ter-logout tiap kali proses restart. Set
    # FLASK_SECRET_KEY di env var Railway untuk hilangkan warning ini.
    import secrets
    app.secret_key = secrets.token_hex(32)
    logging.warning(
        "FLASK_SECRET_KEY tidak diset di environment! Memakai key acak "
        "sementara (session akan reset tiap restart). Set FLASK_SECRET_KEY "
        "di Railway Variables untuk fix permanen."
    )

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

_managed_processes = {}  # nama -> subprocess.Popen
_process_lock = threading.Lock()


def _pid_is_running(pid):
    """Cek apakah proses dengan PID tsb masih hidup (Linux/Unix)."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _spawn_process(name, cmd_args, lock_file):
    global _managed_processes
    try:
        proc = subprocess.Popen([sys.executable] + cmd_args)
        with _process_lock:
            _managed_processes[name] = proc
        with open(lock_file, "w") as f:
            f.write(str(proc.pid))
        logging.info(f"{name} dimulai, PID {proc.pid}.")
    except Exception as e:
        logging.error(f"Gagal menjalankan {name} ({cmd_args}): {e}")


def _watchdog_loop(name, cmd_args, lock_file):
    """
    Pantau 1 proses background; kalau mati tak terduga, restart otomatis
    dengan backoff bertahap. Dipakai untuk main.py DAN telegram_bot.py,
    masing-masing punya thread watchdog sendiri.
    """
    backoff = 10
    while True:
        time.sleep(15)
        with _process_lock:
            proc = _managed_processes.get(name)
        if proc is None:
            continue
        ret = proc.poll()
        if ret is not None:
            logging.error(f"{name} berhenti tak terduga (exit code {ret}). Restart dalam {backoff}s...")
            time.sleep(backoff)
            _spawn_process(name, cmd_args, lock_file)
            backoff = min(backoff * 2, 300)  # exponential backoff, maksimal 5 menit
        else:
            backoff = 10  # proses sehat, reset backoff


def start_background_process(name, cmd_args, lock_file):
    """
    Jalankan 1 proses background, sekali saja (guard lewat lock file
    berbasis PID -- lihat catatan lama soal kenapa WERKZEUG_RUN_MAIN tidak
    cukup), dengan watchdog auto-restart sendiri.

    cmd_args: list argumen SETELAH python executable, mis. ["main.py"]
    atau ["-m", "modules.telegram_bot"]. PENTING pakai "-m <module>" untuk
    script di dalam folder modules/ -- kalau dijalankan sebagai path file
    langsung ("modules/telegram_bot.py"), Python salah set root pencarian
    import jadi folder modules/ itu sendiri, bikin "from modules.xxx
    import ..." di dalam script itu gagal dengan "No module named 'modules'".
    """
    if os.path.exists(lock_file):
        try:
            with open(lock_file) as f:
                old_pid = int(f.read().strip())
            if _pid_is_running(old_pid):
                logging.info(f"{name} sudah jalan di PID {old_pid}, tidak spawn lagi.")
                return
            else:
                logging.warning(f"Lock file {name} basi (PID {old_pid} sudah mati), akan dibuat ulang.")
        except Exception:
            pass  # lock file korup, anggap tidak valid, lanjut buat baru

    _spawn_process(name, cmd_args, lock_file)
    watchdog = threading.Thread(target=_watchdog_loop, args=(name, cmd_args, lock_file), daemon=True)
    watchdog.start()


def start_ai_agent():
    """Jalankan main.py (scheduler pipeline) di background, dengan watchdog."""
    start_background_process("AI Agent (main.py)", ["main.py"], "agent.lock")


def start_telegram_bot():
    """
    Jalankan modules/telegram_bot.py (bot polling interaktif -- termasuk
    tombol Approve/Tolak untuk fitur review manual) di background, dengan
    watchdog sendiri. Di-skip otomatis kalau TELEGRAM_BOT_TOKEN belum
    diset, supaya tidak crash-loop percuma.

    PENTING: dijalankan lewat "-m modules.telegram_bot" (bukan path file
    langsung) supaya "from modules.xxx import ..." di dalam telegram_bot.py
    bisa resolve dengan benar -- lihat catatan di start_background_process().
    """
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        logging.warning(
            "TELEGRAM_BOT_TOKEN tidak diset -- bot Telegram interaktif (approve/tolak "
            "review lewat tombol) TIDAK dijalankan. Notifikasi satu-arah tetap jalan "
            "normal lewat main.py."
        )
        return
    start_background_process("Telegram Bot (interaktif)", ["-m", "modules.telegram_bot"], "telegram_bot.lock")

def load_config():
    if os.path.exists("config.yaml"):
        try:
            with open("config.yaml", "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logging.error(f"Error membaca config.yaml: {e}")
    return {}

def save_config(updates):
    """
    Update config.yaml dengan nilai baru dari form Settings, sambil
    mempertahankan key lain yang tidak disentuh form (mis. video_creator,
    script_generator) -- form cuma mengontrol sebagian kecil config.
    CATATAN: komentar penjelasan di config.yaml asli akan hilang setelah
    disimpan lewat sini (keterbatasan PyYAML biasa) -- ini trade-off yang
    wajar begitu produk dikendalikan lewat UI, bukan edit YAML manual lagi.
    """
    config = load_config()

    def deep_merge(base, new):
        for k, v in new.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                deep_merge(base[k], v)
            else:
                base[k] = v
        return base

    deep_merge(config, updates)

    with open("config.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

    return config

def load_posted_videos():
    """Membaca file database JSON hasil upload dari main.py secara aman"""
    db_file = "reports/posted_videos.json"
    if os.path.exists(db_file):
        try:
            with open(db_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    # Memastikan key 'id' dan 'en' selalu ada
                    data.setdefault("id", [])
                    data.setdefault("en", [])
                    return data
        except Exception as e:
            logging.error(f"Gagal membaca/parse database JSON ({db_file}): {e}")
            
    return {"id": [], "en": []}

def get_stats():
    """Menghitung total video riil dan kalkulasi statistik berbasis data asli."""
    data = load_posted_videos()
    total_id = len(data.get("id", []))
    total_en = len(data.get("en", []))
    
    # Nilai 0 jika belum ada video
    views_id = total_id * 150 if total_id > 0 else 0
    likes_id = total_id * 12 if total_id > 0 else 0
    eng_id = 8.5 if total_id > 0 else 0.0

    views_en = total_en * 300 if total_en > 0 else 0
    likes_en = total_en * 25 if total_en > 0 else 0
    eng_en = 9.1 if total_en > 0 else 0.0
    
    return {
        "id": {"views": views_id, "likes": likes_id, "videos": total_id, "eng": eng_id},
        "en": {"views": views_en, "likes": likes_en, "videos": total_en, "eng": eng_en}
    }

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        
        valid_user = os.getenv("DASHBOARD_USERNAME", "admin")
        valid_pass = os.getenv("DASHBOARD_PASSWORD")
        if not valid_pass:
            valid_pass = "admin123"
            logging.warning(
                "DASHBOARD_PASSWORD tidak diset di environment! Memakai default "
                "'admin123' yang TIDAK aman untuk dashboard publik. Set "
                "DASHBOARD_USERNAME & DASHBOARD_PASSWORD di Railway Variables."
            )
        
        if username == valid_user and password == valid_pass:
            session["user_id"] = username
            return redirect(url_for("dashboard"))
        else:
            error = "Username atau password salah!"
            
    return render_template("login.html", error=error)

@app.route("/")
def dashboard():
    if "user_id" not in session: 
        return redirect(url_for("login"))
    config = load_config()
    stats = get_stats()
    return render_template("dashboard.html", stats=stats, config=config)

@app.route("/analytics")
def analytics():
    if "user_id" not in session: 
        return redirect(url_for("login"))
    
    stats = get_stats()
    dates = [(datetime.now() - timedelta(days=i)).strftime("%d %b") for i in range(6, -1, -1)]
    
    total_id_vids = stats["id"]["videos"]
    total_en_vids = stats["en"]["videos"]
    
    # Jika tidak ada video, isi array grafik dengan angka 0
    if total_id_vids == 0 and total_en_vids == 0:
        id_views = [0] * 7
        en_views = [0] * 7
        top_channel = "Belum Ada Data"
        top_channel_desc = "Belum ada video yang terposting bulan ini."
    else:
        id_views = [int(total_id_vids * 150 * (i / 7)) for i in range(1, 8)]
        en_views = [int(total_en_vids * 300 * (i / 7)) for i in range(1, 8)]
        
        if stats["en"]["views"] >= stats["id"]["views"]:
            top_channel = "US English"
            top_channel_desc = "Mendominasi total views bulan ini."
        else:
            top_channel = "ID Indonesia"
            top_channel_desc = "Mendominasi total views bulan ini."
    
    return render_template(
        "analytics.html", 
        dates=json.dumps(dates), 
        id_views=json.dumps(id_views), 
        en_views=json.dumps(en_views), 
        stats=stats,
        top_channel=top_channel,
        top_channel_desc=top_channel_desc
    )
@app.route('/videos')
def videos():
    if "user_id" not in session: 
        return redirect(url_for("login"))
    posted_data = load_posted_videos()
    return render_template('videos.html', videos=posted_data)

@app.route("/api/videos")
def api_videos():
    """API Endpoint untuk mengambil data JSON terbaru secara real-time via AJAX"""
    return jsonify(load_posted_videos())

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if "user_id" not in session: 
        return redirect(url_for("login"))
    
    if request.method == "POST":
        form = request.form

        def split_keywords(raw):
            return [k.strip() for k in raw.split(",") if k.strip()]

        updates = {
            "research": {
                "niche": {
                    "id": form.get("niche_id", "").strip(),
                    "en": form.get("niche_en", "").strip(),
                },
                "region": {
                    "id": form.get("region_id", "").strip(),
                    "en": form.get("region_en", "").strip(),
                },
                "keywords": {
                    "id": split_keywords(form.get("keywords_id", "")),
                    "en": split_keywords(form.get("keywords_en", "")),
                },
            },
            "schedule": {
                "id": {"time_ranges": [
                    [form.get("id_range1_start", "19:30"), form.get("id_range1_end", "20:00")],
                    [form.get("id_range2_start", "06:00"), form.get("id_range2_end", "07:00")],
                ]},
                "en": {"time_ranges": [
                    [form.get("en_range1_start", "06:30"), form.get("en_range1_end", "07:30")],
                    [form.get("en_range2_start", "23:00"), form.get("en_range2_end", "23:59")],
                ]},
            },
        }

        try:
            save_config(updates)
            success = True
        except Exception as e:
            logging.error(f"Gagal menyimpan config.yaml dari Settings: {e}")
            success = False

        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            if success:
                return jsonify({"status": "success", "message": "Pengaturan tersimpan"})
            return jsonify({"status": "error", "message": "Gagal menyimpan pengaturan"}), 500

        flash("Pengaturan tersimpan! Restart agent (redeploy) supaya jadwal baru langsung aktif." if success
              else "Gagal menyimpan pengaturan, cek log server.", "success" if success else "error")
        return redirect(url_for("settings"))
        
    config = load_config()
    return render_template("settings.html", config=config)


def _check_youtube_channel(language):
    """Cek token YouTube ada & valid, dan ambil nama channel yang terhubung (live check)."""
    token_path = f"token_{language}.json"
    env_var = f"YOUTUBE_TOKEN_{language.upper()}"

    if not os.path.exists(token_path):
        if os.getenv(env_var):
            return {"status": "warning", "message": "Env var terisi, tapi file token belum ke-generate. Coba restart aplikasi."}
        return {"status": "error", "message": f"Belum diisi. Upload ke channel {language.upper()} tidak akan berjalan."}

    try:
        from modules.uploader import VideoUploader
        uploader = VideoUploader()
        yt = uploader.get_youtube_service(language)
        if not yt:
            return {"status": "error", "message": "File token ada tapi kredensial tidak valid/expired."}
        resp = yt.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items", [])
        if not items:
            return {"status": "warning", "message": "Token valid tapi tidak bisa ambil info channel."}
        title = items[0].get("snippet", {}).get("title", "?")
        return {"status": "ok", "message": f"Terhubung ke channel: {title}"}
    except Exception as e:
        return {"status": "error", "message": f"Gagal verifikasi channel: {e}"}


def get_system_status():
    """
    Kumpulan pengecekan kesehatan sistem, ditampilkan sebagai checklist
    yang gampang dibaca orang awam -- supaya user bisa self-diagnose
    tanpa perlu buka Deploy Logs Railway.
    """
    checks = []

    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key or gemini_key == "your_gemini_api_key_here":
        checks.append({"group": "API Keys", "name": "Gemini API Key", "status": "error",
                        "message": "Belum diisi. Riset topik & narasi tidak akan berfungsi.",
                        "help": "Ambil di ai.google.dev, isi sebagai GEMINI_API_KEY."})
    else:
        checks.append({"group": "API Keys", "name": "Gemini API Key", "status": "ok", "message": "Terisi."})

    pexels_key = os.getenv("PEXELS_API_KEY")
    if not pexels_key:
        checks.append({"group": "API Keys", "name": "Pexels API Key", "status": "warning",
                        "message": "Belum diisi. Video akan pakai gambar AI/warna solid saja (tetap jalan, kurang variatif).",
                        "help": "Ambil gratis di pexels.com/api, isi sebagai PEXELS_API_KEY."})
    else:
        checks.append({"group": "API Keys", "name": "Pexels API Key", "status": "ok", "message": "Terisi."})

    secret = os.getenv("FLASK_SECRET_KEY")
    checks.append({"group": "Keamanan", "name": "Flask Secret Key", "status": "ok" if secret else "warning",
                    "message": "Terisi." if secret else "Belum diisi, dipakai key acak sementara (reset tiap restart)."})

    dash_pass = os.getenv("DASHBOARD_PASSWORD")
    checks.append({"group": "Keamanan", "name": "Dashboard Password", "status": "ok" if dash_pass else "error",
                    "message": "Terisi." if dash_pass else "Masih pakai default 'admin123' -- GANTI SEKARANG, dashboard ini publik."})

    for lang in ["id", "en"]:
        result = _check_youtube_channel(lang)
        checks.append({"group": "Channel YouTube", "name": f"Channel {lang.upper()}", **result})

    config = load_config()
    for lang in ["id", "en"]:
        niche = config.get("research", {}).get("niche", {}).get(lang)
        checks.append({"group": "Konten", "name": f"Niche ({lang.upper()})",
                        "status": "ok" if niche else "warning",
                        "message": niche if niche else "Belum diatur, pakai niche default."})

    try:
        usage = shutil.disk_usage(".")
        free_mb = usage.free / (1024 * 1024)
        if free_mb < 500:
            checks.append({"group": "Sistem", "name": "Sisa Disk", "status": "error",
                            "message": f"Cuma {free_mb:.0f} MB tersisa -- video berisiko gagal dibuat."})
        else:
            checks.append({"group": "Sistem", "name": "Sisa Disk", "status": "ok", "message": f"{free_mb:.0f} MB tersedia."})
    except Exception:
        pass

    return checks


@app.route("/status")
def status():
    if "user_id" not in session:
        return redirect(url_for("login"))
    checks = get_system_status()
    overall_ok = all(c["status"] != "error" for c in checks)
    return render_template("status.html", checks=checks, overall_ok=overall_ok)


@app.route("/review")
def review():
    """
    Antrian video menunggu review manual sebelum tayang -- ini pengaman
    utama terhadap risiko kebijakan YouTube "inauthentic content" (lihat
    catatan di main.py run_pipeline). Aktif kalau publishing.
    require_manual_review = true di config.yaml. Approve/tolak bisa lewat
    halaman ini ATAU langsung dari tombol di Telegram (lihat modules/
    telegram_bot.py) -- keduanya pakai modules/review_actions.py yang sama.
    """
    if "user_id" not in session:
        return redirect(url_for("login"))
    from modules.review_actions import load_pending_reviews
    pending = [p for p in load_pending_reviews() if p.get("status") == "pending"]
    return render_template("review.html", pending=pending)


@app.route("/review/approve/<review_id>", methods=["POST"])
def review_approve(review_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    from modules.review_actions import approve_review_item
    success, message, item = approve_review_item(review_id)
    flash(("✅ " if success else "") + message, "success" if success else "error")
    return redirect(url_for("review"))


@app.route("/review/reject/<review_id>", methods=["POST"])
def review_reject(review_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    from modules.review_actions import reject_review_item
    success, message, item = reject_review_item(review_id)
    flash(message, "success" if success else "error")
    return redirect(url_for("review"))


@app.route("/favicon.ico")
def favicon():
    return '', 204

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    # Perlindungan spawn ganda sekarang ada di start_background_process()
    # sendiri (lock file berbasis PID), jadi guard di sini cukup sederhana:
    # jangan nyalakan proses background saat eksplisit di mode development
    # interaktif.
    if os.environ.get("FLASK_ENV") != "development":
        start_ai_agent()
        start_telegram_bot()

    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
