import os
from google_auth_oauthlib.flow import InstalledAppFlow

# Scope yang dibutuhkan untuk mengunggah video ke YouTube
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

def main():
    if not os.path.exists('client_secret.json'):
        print("Error: File 'client_secret.json' tidak ditemukan di folder ini!")
        return

    # Menjalankan alur autentikasi lokal
    flow = InstalledAppFlow.from_client_secrets_file('client_secret_828690851833-t1ogk5b4p3ju4ga6553n9obvj54aof2g.apps.googleusercontent.com.json', SCOPES)
    
    # access_type='offline' dan prompt='consent' SANGAT PENTING untuk mendapatkan Refresh Token
    credentials = flow.run_local_server(port=8080, access_type='offline', prompt='consent')

    print("\n" + "="*50)
    print("BERHASIL AUTENTIKASI!")
    print("Berikut adalah Refresh Token Anda:")
    print("="*50)
    print(credentials.refresh_token)
    print("="*50)
    print("\nSalin token di atas dan masukkan ke file .env Anda sebagai YT_ID_REFRESH_TOKEN.")

if __name__ == '__main__':
    main()