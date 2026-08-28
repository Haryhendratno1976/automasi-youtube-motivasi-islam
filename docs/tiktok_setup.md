# Panduan Setup TikTok Content Posting API

TikTok memiliki kebijakan API yang cukup ketat. Berikut adalah cara mendapatkan akses resmi:

## 1. Daftar di TikTok For Developers
1. Buka [TikTok for Developers](https://developers.tiktok.com/).
2. Daftar sebagai Developer dan buat aplikasi baru.
3. Pilih produk **"Content Posting API"**.

## 2. Dapatkan Client Key & Secret
1. Setelah aplikasi dibuat, Anda akan mendapatkan `Client Key` dan `Client Secret`.
2. Masukkan kedua nilai tersebut ke file `.env` di project ini.

## 3. Konfigurasi Redirect URI
1. Di dashboard TikTok Developer, atur Redirect URI ke `http://localhost:5000/callback` (atau sesuaikan dengan kebutuhan Anda).

## 4. Alternatif (Tanpa API Resmi)
Jika Anda kesulitan mendapatkan akses API resmi, Anda bisa menggunakan metode **Browser Automation** (Selenium/Playwright). 
Namun, metode ini memiliki risiko:
- Akun bisa terdeteksi sebagai bot.
- Sering gagal jika ada update UI TikTok.

**Rekomendasi**: Gunakan API resmi untuk keamanan jangka panjang akun Anda.
