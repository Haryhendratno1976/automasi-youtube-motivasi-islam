import asyncio
from playwright.async_api import async_playwright

async def save_session():
    async with async_playwright() as p:
        # Buka browser Chrome biasa agar Anda bisa login
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("Membuka TikTok Studio...")
        await page.goto("https://www.tiktok.com/tiktokstudio/upload")
        
        print("\n" + "="*60)
        print("SILAKAN LOGIN KE AKUN TIKTOK ANDA DI BROWSER YANG TERBUKA.")
        print("Setelah selesai login dan berada di halaman upload TikTok Studio,")
        print("kembali ke terminal ini lalu tekan ENTER.")
        print("="*60 + "\n")
        
        input("Tekan ENTER jika sudah berhasil login...")

        # Simpan state/session (cookie & token login) ke file json
        await context.storage_state(path="tiktok_cookies.json")
        print("Sesi login berhasil disimpan ke 'tiktok_cookies.json'!")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(save_session())