#!/usr/bin/env python3
"""
Modul 4: Auto-Posting (Multi-Channel)
Mendukung posting ke channel YouTube dan TikTok yang berbeda berdasarkan bahasa.
"""

import os
import logging
import yaml
import pickle
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ContentPoster:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        self.posting_config = self.config.get("posting", {})

    def get_youtube_service(self, language="id"):
        """
        Mendapatkan service YouTube berdasarkan channel bahasa tertentu.
        """
        scopes = ["https://www.googleapis.com/auth/youtube.upload"]
        creds = None
        token_file = f"token_{language}.pickle"
        secret_file = f"client_secrets_{language}.json"
        
        if os.path.exists(token_file):
            with open(token_file, "rb") as token:
                creds = pickle.load(token)
                
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(secret_file):
                    logger.error(f"{secret_file} tidak ditemukan untuk bahasa {language}!")
                    return None
                flow = InstalledAppFlow.from_client_secrets_file(secret_file, scopes)
                creds = flow.run_local_server(port=0)
            with open(token_file, "wb") as token:
                pickle.dump(creds, token)
                
        return build("youtube", "v3", credentials=creds)

    def upload_to_youtube(self, video_path, title, description, tags, language="id"):
        youtube = self.get_youtube_service(language)
        if not youtube: return False
        
        logger.info(f"Mengunggah ke YouTube Shorts ({language.upper()}): {title}")
        
        conf = self.posting_config["youtube"].get(language, self.posting_config["youtube"]["id"])
        
        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": tags,
                "categoryId": "22"
            },
            "status": {
                "privacyStatus": conf["privacy_status"],
                "selfDeclaredMadeForKids": conf["made_for_kids"]
            }
        }
        
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"Upload YT {language.upper()}: {int(status.progress() * 100)}%")
                
        logger.info(f"Berhasil diunggah ke YouTube {language.upper()}! ID: {response['id']}")
        return response["id"]

    def upload_to_tiktok(self, video_path, caption, language="id"):
        """
        Placeholder untuk upload TikTok multi-channel.
        Menggunakan environment variables TT_ID_... atau TT_EN_...
        """
        logger.info(f"Mempersiapkan upload ke TikTok ({language.upper()})...")
        env_prefix = f"TT_{language.upper()}_"
        access_token = os.environ.get(f"{env_prefix}ACCESS_TOKEN")
        
        if not access_token:
            logger.warning(f"Access token untuk TikTok {language.upper()} tidak ditemukan.")
            return False
            
        logger.info(f"Video {video_path} akan diunggah ke TikTok channel {language.upper()}.")
        return True

if __name__ == "__main__":
    poster = ContentPoster()
    # poster.upload_to_youtube("video.mp4", "Title", "Desc", ["tag"], "en")
