import requests

# Masukkan Client Key dan Client Secret dari TikTok Developer Portal Anda
CLIENT_KEY = "MASUKKAN_CLIENT_KEY_ANDA"
CLIENT_SECRET = "MASUKKAN_CLIENT_SECRET_ANDA"
REDIRECT_URI = "https://localhost/" # Sesuaikan dengan Redirect URI di App Settings

def generate_auth_url():
    # URL untuk mengarahkan pengguna ke halaman Oauth login TikTok
    scope = "user.info.basic,video.upload,video.publish"
    url = (
        f"https://www.tiktok.com/v2/auth/authorize/"
        f"?client_key={CLIENT_KEY}"
        f"&response_type=code"
        f"&scope={scope}"
        f"&redirect_uri={REDIRECT_URI}"
    )
    print("1. Buka URL berikut di browser Anda:")
    print(url)
    print("\n2. Login dan izinkan akses.")
    print("3. Setelah di-redirect, salin kode 'code=' dari URL di browser Anda.")

def get_access_token(auth_code):
    # Menukarkan authorization code dengan Access Token
    url = "https://open.tiktokapis.com/v2/oauth/token/"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": auth_code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI
    }
    
    response = requests.post(url, headers=headers, data=data)
    res_data = response.json()
    
    if "access_token" in res_data.get("data", {}):
        print("\n" + "="*50)
        print("BERHASIL! Berikut Access Token Anda:")
        print("="*50)
        print(res_data["data"]["access_token"])
        print("="*50)
    else:
        print("Gagal mendapatkan token:", res_data)

if __name__ == "__main__":
    generate_auth_url()
    code = input("\nMasukkan kode 'code' dari URL hasil redirect: ").strip()
    if code:
        get_access_token(code)