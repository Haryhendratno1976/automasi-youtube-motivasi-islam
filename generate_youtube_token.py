from google_auth_oauthlib.flow import InstalledAppFlow

# Ambil scope upload YouTube
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Ganti 'client_secrets_id.json' dengan file client secret mentah milikmu jika beda nama
flow = InstalledAppFlow.from_client_secrets_file('client_secrets_id.json', SCOPES)
creds = flow.run_local_server(port=8080)

# Simpan token hasil otorisasi
with open('authorized_token_id.json', 'w') as token:
    token.write(creds.to_json())

print("✅ BERHASIL! File 'authorized_token_id.json' telah dibuat.")