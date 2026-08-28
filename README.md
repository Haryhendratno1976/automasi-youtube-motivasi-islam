# 🌍 AI Agent Motivasi Islam (Multi-Language & Multi-Channel)

Project ini adalah upgrade besar dari sistem otomasi video short motivasi, kini mendukung target penonton **Internasional (Amerika)** dan **Lokal (Indonesia)** secara bersamaan dengan channel terpisah.

---

## 🚀 Fitur 

1. **🌐 Dukungan Multi-Bahasa**:
   - Menghasilkan konten dalam **Bahasa Indonesia** dan **Bahasa Inggris**.
   - Script generator menggunakan AI untuk menghasilkan narasi yang natural bagi penonton global.
   - Voiceover (TTS) menggunakan suara premium yang sesuai dengan masing-masing bahasa.

2. **📺 Multi-Channel Management**:
   - **YouTube**: Posting otomatis ke 2 channel berbeda (1 ID, 1 EN).
   - **facebook**: Posting otomatis ke 2 akun berbeda (1 ID, 1 EN).
   - Konfigurasi API keys dan token terpisah untuk setiap channel di `.env`.

3. **📅 Smart Scheduling (4x Daily)**:
   - Posting 4 video setiap hari: 2x Indonesia dan 2x Inggris.
   - Jadwal dapat diatur secara independen untuk masing-masing bahasa di `config.yaml`.

4. **🔍 Global Trend Research**:
   - Modul riset kini memantau trending motivasi di pasar internasional (English-speaking audience) serta pasar lokal.

5. **📊 Comparative Analytics Dashboard**:
   - Dashboard web menampilkan perbandingan performa antar channel.
   - Grafik interaktif untuk memantau views, likes, dan engagement rate per bahasa.
   - Telegram Bot mendukung pengecekan status per channel.

---

## 📂 Konfigurasi Multi-Channel

### 1. File Konfigurasi (`config.yaml`)
Sekarang mendukung struktur per bahasa:
```yaml
schedule:
  id:
    post_times: ["05:00", "18:00"]
  en:
    post_times: ["10:00", "20:00"]
```

### 2. Environment Variables (`.env`)
Anda perlu menyiapkan kredensial untuk masing-masing channel:
- `YT_ID_...` & `YT_EN_...` untuk YouTube.
- `TT_ID_...` & `TT_EN_...` untuk TikTok.
- Lihat `.env.example` untuk daftar lengkap variabel yang diperlukan.

### 3. YouTube Auth
Sistem akan mencari file token terpisah:
- `token_id.pickle` & `client_secrets_id.json`
- `token_en.pickle` & `client_secrets_en.json`

---

## 🛠️ Cara Update & Jalankan
1. Ekstrak file project 
2. Update dependensi: `pip install -r requirements.txt`.
3. Sesuaikan `config.yaml` dan `.env` dengan kredensial channel Anda.
4. Jalankan dashboard: `python app.py`.
5. Jalankan agent: `python main.py`.

---

## ☁️ Deployment
Sistem tetap mendukung deployment ke **Railway** atau **Render**. Pastikan semua environment variables multi-channel sudah dimasukkan di panel kontrol cloud Anda.
"# Youtubemotivasiislam" 
