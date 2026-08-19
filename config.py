"""
config.py - Basit ayarlar.

ONEMLI: Tabletleri ogrencilere vermeden once ADMIN_PASSWORD degerini
mutlaka degistirin! Aksi halde ogrenciler admin paneline girip cevap
anahtarlarini gorebilir.

Admin sifresi artik once Streamlit'in "Secrets" (gizli bilgiler) ayarindan
okunmaya calisilir -- bu sayede GitHub deposu herkese acik (public) olsa
bile sifre kod icinde gorunmez. Bulutta calisirken Streamlit Cloud'un
"Secrets" bolumune ADMIN_PASSWORD = "sizin-sifreniz" satirini eklemeniz
yeterli. Bilgisayarinizda yerel calistirirken (BASLAT.bat ile) Secrets
tanimli olmadigi icin asagidaki varsayilan deger kullanilir; isterseniz
onu da degistirebilirsiniz.
"""

import streamlit as st

try:
    ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]
except Exception:
    st.error(
        "ADMIN_PASSWORD ayarlanmamış. Bilgisayarınızda "
        r".streamlit\secrets.toml dosyasına, bulutta ise Streamlit Cloud'un "
        "'Secrets' bölümüne ADMIN_PASSWORD = \"sizin-sifreniz\" satırını ekleyin."
    )
    st.stop()

APP_TITLE = "LGS Eğitim Platformu"
PDF_DIR_NAME = "pdfs"
