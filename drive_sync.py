"""
drive_sync.py - Google Drive'daki bir klasordeki PDF'leri listeleyip
sisteme indiren, ISTEGE BAGLI entegrasyon.

Bu modulun calismasi icin SIZIN kendi Google hesabinizla bir kere kurulum
yapmaniz gerekir (bunu sizin adiniza bu ortamdan yapamayiz cunku Google
hesabiniza erisim gerektirir). Adimlar README.md icinde ayrintili
anlatilmistir; ozetle:

  1) https://console.cloud.google.com adresinde bir proje acin.
  2) "Google Drive API"yi etkinlestirin.
  3) "OAuth istemci kimligi" (Masaustu uygulamasi tipi) olusturup
     credentials.json dosyasini indirin.
  4) credentials.json dosyasini bu proje klasorune (app.py ile ayni yere) koyun.

credentials.json yoksa, uygulamadaki "Google Drive'dan İçe Aktar" bolumu
otomatik olarak gizlenir; sistemin geri kalani (PDF yukleme, cozme,
puanlama) bundan etkilenmeden calismaya devam eder.
"""

import io
import os

CREDENTIALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.json")
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def is_configured():
    return os.path.exists(CREDENTIALS_PATH)


def _get_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            # Yerel bilgisayarda calisirken bir tarayici sekmesi acar, admin izin verir.
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds)


def list_pdfs_in_folder(folder_id):
    """Klasordeki pdf dosyalarini [{"id":..., "name":...}] olarak dondurur."""
    service = _get_service()
    query = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
    results = []
    page_token = None
    while True:
        resp = (
            service.files()
            .list(q=query, fields="nextPageToken, files(id, name)", pageToken=page_token)
            .execute()
        )
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def download_file(file_id, dest_path):
    from googleapiclient.http import MediaIoBaseDownload

    service = _get_service()
    request = service.files().get_media(fileId=file_id)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with io.FileIO(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest_path
