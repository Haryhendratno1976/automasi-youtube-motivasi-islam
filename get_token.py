import json
from google_auth_oauthlib.flow import InstalledAppFlow

# Scope khusus untuk upload YouTube
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def generate_authorized_token():
    # Menggunakan file client secrets mentah milikmu
    flow = InstalledAppFlow.from_client_secrets_file(
        "client_secrets_i.json", SCOPES
    )
    
    print("Membuka browser untuk otorisasi Google...")
    # Gunakan port=0 agar otomatis menyesuaikan dengan OAuth Desktop App (Bypass redirect_uri_mismatch)
    creds = flow.run_local_server(port=0)
    
    # Format hasil JSON token
    token_json = creds.to_json()
    
    print("\n" + "="*60)
    print("✅ SALIN SELURUH TEKS JSON DI BAWAH INI UNTUK RAILWAY VARIABLE:")
    print("="*60)
    print(token_json)
    print("="*60 + "\n")
    
    # PERBAIKAN: Simpan ke token_id.json (JANGAN menimpa client_secrets_i.json!)
    with open("token_id.json", "w", encoding="utf-8") as f:
        f.write(token_json)
        
    print(" Saved token to token_id.json successfully!")

if __name__ == "__main__":
    generate_authorized_token()