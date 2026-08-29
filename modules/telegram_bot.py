#!/usr/bin/env python3
"""
Modul Telegram Bot v2.5 (Multi-Channel Support & Notifier)
Mengintegrasikan Notifikasi Otomatis dan Interactive Dashboard Control.
"""

import os
import logging
import yaml
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler

logger = logging.getLogger("TelegramBot")

# PENTING: versi sebelumnya punya token bot & chat ID ASLI hardcoded di
# sini sebagai fallback -- itu KEBOCORAN KEAMANAN SERIUS (siapapun yang
# baca kode ini dapat kontrol penuh bot Telegram kamu). Sudah dihapus.
# WAJIB revoke token lama lewat @BotFather -> /mybots -> API Token ->
# Revoke, generate token baru, simpan HANYA di env var TELEGRAM_BOT_TOKEN.
# Tidak ada fallback default lagi -- kalau env var kosong, fitur Telegram
# dinonaktifkan dengan jelas (bukan diam-diam pakai token orang lain).

class TelegramNotifier:
    """Kelas khusus untuk mengirim notifikasi status video dari pipeline main.py."""
    def __init__(self, config_path="config.yaml"):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not self.token or not self.chat_id:
            logger.warning(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tidak diset di environment -- "
                "notifikasi & review lewat Telegram tidak akan berfungsi sampai diisi."
            )

    async def send_message(self, text: str) -> bool:
        """Mengirim pesan teks/notifikasi secara asynchronous."""
        if not self.token or not self.chat_id:
            logger.warning("⚠️ Token atau Chat ID Telegram tidak ditemukan.")
            return False
            
        try:
            # PENTING: pakai `async with Bot(...)` (bukan `Bot(...)` polos)
            # supaya python-telegram-bot benar-benar initialize() dan
            # shutdown() client HTTP internalnya (httpx). Tanpa ini, tiap
            # panggilan send_message() bikin objek Bot baru tanpa pernah
            # ditutup dengan rapi -- untuk scheduler yang jalan terus-
            # menerus dan kirim notifikasi berkali-kali sehari, ini lama-
            # lama meninggalkan koneksi HTTP yang tidak dibersihkan.
            async with Bot(token=self.token) as bot:
                await bot.send_message(chat_id=self.chat_id, text=text, parse_mode="Markdown")
            logger.info("✅ Notifikasi Telegram berhasil dikirim!")
            return True
        except Exception as e:
            logger.error(f"❌ Gagal mengirim notifikasi Telegram: {e}")
            return False

    async def send_review_request(self, review_id: str, title: str, hook: str, language: str, video_path: str = None) -> bool:
        """
        Kirim video yang menunggu review DENGAN tombol inline Approve/Tolak
        -- ini yang bikin approve/reject bisa langsung dari chat Telegram,
        tanpa perlu buka dashboard web. Tombol ditangani oleh
        TelegramBotManager.button_handler (lihat run_polling di app.py).

        PENTING: video_path harus diisi supaya file video-nya BENERAN
        terkirim dan bisa ditonton langsung di Telegram sebelum approve --
        sebelumnya cuma kirim teks judul/hook tanpa video sama sekali,
        jadi tidak mungkin review dengan benar (approve buta).
        """
        if not self.token or not self.chat_id:
            logger.warning("⚠️ Token atau Chat ID Telegram tidak ditemukan, tidak bisa kirim permintaan review.")
            return False
        try:
            # Sama seperti send_message() -- pakai `async with Bot(...)`
            # supaya client HTTP internalnya di-initialize()/shutdown()
            # dengan rapi, bukan ditinggal terbuka tiap panggilan.
            async with Bot(token=self.token) as bot:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approve & Upload", callback_data=f"approve:{review_id}"),
                    InlineKeyboardButton("❌ Tolak", callback_data=f"reject:{review_id}"),
                ]])
                caption = (
                    f"📝 *Video {language.upper()} Menunggu Review*\n\n"
                    f"*Judul:* {title}\n"
                    f"*Hook:* {hook}\n\n"
                    f"Tonton dulu videonya di atas, lalu tap tombol untuk approve/tolak."
                )

                if video_path and os.path.exists(video_path):
                    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
                    if file_size_mb > 50:
                        # Batas kirim file langsung lewat Bot API standar ~50MB --
                        # video kita (preset ultrafast, durasi pendek) harusnya
                        # jauh di bawah itu, tapi tetap dijaga supaya tidak error.
                        logger.warning(
                            f"Video {video_path} ({file_size_mb:.1f}MB) melebihi batas 50MB Telegram Bot API, "
                            f"kirim teks saja tanpa video."
                        )
                        await bot.send_message(chat_id=self.chat_id, text=caption, parse_mode="Markdown", reply_markup=keyboard)
                    else:
                        with open(video_path, "rb") as video_file:
                            await bot.send_video(
                                chat_id=self.chat_id,
                                video=video_file,
                                caption=caption,
                                parse_mode="Markdown",
                                reply_markup=keyboard,
                                supports_streaming=True,
                            )
                else:
                    logger.warning(f"video_path '{video_path}' tidak ada/tidak diisi, kirim teks saja tanpa video.")
                    await bot.send_message(chat_id=self.chat_id, text=caption, parse_mode="Markdown", reply_markup=keyboard)

            logger.info(f"✅ Permintaan review {review_id} terkirim ke Telegram.")
            return True
        except Exception as e:
            logger.error(f"❌ Gagal kirim permintaan review ke Telegram: {e}")
            return False

class TelegramBotManager:
    """Kelas untuk mengelola Bot Interaktif (Menu Utama, Inline Buttons, Polling)."""
    def __init__(self, config_path="config.yaml"):
        self.config_path = config_path
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f)
        else:
            self.config = {}
            
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🇮🇩 Channel Indo", callback_data="chan_id"),
             InlineKeyboardButton("🇺🇸 Channel English", callback_data="chan_en")],
            [InlineKeyboardButton("📊 Global Status", callback_data="agent_status"),
             InlineKeyboardButton("🚀 Generate All", callback_data="agent_generate")],
            [InlineKeyboardButton("📈 Analytics Comparison", callback_data="agent_analytics")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.message:
            await update.message.reply_text("🤖 *AI Agent Multi-Channel Panel*\nPilih channel atau aksi:", reply_markup=reply_markup, parse_mode="Markdown")

    async def _safe_edit_status(self, query, text):
        """
        Update pesan status (mis. "Memproses...", lalu hasil akhir) dengan
        AMAN, entah pesan aslinya teks biasa ATAU video dengan caption.

        BUG YANG DIPERBAIKI: pesan hasil send_review_request() di
        TelegramNotifier bisa berupa VIDEO (caption=..., bukan text=...)
        kalau video_path terisi -- ini kasus NORMAL setiap kali ada video
        menunggu review. Pesan video TIDAK PUNYA `text`, cuma `caption`,
        jadi edit_message_text() akan gagal dengan
        "There is no text in the message to edit" (BadRequest).

        Sebelumnya exception ini MELEDAK sebelum approve_review_item()/
        reject_review_item() sempat dipanggil sama sekali -- akibatnya user
        tap Approve di Telegram, tapi video TIDAK PERNAH ter-upload ke
        YouTube, tanpa ada tanda kegagalan yang jelas ke user (cuma error
        di log server). Deteksi tipe pesan di sini, dan JANGAN PERNAH biarkan
        kegagalan update status UI ini menghentikan aksi approve/reject yang
        sebenarnya -- exception selalu ditangkap & di-log, tidak dilempar ulang.
        """
        try:
            if query.message and query.message.caption is not None:
                await query.edit_message_caption(caption=text)
            else:
                await query.edit_message_text(text)
        except Exception as e:
            logger.error(f"Gagal update status pesan Telegram (tidak fatal, proses tetap lanjut): {e}")

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data

        # Tombol approve/tolak video hasil review manual -- pakai logika
        # yang SAMA dengan dashboard web (/review), lewat modules/review_actions.py,
        # supaya perilakunya konsisten di kedua tempat.
        if data.startswith("approve:") or data.startswith("reject:"):
            action, review_id = data.split(":", 1)
            await self._safe_edit_status(query, f"⏳ Memproses ({action})...")

            try:
                # Kasus produksi: telegram_bot.py diimpor sebagai
                # modules.telegram_bot (lihat main.py:
                # `from modules.telegram_bot import TelegramNotifier`).
                # review_actions.py harus ada di folder yang SAMA (modules/)
                # supaya relative import ini berhasil.
                from .review_actions import approve_review_item, reject_review_item
            except ImportError:
                # Kasus dijalankan langsung berdiri sendiri (mis. `python3
                # telegram_bot.py` untuk testing bot interaktif) -- relative
                # import di atas gagal karena tidak ada package induk, jadi
                # fallback ke absolute import (review_actions.py harus ada
                # di folder yang sama dengan file ini).
                from review_actions import approve_review_item, reject_review_item
            import asyncio
            loop = asyncio.get_event_loop()
            func = approve_review_item if action == "approve" else reject_review_item
            # Jalankan di executor -- upload video itu blocking (3-5 detik+),
            # jangan sampai menahan event loop bot (tetap responsif ke
            # pesan/tombol lain selagi upload berjalan).
            success, message, item = await loop.run_in_executor(None, func, review_id)
            icon = "✅" if success else "❌"
            if action == "reject" and success:
                icon = "🗑️"

            await self._safe_edit_status(query, f"{icon} {message}")
            return

        if data == "chan_id":
            msg = "🇮🇩 *Channel Indonesia*\nStatus: Active\nJadwal: 08:00, 18:00\nFormat: Shorts (1080x1920)"
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=self._back_keyboard())
        elif data == "chan_en":
            msg = "🇺🇸 *Channel English*\nStatus: Active\nJadwal: 10:00, 20:00\nFormat: Shorts (1080x1920)"
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=self._back_keyboard())
        elif data == "agent_status":
            msg = "🟢 *Global Agent Status: RUNNING*\nMode: Multi-Language (ID + EN)\nTotal Posting Harian: 4 video"
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=self._back_keyboard())
        elif data == "agent_analytics":
            msg = "📈 *Analytics Comparison*\n\n🇮🇩 ID: Active\n🇺🇸 EN: Active\n\n Laporan detail diperbarui jam 23:30."
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=self._back_keyboard())
        elif data == "main_menu":
            keyboard = [
                [InlineKeyboardButton("🇮🇩 Channel Indo", callback_data="chan_id"),
                 InlineKeyboardButton("🇺🇸 Channel English", callback_data="chan_en")],
                [InlineKeyboardButton("📊 Global Status", callback_data="agent_status"),
                 InlineKeyboardButton("🚀 Generate All", callback_data="agent_generate")],
                [InlineKeyboardButton("📈 Analytics Comparison", callback_data="agent_analytics")]
            ]
            await query.edit_message_text("🤖 *AI Agent Multi-Channel Panel*\nPilih channel atau aksi:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    def _back_keyboard(self):
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]])

    async def _error_handler(self, update, context):
        """
        Handler error terpusat untuk Application -- TANPA ini, python-telegram-bot
        cuma dump traceback panjang ke log tiap kali ada exception (persis
        yang terlihat di log kamu: "No error handlers are registered").

        Khusus untuk telegram.error.Conflict ("terminated by other getUpdates
        request"): ini BUKAN bug di kode -- artinya ada 2 instance bot jalan
        bersamaan dengan token yang SAMA (Telegram cuma izinkan 1 koneksi
        polling aktif per token). Paling sering terjadi sebentar saat Railway
        redeploy (container lama belum benar-benar mati saat container baru
        mulai polling) -- python-telegram-bot SUDAH otomatis retry sampai
        container lama benar-benar berhenti, jadi biasanya pulih sendiri
        dalam beberapa detik. Kalau ini muncul TERUS-MENERUS (bukan cuma
        sebentar saat redeploy), cek di Railway: pastikan service bot ini
        replicas=1 (bukan lebih), dan tidak ada instance lain (mis. dijalankan
        manual/lokal) pakai TELEGRAM_BOT_TOKEN yang sama secara bersamaan.
        """
        from telegram.error import Conflict
        if isinstance(context.error, Conflict):
            logger.warning(
                "Conflict: ada instance bot LAIN yang juga polling dengan token yang sama "
                "(biasanya normal & sementara saat Railway redeploy -- container lama vs baru "
                "tumpang tindih sebentar). Kalau ini berlanjut terus, cek replicas di Railway "
                "dan pastikan tidak ada instance lokal yang jalan bersamaan."
            )
        else:
            logger.error(f"Error tak terduga di Telegram bot: {context.error}", exc_info=context.error)

    def run(self):
        if not self.token:
            logger.error("Token Telegram tidak ditemukan. Bot gagal berjalan.")
            return
        
        logger.info("Menjalankan Telegram Bot Listener...")
        app = ApplicationBuilder().token(self.token).build()
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CallbackQueryHandler(self.button_handler))
        app.add_error_handler(self._error_handler)
        # drop_pending_updates=True -- buang update/tombol yang sempat
        # tertunda dari SEBELUM restart ini (mis. tombol approve/reject lama
        # yang video-nya mungkin sudah tidak relevan/sudah expired), supaya
        # bot tidak memproses aksi basi begitu instance baru mulai polling.
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("🤖 Menjalankan Telegram Bot Standalone...")
    bot_manager = TelegramBotManager()
    bot_manager.run()
