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

# Bu, yöneticinin İLK kullanıcı adıdır -- sadece hesap ilk oluşturulurken kullanılır.
# Şifre (ve isterseniz kullanıcı adını da) daha sonra uygulama içinden, Admin
# Panelindeki "Hesap Ayarları" bölümünden değiştirebilirsiniz; oradan yapılan
# değişiklik veritabanında saklanır, bu dosyadaki değeri etkilemez.
ADMIN_USERNAME = st.secrets.get("ADMIN_USERNAME", "admin")

APP_TITLE = "M.ONUR LGS Eğitim Platformu"
PDF_DIR_NAME = "pdfs"
