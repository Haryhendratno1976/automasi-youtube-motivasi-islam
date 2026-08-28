# Panduan Setup YouTube Data API v3

Untuk mengaktifkan fitur posting otomatis ke YouTube Shorts, ikuti langkah-langkah berikut:

## 1. Buat Project di Google Cloud Console
1. Buka [Google Cloud Console](https://console.cloud.google.com/).
2. Buat project baru dengan nama "Motivation AI Agent".
3. Di Dashboard, cari **"YouTube Data API v3"** dan klik **Enable**.

## 2. Konfigurasi OAuth Consent Screen
1. Buka menu **APIs & Services > OAuth consent screen**.
2. Pilih **External** dan klik Create.
3. Isi informasi aplikasi (nama, email support).
4. Pada bagian **Scopes**, tambahkan scope: `.../auth/youtube.upload`.
5. Tambahkan email YouTube Anda sebagai **Test User** (karena aplikasi masih dalam mode testing).

## 3. Buat Credentials
1. Buka menu **APIs & Services > Credentials**.
2. Klik **Create Credentials > OAuth client ID**.
3. Pilih Application type: **Desktop app**.
4. Download file JSON yang dihasilkan dan simpan di root folder project dengan nama `client_secrets.json`.

## 4. Jalankan Autentikasi Pertama Kali
Saat pertama kali menjalankan `main.py` atau `modules/poster.py`, browser akan terbuka dan meminta Anda login ke akun YouTube. Setelah login, sistem akan menyimpan `token.pickle` sehingga Anda tidak perlu login lagi di masa mendatang.

---
**Tips**: Pastikan akun YouTube Anda sudah diverifikasi untuk mengunggah video agar tidak terkena limitasi.
