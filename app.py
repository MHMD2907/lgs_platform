"""
LGS Eğitim Platformu - app.py
Tablet/bilgisayar tarayıcısından çalışan, geçmiş yıl LGS (ve 6-7. sınıf,
İOKBS, genel soru bankası) denemelerini çözüp otomatik puanlayan sistem.

Çalıştırmak için:
    pip install -r requirements.txt
    streamlit run app.py

Ayrıntılı kurulum ve kullanım için README.md dosyasına bakın.
"""

import io
import os
import re
import shutil
from datetime import datetime
from urllib.parse import quote

import pandas as pd
import streamlit as st

import bot
import config
import db
import drive_sync
import parsing
import scoring
import soru_bankasi

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# PDF'ler "static" klasoru altinda tutulur; Streamlit bu klasoru dogrudan
# /app/static/... adresinden sunar. Boylece tablet PDF'i normal bir dosya
# gibi BIR KEZ indirip onbellege alir (base64 gomme yontemi ise 25 MB'lik
# bir kitapcigi ~33 MB metne cevirip her yenilemede tekrar gonderiyordu).
STATIC_DIR = os.path.join(BASE_DIR, "static")
PDF_DIR = os.path.join(STATIC_DIR, config.PDF_DIR_NAME)
os.makedirs(PDF_DIR, exist_ok=True)
# ÖNEMLİ - GÜVENLİK: Cevap anahtarı DAHİL orijinal PDF'ler kesinlikle "static"
# klasörünün DIŞINDA tutulur. "static" klasörü Streamlit tarafından herkese
# açık bir URL üzerinden sunulur (enableStaticServing=true); dosya adı tahmin
# edilebilirse (ör. deneme başlığından türetilmiş "slug") admin paneline hiç
# girmeden cevap anahtarlı PDF'e ulaşılabilir. Bu yüzden orijinal PDF'ler
# static'in dışındaki bu özel klasörde tutulup show_pdf() ile HER ZAMAN
# base64 gömme yöntemiyle (yani sadece admin paneli üzerinden, dogrudan URL
# olmadan) gösterilir.
PRIVATE_DIR = os.path.join(BASE_DIR, "private_pdfs")
os.makedirs(PRIVATE_DIR, exist_ok=True)

db.init_db()
db.ensure_default_admin(config.ADMIN_USERNAME, "Yönetici", config.ADMIN_PASSWORD)

# --- 8. Sınıf LGS için resmi ders/soru sayısı/katsayı yapısı (sabit) ---
LGS_SUBJECTS = {
    "Sözel": [("Türkçe", 20, 4), ("İnkılap", 10, 1), ("Din", 10, 1), ("İngilizce", 10, 1)],
    "Sayısal": [("Matematik", 20, 4), ("Fen", 20, 4)],
}
LGS_STRUCTURE = {
    section: {name: {"count": c, "coef": k} for name, c, k in subs}
    for section, subs in LGS_SUBJECTS.items()
}
LGS_CATEGORY = "8. Sınıf (LGS)"

SUBJECT_KEYWORDS = [
    "Türkçe", "Matematik", "Fen", "İnkılap", "Din", "İngilizce",
    "Sosyal", "Tarih", "Coğrafya", "Fizik", "Kimya", "Biyoloji",
]


# ---------------------------------------------------------------- helpers

def guess_title_and_subject(filename):
    """'Matematik_Carpanlar_Test1.pdf' -> ('Matematik Carpanlar Test1', 'Matematik')
    Dosya adından okunabilir bir başlık ve (varsa) ders adını tahmin eder;
    admin panelinde önceden doldurulmuş ama düzenlenebilir alan olarak kullanılır."""
    name = os.path.splitext(filename)[0]
    pretty = re.sub(r"[_\-]+", " ", name).strip()
    subject = None
    for kw in SUBJECT_KEYWORDS:
        if kw.lower() in pretty.lower():
            subject = kw
            break
    return pretty, subject


TR_MAP = str.maketrans("ğüşıöçĞÜŞİÖÇ", "gusiocGUSIOC")


def slugify(title, fallback="test"):
    """Dosya adi icin guvenli, ASCII bir ad uretir (Turkce karakterler
    donusturulur). Boylece dosya adresleri tarayicida sorunsuz calisir."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", (title or "").translate(TR_MAP)).strip("_")
    return s or fallback


def _compression_note(path):
    """Admin panelinde 'deneme eklendi' mesajının yanına, PDF'in gerçekte
    ne kadar küçültülüp küçültülemediğini gösteren küçük bir not üretir --
    böylece 'PDF açılmıyor' sorununu kör kör tahmin etmek yerine, boyutun
    gerçekten küçülüp küçülmediğini birlikte görebiliyoruz."""
    try:
        size_mb = round(os.path.getsize(path) / (1024 * 1024), 1)
    except OSError:
        return ""
    gs_found = shutil.which("gs") is not None
    if not gs_found:
        # ÖNEMLİ - ESKİ UYARI YANILTICIYDI: Burada "Ghostscript bulunamadı,
        # GitHub'dan yeniden dağıtın" yazıyordu. Oysa (a) uygulama kendi
        # bilgisayarınızda çalışırken `packages.txt` zaten hiç devreye girmez,
        # Ghostscript'in orada olmaması normaldir; (b) daha da önemlisi,
        # kitapçık artık tarayıcıya gömülmüyor, her sayfa sunucuda tek tek
        # resme çevrilip gösteriliyor. Ölçüldü: 11,5 MB'lik sıkıştırılmamış
        # dosyada da 4 MB'lik sıkıştırılmış dosyada da sayfa açma süresi aynı
        # (~0,04 saniye). Yani küçültme yapılmaması bir SORUN DEĞİL.
        return (
            f" (PDF boyutu: {size_mb} MB — küçültme yapılmadı, sorun değil: sayfalar "
            f"tek tek resme çevrilerek gösterildiği için dosya boyutu açılma hızını "
            f"etkilemiyor. Sadece 'kitapçığın tamamını indir' biraz uzun sürebilir.)"
        )
    return f" (PDF boyutu: {size_mb} MB — küçültülmüş olarak kaydedildi.)"


def _pdf_cache_entry(path):
    """Bir PDF'in ham baytlarini oturum icinde bir kez okuyup onbellekte
    tutar (yol + degisim zamani + boyut anahtar olarak). Sadece "kitapcigin
    tamamini indir" dugmesi icin kullanilir.

    ONEMLI - PERFORMANS: Ogrenci sinav cozerken her cevap tikladiginda
    sayfa yeniden calisiyor (ilerlemeyi otomatik kaydedebilmek icin). Bu
    yuzden ayni dosyayi HER rerun'da yeniden okuyup base64'e cevirmek
    (10+ MB'lik bir PDF icin saniyeler surebilir) cok yavas olur.

    ONEMLI - STATIC SERVING KULLANILMIYOR: Streamlit Cloud'da 'static'
    klasorunu dogrudan URL'den sunma ozelliginin (enableStaticServing)
    her zaman guvenilir calistigi dogrulanamadigi icin (bos/beyaz sayfa
    gozlemlendi), HER PDF base64 gomme yontemiyle gosteriliyor."""
    stat = os.stat(path)
    cache_key = f"{path}|{stat.st_mtime_ns}|{stat.st_size}"
    cache = st.session_state.setdefault("_pdf_cache", {})
    if cache_key not in cache:
        with open(path, "rb") as f:
            data = f.read()
        cache.clear()  # ayni anda birden fazla buyuk PDF'i bellekte tutmayalim
        # NOT: Burada eskiden bir de base64 ("data:") metni uretiliyordu.
        # Artik PDF sayfa sayfa RESIM olarak gosterildigi icin buna hic
        # gerek kalmadi; 11 MB'lik bir dosya icin her seferinde ~15 MB'lik
        # metin uretmek bosuna yavaslik demekti, kaldirildi.
        cache[cache_key] = {"bytes": data}
    return cache[cache_key]


@st.cache_data(show_spinner=False, max_entries=8)
def _pdf_page_count(path, mtime_ns):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(path)
    try:
        return len(doc)
    finally:
        doc.close()


@st.cache_data(show_spinner=False, max_entries=60)
def _pdf_page_image(path, mtime_ns, page_num, dpi=130):
    """Bir PDF sayfasini DUZ BIR RESME (JPEG bayt dizisi) cevirir.

    ONEMLI - NEDEN RESIM: PDF'i tarayiciya gomup gostermenin denenen HER
    YOLU (dogrudan 'data:' adresi iframe'e verilmesi, 'data:' adresinin
    yeni sekmede acilmasi, base64'un JavaScript ile 'Blob'a cevrilip
    iframe'e verilmesi) tarayici guvenlik kisitlamalarina takildi -- Chrome
    hepsini "engellendi" diyerek reddetti, hem masaustunde hem telefonda.
    Duz bir RESIM (JPEG) ise siradan bir fotograf gibi davranir; PDF'e
    ozel HICBIR guvenlik kisitlamasi yoktur ve her cihazda calisir.

    ONEMLI - HIZ: Once bu is pdfplumber ile yapiliyordu; pdfplumber sayfayi
    cizmek icin TUM PDF'i ayristirdigindan tek sayfa ~1.7 saniye suruyordu
    (kullanicinin "sayfalar arasinda hemen geçmiyor" dedigi sorun).
    Dogrudan pypdfium2 ile bu sure olcumle 0.04 saniyeye dustu (~40 kat).
    Dosya her cagrida yeniden aciliyor ama bunun maliyeti olculdu: sadece
    0.005 saniye. Belgeyi acik tutup paylasmak yerine boyle yapiliyor,
    cunku ayni belge nesnesini birden fazla kullanici ayni anda kullanirsa
    (anne-baba ve cocuk ayni anda girerse) cizim islemi guvenli degil.

    st.cache_data: onbellek OTURUMLAR ARASI paylasilir, yani ayni sayfaya
    ikinci kez bakildiginda (ya da baska bir cihazdan ayni denemeye
    girildiginde) sayfa aninda gelir. mtime_ns anahtarin parcasi: deneme
    silinip yeniden eklenirse eski resimler otomatik gecersiz olur."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(path)
    try:
        pil = doc[page_num].render(scale=dpi / 72).to_pil()
    finally:
        doc.close()
    buf = io.BytesIO()
    pil.convert("RGB").save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def show_pdf(path, height=780):
    """PDF'i SAYFA SAYFA RESIM olarak gosterir; ogrenci 'Onceki/Sonraki'
    ile gezinir ya da dogrudan sayfa numarasi girer. Boylece PDF'i ve
    Optik Form'u ayni ekranda, yan yana, disari cikmadan kullanabilir
    (bkz. _pdf_page_image() ustundeki not: bu, denenen onceki uc yontemin
    (data: URI, yeni sekme, Blob) hepsinin tarayici tarafindan engellenmesi
    uzerine bulunan cozum)."""
    try:
        page_count = _pdf_page_count(path, os.stat(path).st_mtime_ns)
    except Exception as e:
        st.error(f"PDF okunamadı: {e}")
        return

    state_key = f"_pdf_page_{path}"
    if state_key not in st.session_state:
        st.session_state[state_key] = 0
    st.session_state[state_key] = max(0, min(st.session_state[state_key], page_count - 1))

    def _nav_row(where):
        """Sayfa gezinme satiri. HEM ustte HEM altta gosteriliyor: ogrenci
        sayfanin sonuna kadar okuduktan sonra bir sonraki sayfaya gecmek
        icin yukari kaydirmak zorunda kalmasin."""
        cur = st.session_state[state_key]
        nav1, nav2, nav3 = st.columns([1, 2, 1])
        with nav1:
            if st.button("◀ Önceki", key=f"prev_{where}_{path}", use_container_width=True,
                         disabled=cur <= 0):
                st.session_state[state_key] = cur - 1
                st.rerun()
        with nav3:
            if st.button("Sonraki ▶", key=f"next_{where}_{path}", use_container_width=True,
                         disabled=cur >= page_count - 1):
                st.session_state[state_key] = cur + 1
                st.rerun()
        with nav2:
            # ÖNEMLİ: key'in içine geçerli sayfa numarası eklendi. Aksi halde
            # Streamlit, "Önceki/Sonraki" ile sayfa değiştiğinde bu widget'ın
            # ESKİ değerini session_state'te tuttuğu için yeni 'value=' parametresini
            # YOK SAYAR -- bu da tıklamayı sessizce geri alırdı. Sayfa değiştikçe
            # anahtar da değiştiği için widget her seferinde temiz/doğru değerle
            # yeniden oluşturuluyor.
            new_page = st.number_input(
                f"Sayfa (1 - {page_count})",
                min_value=1, max_value=page_count, value=cur + 1,
                key=f"_pdf_jump_{where}_{path}_{cur}",
                label_visibility="collapsed",
            )
            if new_page - 1 != cur:
                st.session_state[state_key] = new_page - 1
                st.rerun()

    _nav_row("top")

    try:
        img_bytes = _pdf_page_image(path, os.stat(path).st_mtime_ns, st.session_state[state_key])
    except Exception as e:
        st.error(f"Sayfa gösterilirken hata oluştu: {e}")
        return

    with st.container(height=height, border=True):
        st.image(img_bytes, use_container_width=True)

    st.caption(f"📄 Sayfa {st.session_state[state_key] + 1} / {page_count}")
    _nav_row("bottom")

    with st.expander("⬇️ Kitapçığın tamamını indir"):
        entry = _pdf_cache_entry(path)
        st.download_button(
            "Tüm kitapçığı PDF olarak indir",
            data=entry["bytes"],
            file_name=os.path.basename(path),
            mime="application/pdf",
            use_container_width=True,
            key=f"dl_{path}",
        )


def pdf_link_button(path, label="🔓 Orijinal PDF (cevap anahtarlı)"):
    """Kucuk bir INDIRME dugmesi (yeni sekmede acan bir link DEGIL --
    Chrome buyuk data: linklerini yeni sekmede acmayi engelliyor) --
    admin panelindeki 'Kayitli Denemeler' listesinde goruntu kirliligi
    yaratmamasi icin, sadece bu buton acikca tiklandiginda cagrilir."""
    entry = _pdf_cache_entry(path)
    st.download_button(
        label,
        data=entry["bytes"],
        file_name=os.path.basename(path),
        mime="application/pdf",
        key=f"dl_orig_{path}",
        help="Cevap anahtarını içeren tam PDF — sadece siz görürsünüz, öğrenciyle paylaşmayın.",
    )


def inject_css():
    st.markdown(
        """
        <style>
        /* Streamlit'in kendi ust seridi (Share / GitHub / kalem ikonlari) ve
           alt bilgi satiri gizlenir -- boylece ekran gercek bir uygulama gibi
           gorunur ve PDF'e daha fazla dikey yer kalir.

           ÖNEMLİ - ÖNCEKİ SÜRÜMDEKİ HATA (tablette menü kayboluyordu):
           Burada önce 'header', sonra da üst araç çubuğunun TAMAMI
           (stToolbar) gizlenmişti. Ama kenar çubuğu (giriş menüsü)
           kapatılınca onu geri AÇAN "»" düğmesi tam olarak o araç
           çubuğunun İÇİNDE duruyor; üst öğe gizlenince o düğme de sıfır
           boyuta düşüyor ve menüyü geri açmanın HİÇBİR yolu kalmıyordu.
           (Tarayıcıda ölçüldü: düğmenin genişliği ve yüksekliği 0 çıkıyordu.)
           Bu yüzden artık araç çubuğu bir bütün olarak gizlenmiyor; sadece
           tek tek gereksiz düğmeler gizleniyor ve açma düğmesi açıkça
           görünür kılınıyor. */
        footer {visibility: hidden;}
        header[data-testid="stHeader"] {background: transparent !important;}
        div[data-testid="stDecoration"] {display: none !important;}
        div[data-testid="stStatusWidget"] {display: none !important;}
        /* Gereksiz düğmeler: "Deploy", üç nokta menüsü ve Streamlit Cloud'un
           "Manage app" / hesap rozeti (bunlar Streamlit hesabına götürüyordu,
           öğrencinin görmesine gerek yok). */
        button[data-testid="stMainMenuButton"],
        button[data-testid="stBaseButton-header"],
        div[data-testid="stAppDeployButton"],
        div[data-testid="stAppViewerBadge"],
        div[data-testid="manage-app-button"],
        div[class^="viewerBadge"], div[class*=" viewerBadge"],
        a[href*="streamlit.io/cloud"] {display: none !important;}
        /* Menüyü geri açan düğme HER ZAMAN görünür ve rahat tıklanır olsun. */
        button[data-testid="stExpandSidebarButton"] {
            display: flex !important; visibility: visible !important;
            opacity: 1 !important; z-index: 999999 !important;
            background: #2563EB !important; color: #ffffff !important;
            border-radius: 10px !important; padding: 4px 8px !important;
        }
        .block-container {padding-top: 2.2rem; padding-left: 2rem; padding-right: 2rem; max-width: 100%;}
        div[data-baseweb="tab-list"] {
            overflow-x: visible !important;
            flex-wrap: wrap;
        }
        div[data-baseweb="tab-list"] button[data-baseweb="tab"] {
            height: auto;
            white-space: normal;
        }
        div[data-testid="stMetric"] {
            background: #F1F5F9; border-radius: 14px; padding: 14px 10px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        }
        .stButton>button, .stFormSubmitButton>button {
            border-radius: 10px; padding: 0.6rem 1.2rem; font-weight: 600;
        }
        div[role="radiogroup"] > label {
            padding: 2px 8px; border-radius: 8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def build_generic_structure(subject_rows):
    """subject_rows: [("Türkçe", 10), ...] -> tek bölümlü ('Genel') structure sözlüğü."""
    return {"Genel": {name: {"count": count, "coef": 1} for name, count in subject_rows}}


# ---------------------------------------------------------------- app shell

# initial_sidebar_state="expanded": tablette/telefonda sayfa her acildiginda
# giris menusu ACIK gelsin (kullanici menuyu bulamayip giris yapamaz duruma
# dusmesin).
st.set_page_config(
    page_title=config.APP_TITLE, layout="wide", page_icon="📚",
    initial_sidebar_state="expanded",
)
inject_css()

if "is_admin" not in st.session_state:
    st.session_state.is_admin = False
if "attempt" not in st.session_state:
    st.session_state.attempt = {}  # exam_id -> deneme sayacı (sıfırla için)
if "student_name" not in st.session_state:
    st.session_state.student_name = ""  # login sonrası: kullanıcı adı (sonuçların anahtarı)
if "student_display_name" not in st.session_state:
    st.session_state.student_display_name = ""  # login sonrası: ekranda gösterilecek ad

# ---------------- Oturum zaman aşımı ----------------
# Bir cihaz (tablet/bilgisayar) uzun süre açık/etkin bırakılırsa, güvenlik
# için bir süre sonra oturumu otomatik kapatıp tekrar şifre istiyoruz.
# Cevaplar ise DB'de (in_progress tablosunda) zaten saklandığı için, tekrar
# giriş yapıldığında öğrenci kaldığı yerden devam edebiliyor -- hiçbir
# cevap kaybolmaz, sadece giriş oturumu süresi doluyor.
SESSION_TIMEOUT_SECONDS = 60 * 60  # 1 saat işlem yapılmazsa otomatik çıkış

if "_last_activity" not in st.session_state:
    st.session_state._last_activity = datetime.now()

_idle_seconds = (datetime.now() - st.session_state._last_activity).total_seconds()
if _idle_seconds > SESSION_TIMEOUT_SECONDS and (st.session_state.is_admin or st.session_state.student_name):
    st.session_state.is_admin = False
    st.session_state.student_name = ""
    st.session_state.student_display_name = ""
    st.session_state["_timeout_notice"] = True

st.session_state._last_activity = datetime.now()

def render_student_login_form():
    # Admin kullanıcı adı kutusu gibi, kayıtlı TEK bir öğrenci varsa (tipik
    # kullanım: tek çocuk) kullanıcı adını otomatik dolduruyoruz; birden
    # fazla öğrenci kayıtlıysa hangisi olduğunu tahmin edemeyeceğimiz için
    # boş bırakıyoruz.
    _students = db.get_students()
    _default_user = _students[0]["username"] if len(_students) == 1 else ""
    login_user = st.text_input("Kullanıcı Adı", value=_default_user, key="login_user")
    login_pw = st.text_input("Şifre", type="password", key="login_pw")
    if st.button("Giriş Yap", key="student_login_btn", type="primary", use_container_width=True):
        student = db.verify_student(login_user, login_pw)
        if student:
            st.session_state.student_name = student["username"]
            st.session_state.student_display_name = student["display_name"]
            st.rerun()
        else:
            st.error("Kullanıcı adı veya şifre yanlış.")

    with st.expander("🆕 Hesabınız yok mu? Kayıt olun"):
        reg_display = st.text_input("Adınız Soyadınız", key="reg_display")
        reg_user = st.text_input("Kullanıcı Adı", key="reg_user", help="Boşluksuz, Türkçe karaktersiz olması önerilir.")
        reg_pw = st.text_input("Şifre", type="password", key="reg_pw")
        reg_pw2 = st.text_input("Şifre (tekrar)", type="password", key="reg_pw2")
        if st.button("Hesap Oluştur", key="register_btn", use_container_width=True):
            if reg_pw != reg_pw2:
                st.error("Girdiğiniz şifreler birbiriyle eşleşmiyor.")
            elif len(reg_pw) < 4:
                st.error("Şifre en az 4 karakter olmalı.")
            else:
                ok, msg = db.create_student(reg_user, reg_display, reg_pw)
                if ok:
                    st.success(msg + " Şimdi yukarıdaki kutulardan giriş yapabilirsiniz.")
                else:
                    st.error(msg)


def render_admin_login_form():
    admin_user = st.text_input("Kullanıcı Adı", key="admin_user", value=config.ADMIN_USERNAME)
    admin_pw = st.text_input("Şifre", type="password", key="admin_pw")
    if st.button("Giriş Yap", key="admin_login_btn", type="primary", use_container_width=True):
        admin = db.verify_admin(admin_user, admin_pw)
        if admin:
            st.session_state.is_admin = True
            st.session_state.admin_username = admin["username"]
            st.rerun()
        elif not admin_pw:
            st.error("Şifre alanı boş. Önce şifreyi girin.")
        else:
            st.error("Kullanıcı adı veya şifre yanlış.")


with st.sidebar:
    st.markdown(f"### 📚 {config.APP_TITLE}")

    if st.session_state.pop("_timeout_notice", False):
        st.warning(
            f"Uzun süre işlem yapılmadığı için oturumunuz kapatıldı (yaklaşık "
            f"{SESSION_TIMEOUT_SECONDS // 60} dakika). Cevaplarınız kaydedildi, "
            f"tekrar giriş yapıp kaldığınız yerden devam edebilirsiniz."
        )

    if not st.session_state.student_name and not st.session_state.is_admin:
        login_type = st.selectbox("Giriş türü", ["Öğrenci", "Yönetici"], key="login_type")
        st.divider()
        if login_type == "Öğrenci":
            st.subheader("👤 Öğrenci Girişi")
            render_student_login_form()
        else:
            st.subheader("⚙️ Yönetici Girişi")
            render_admin_login_form()
    elif st.session_state.student_name:
        # Sadece öğrenci girişi yapılmış: kafa karışıklığını önlemek için
        # burada ayrıca "Yönetici Girişi" seçeneği GÖSTERİLMEZ. Yönetici
        # girişi yapmak isteyen kişi önce çıkış yapıp Yönetici'yi seçmelidir.
        st.success(f"Hoş geldin, {st.session_state.student_display_name}! 👋")
        if st.session_state.is_admin:
            # Yönetici, yukarıdaki "öğrenci olarak devam et" ile bu moda
            # geçmiş olabilir; hâlâ yönetici olduğunu görebilmeli.
            st.caption("⚙️ Aynı zamanda yönetici olarak giriş yaptınız.")
        if st.button("Çıkış Yap", key="student_logout_btn", use_container_width=True):
            st.session_state.student_name = ""
            st.session_state.student_display_name = ""
            st.rerun()
        if st.session_state.is_admin and st.button(
            "Yönetici oturumunu da kapat", key="admin_logout_btn2", use_container_width=True
        ):
            st.session_state.is_admin = False
            st.session_state.student_name = ""
            st.session_state.student_display_name = ""
            st.rerun()
    else:
        # Sadece yönetici girişi yapılmış.
        st.success("Yönetici olarak giriş yaptınız.")
        if st.button("Çıkış Yap", key="admin_logout_btn", use_container_width=True):
            st.session_state.is_admin = False
            st.rerun()

        # ÖNEMLİ - ÖNCEKİ SÜRÜMDEKİ HATA: Burada öğrenci seçme imkânı hiç
        # yoktu. Yönetici olarak giriş yapan kişi "Sınav Çöz" sekmesinde
        # soruları işaretleyebiliyor, ama "Sınavı Bitir" dediğinde sistem
        # "önce giriş yapın" diyordu -- ve giriş yapacak bir yer de
        # olmadığı için sınav ASLA bitirilemiyordu. Yönetici zaten şifresiyle
        # kimliğini doğrulamış olduğu için, burada kimin adına çözüleceğini
        # tek bir kutudan seçmesi yeterli (ayrıca şifre sorulmuyor).
        _students = db.get_students()
        if _students:
            st.divider()
            st.caption("Kendiniz deneme çözmek/test etmek isterseniz, kimin adına çözüleceğini seçin:")
            _opts = [None] + [s["username"] for s in _students]
            _labels = {s["username"]: s["display_name"] for s in _students}
            _chosen = st.selectbox(
                "Öğrenci olarak devam et",
                _opts,
                format_func=lambda u: "— Öğrenci seçilmedi —" if u is None else _labels.get(u, u),
                key="admin_as_student",
            )
            if _chosen and st.button("Bu öğrenci olarak devam et", key="admin_as_student_btn",
                                     use_container_width=True):
                st.session_state.student_name = _chosen
                st.session_state.student_display_name = _labels.get(_chosen, _chosen)
                st.rerun()
        else:
            st.divider()
            st.caption(
                "Henüz kayıtlı öğrenci yok. Sınav sonuçlarının kaydedilebilmesi için "
                "Admin Paneli → Öğrenciler bölümünden bir öğrenci ekleyin."
            )

# ÖNEMLİ - GÜVENLİK/KARŞILAMA EKRANI: Daha önce uygulama açılır açılmaz,
# hiç giriş yapılmadan doğrudan "Sınav Çöz" sekmesi geliyordu; yani adresi
# bilen herkes denemelere ulaşabiliyordu. Artık giriş yapılmadıysa sadece
# aşağıdaki karşılama ekranı gösterilir ve script burada durur.
if not st.session_state.student_name and not st.session_state.is_admin:
    st.markdown(
        f"""
        <div style="text-align:center; padding:3.5rem 1rem 2rem 1rem;">
          <div style="font-size:4.5rem; line-height:1;">📚</div>
          <h1 style="margin:0.6rem 0 0.2rem 0; color:#1E3A8A;">{config.APP_TITLE}</h1>
          <p style="font-size:1.15rem; color:#475569; margin-top:0.4rem;">
            Geçmiş yıl LGS denemelerini çöz, netlerini anında gör, gelişimini takip et.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    # ---- Sayaçlar: sistemde ne var, ne kadarı çözülmüş ----
    _ist = db.genel_istatistikler()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("📚 Toplam Deneme/Test", _ist["toplam_deneme"])
    m2.metric("❓ Toplam Soru", _ist["toplam_soru"])
    m3.metric("✅ Çözülen Sınav", _ist["toplam_sonuc"])
    m4.metric("👨‍🎓 Kayıtlı Öğrenci", _ist["ogrenci_sayisi"])

    if _ist["kategoriler"]:
        st.markdown("##### 📂 Bölümler ve içerikleri")
        _kat_df = pd.DataFrame(
            [
                {
                    "Bölüm": k,
                    "Deneme/Test Sayısı": v["deneme"],
                    "Toplam Soru": v["soru"],
                    "Çözülen": v["cozulen"],
                }
                for k, v in _ist["kategoriler"].items()
            ]
        )
        st.dataframe(_kat_df, use_container_width=True, hide_index=True)

    st.divider()
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown(
            """
### 📖 Bu program nedir?

Bu platform, **8. sınıf LGS** ve **bursluluk (İOKBS)** sınavlarına hazırlanan bir öğrencinin
geçmiş yıl sorularını ve soru bankası testlerini **tablet veya bilgisayardan çözüp**
sonucunu anında görebilmesi için hazırlandı. Kâğıt çıktı almaya, cevap anahtarını
elle kontrol etmeye gerek yok.

**Sınav çözerken**

- Kitapçık ekranın solunda **sayfa sayfa** açılır; sağında optik form durur. İkisi yan yana,
  aynı ekranda — dışarıda başka bir program açmaya gerek yok.
- "Önceki / Sonraki" düğmeleri hem üstte hem altta; doğrudan sayfa numarası da yazılabilir.
- İşaretlenen her cevap **anında kaydedilir**. İnternet kesilse, tablet kapansa bile
  tekrar girildiğinde **kaldığı yerden** devam edilir.
- Üstteki sayaç kaç soru işaretlendiğini gösterir (*"12 / 20 soru işaretlendi"*).
- Boş sayfalar kitapçıktan otomatik atılır; cevap anahtarı sayfaları öğrenciye **gösterilmez**.

**Sınav bittikten sonra**

- Ders ders **doğru / yanlış / boş** sayısı ve net hesabı (3 yanlış 1 doğruyu götürür).
- Hangi soruyu yanlış yaptığı, **ne işaretlediği ve doğrusunun ne olduğu** tek tek listelenir.
- Gelişim grafiği ile netlerin zamanla nasıl değiştiği görülür.
- Aynı deneme istenildiği kadar tekrar çözülebilir; **her denemenin kaydı ayrı ayrı** saklanır.

**Yönetici (veli) tarafında**

- Geçmiş yıl LGS kitapçıkları **tek tuşla, resmi MEB arşivinden** indirilir (2018-2025).
- Bir soru bankası PDF'i yüklenince kitap taranır, **her test ayrı ayrı** sisteme eklenir.
- Kendi PDF'lerinizi de yükleyebilir, cevap anahtarını otomatik okutabilirsiniz.
- Öğrenci ekleme/silme, şifre sıfırlama, sonuç silme ve tüm raporlar buradadır.
- Cevap anahtarlı **orijinal kitapçığı sadece yönetici** görebilir.

**Güvenlik ve gizlilik**

- Şifre girilmeden hiçbir teste ulaşılamaz.
- Cevap anahtarlı dosyalar, adresi tahmin edilerek açılamayacak korumalı bir klasörde tutulur.
- 1 saat işlem yapılmazsa oturum kendiliğinden kapanır; ilerleme kaybolmaz.
            """
        )
    with c2:
        st.info(
            "👈 Başlamak için **soldaki menüden giriş yapın.**\n\n"
            "Menüyü göremiyorsanız, sol üstteki **»** düğmesine dokunun.",
            icon="🔐",
        )
        st.markdown(
            """
            | | |
            |---|---|
            | 📝 | Cevaplar **otomatik kaydedilir** |
            | ⏸️ | **Kaldığın yerden** devam edebilirsin |
            | 📄 | PDF ve optik form **yan yana** |
            | 🔢 | **Soru sayacı** ile takip |
            | 📊 | Net, doğru/yanlış **dökümü** |
            | 📈 | **Gelişim grafiği** |
            | 🔁 | Aynı testi **tekrar tekrar** çöz |
            | 🔒 | Cevap anahtarı **öğrenciye kapalı** |
            """
        )
    st.stop()

tab_names = ["📱 Sınav Çöz", "📊 Gelişim Raporum"]
if st.session_state.is_admin:
    tab_names.append("⚙️ Admin Paneli")
tabs = st.tabs(tab_names)


# ================================================================= TAB: SINAV ÇÖZ
with tabs[0]:
    st.markdown("### 📱 Sınav Çöz")

    # ---- Sayaçlar: sistemde ne var, öğrenci ne kadarını çözmüş ----
    _ist = db.genel_istatistikler()
    _benim = (
        len(db.get_results(student_name=st.session_state.student_name))
        if st.session_state.student_name else 0
    )
    _s1, _s2, _s3, _s4 = st.columns(4)
    _s1.metric("📚 Toplam Deneme/Test", _ist["toplam_deneme"])
    _s2.metric("❓ Toplam Soru", _ist["toplam_soru"])
    _s3.metric("✅ Senin Çözdüğün", _benim)
    _s4.metric("📈 Tüm Çözülenler", _ist["toplam_sonuc"])
    with st.expander("📂 Bölüm bölüm dağılım"):
        if _ist["kategoriler"]:
            st.dataframe(
                pd.DataFrame([
                    {
                        "Bölüm": k,
                        "Deneme/Test": v["deneme"],
                        "Toplam Soru": v["soru"],
                        "Çözülme Sayısı": v["cozulen"],
                    }
                    for k, v in _ist["kategoriler"].items()
                ]),
                use_container_width=True, hide_index=True,
            )
        else:
            st.caption("Henüz hiç deneme eklenmemiş.")

    st.caption("Aşağıdan bir kategori ve deneme seçerek başlayın.")
    # ÖNEMLİ - "GİRİŞ YAPINCA HER ŞEY SIFIRLANIYOR" HATASININ KÖK NEDENİ:
    # Streamlit, bir çalışma turunda EKRANA ÇİZİLMEYEN kutuların hafızasını
    # siler. Giriş/çıkış düğmeleri kenar çubuğunda st.rerun() çağırdığı için
    # sayfa tam o noktada yarıda kesiliyor, aşağıdaki seçim kutuları o turda
    # hiç çizilmiyor ve Streamlit "kullanılmıyor" sanıp seçimleri siliyordu.
    # Sonuç: giriş yapıldığı anda seçili deneme kayboluyor, PDF ve işaretlenen
    # cevaplar ekrandan siliniyordu. Çözüm: seçimleri, kutunun kendi
    # anahtarının YANINDA ayrı birer "gölge" kayıtta da tutmak -- bu kayıtlar
    # kutu olmadığı için asla silinmiyor ve seçim buradan geri yükleniyor.
    categories = db.get_categories()
    col_a, col_b = st.columns([1, 2])
    with col_a:
        _cat_prev = st.session_state.get("_solve_cat_val")
        _cat_index = categories.index(_cat_prev) if _cat_prev in categories else 0
        selected_cat = st.selectbox("Kategori", categories, index=_cat_index, key="solve_cat")
    st.session_state["_solve_cat_val"] = selected_cat
    exams = db.get_exams(category=selected_cat)
    with col_b:
        if exams:
            exam_titles = {e["id"]: e["title"] for e in exams}
            # Sayfa açılır açılmaz otomatik olarak bir sınavın içine
            # düşülmesin diye başta hiçbir deneme seçili gelmiyor; öğrenci
            # bilinçli olarak bir deneme seçmeden PDF/Optik Form görünmüyor.
            options = [None] + list(exam_titles.keys())
            _ex_prev = st.session_state.get("_solve_exam_val")
            _ex_index = options.index(_ex_prev) if _ex_prev in options else 0
            selected_exam_id = st.selectbox(
                "Çözmek İstediğiniz Denemeyi Seçin",
                options,
                index=_ex_index,
                format_func=lambda x: "— Bir deneme seçin —" if x is None else exam_titles[x],
                key="solve_exam",
            )
            st.session_state["_solve_exam_val"] = selected_exam_id
        else:
            selected_exam_id = None
            st.info("Bu kategoride henüz bir deneme yok. Admin panelinden ekleyin.")

    if selected_exam_id:
        exam = db.get_exam(selected_exam_id)
        structure = exam["structure"]
        answer_key = exam["answer_key"]
        student_name = st.session_state.student_name

        # Bir öğrenci daha önce (belki de başka bir oturumda / cihazda) bu
        # denemeyi çözmeye başlamış ya da bitirmişse, deneme numarasını
        # veritabanından öğreniyoruz -- böylece sayfa yeniden açıldığında
        # sıfırdan boş bir form yerine kaldığı yer / sonuç gösterilir.
        if selected_exam_id not in st.session_state.attempt:
            st.session_state.attempt[selected_exam_id] = db.get_current_attempt_no(
                selected_exam_id, student_name
            )
        attempt_no = st.session_state.attempt[selected_exam_id]

        existing_result = db.get_result_for_attempt(selected_exam_id, student_name, attempt_no)

        col_pdf, col_form = st.columns([6, 4])

        with col_pdf:
            st.subheader(exam["title"])
            pdf_path = exam["pdf_path"]
            if not os.path.exists(pdf_path):
                st.error(
                    "PDF dosyası sunucuda bulunamadı. Bu deneme muhtemelen bir önceki "
                    "yayına almadan (deploy) kalan bir kayıt; admin panelinden silip "
                    "yeniden eklemeniz gerekebilir."
                )
            elif os.path.getsize(pdf_path) == 0:
                st.error("PDF dosyası boş (0 byte) kaydedilmiş. Denemeyi silip yeniden eklemeniz gerekiyor.")
            else:
                try:
                    show_pdf(pdf_path)
                except Exception as e:
                    st.error(f"PDF gösterilirken bir hata oluştu: {e}")

        with col_form:
            if existing_result:
                # ---- Bu deneme numarası için sınav zaten bitirilmiş: sonucu göster ----
                st.success("✅ Bu denemeyi zaten çözdünüz. Sonuçlarınız:")
                per_subject = existing_result["per_subject"]
                total_net = existing_result["total_net"]
                weighted_score = existing_result["weighted_score"]
                cols = st.columns(len(per_subject))
                for c, (subj, r) in zip(cols, per_subject.items()):
                    c.metric(subj, f"Net: {r['net']}", f"D:{r['dogru']} Y:{r['yanlis']} B:{r['bos']}")
                m1, m2 = st.columns(2)
                m1.metric("Toplam Net", total_net)
                if weighted_score is not None:
                    m2.metric("Tahmini Ağırlıklı Puan Göstergesi", weighted_score)
                    st.caption(
                        "Bu, ders katsayılarıyla ağırlıklandırılmış bir GÖSTERGEdir; "
                        "MEB'in Türkiye geneli istatistiklerine dayanan resmi 100-500 LGS "
                        "puanı değildir."
                    )
                st.divider()
                if st.button(
                    "🔄 Yeniden Çöz (yeni bir deneme başlat)",
                    key=f"retry_{selected_exam_id}_{attempt_no}",
                    use_container_width=True,
                ):
                    st.session_state.attempt[selected_exam_id] = attempt_no + 1
                    st.rerun()
            else:
                # ---- Bu deneme numarası henüz bitirilmemiş: formu göster ----
                st.subheader("📝 Optik Form")
                if student_name:
                    st.caption(
                        "İşaretlediğiniz cevaplar otomatik kaydedilir; sayfa kapanırsa veya "
                        "internet kesilirse tekrar açtığınızda kaldığınız yerden devam edebilirsiniz."
                    )
                else:
                    st.warning(
                        "Şu an kimse giriş yapmadığı için sonuç KAYDEDİLEMEZ. Soldaki menüden "
                        "giriş yapın; işaretlediğiniz cevaplar kaybolmaz, giriş yaptıktan sonra "
                        "olduğu gibi durmaya devam eder."
                    )

                # ÖNEMLİ - CEVAP KAYBI: Öğrenci giriş yapmadan işaretlediği
                # cevaplar veritabanına yazılamıyordu ve giriş yapıldığı anda
                # kayboluyordu. Artık cevaplar giriş durumundan BAĞIMSIZ olarak
                # her durumda oturum belleğinde de tutuluyor; giriş yapılınca
                # oradan geri yükleniyor.
                buf_key = f"_ans_buf_{selected_exam_id}_{attempt_no}"
                saved = (
                    st.session_state.get(buf_key)
                    or db.load_progress(selected_exam_id, student_name, attempt_no)
                    or {}
                )

                all_subjects = [
                    (section, subject)
                    for section, subjects in structure.items()
                    for subject in subjects
                ]
                user_answers = {section: {} for section in structure}
                options = ["A", "B", "C", "D", "Boş"]

                # PDF görüntüleyici ile aynı yükseklikte, kaydırılabilir bir
                # kutu içinde gösteriliyor -- böylece PDF bittiğinde Optik
                # Form aşağıya doğru uzayıp gitmiyor, ikisi de aynı boyda
                # kalıp kendi içinde kayıyor.
                with st.container(height=780, border=True):
                    subject_tabs = st.tabs([s for _, s in all_subjects])
                    for (section, subject), stab in zip(all_subjects, subject_tabs):
                        with stab:
                            _meta = structure[section][subject]
                            count = _meta["count"]
                            # ÖNEMLİ - OPTİK FORM PDF İLE AYNI NUMARALARI
                            # GÖSTERİR: Soru bankasından alınan bazı testlerin
                            # kitaptaki ilk sayfası olmadığı için sorular 1'den
                            # değil, örneğin 4'ten başlar. Böyle testlerde
                            # "numbers" alanında sayfadaki GERÇEK soru
                            # numaraları durur ve optik form da "Soru 4, 5, 6, 7"
                            # diye devam eder -- yoksa çocuk PDF'te 4. soruyu
                            # okurken formda 1. soruyu işaretler ve her şey
                            # kayar. "numbers" yoksa normal 1, 2, 3... kullanılır.
                            numaralar = _meta.get("numbers") or list(range(1, count + 1))
                            saved_subject = saved.get(section, {}).get(subject, [])
                            if numaralar and numaralar[0] != 1:
                                st.caption(
                                    f"ℹ️ Bu testin soruları kitapçıkta **{numaralar[0]}. sorudan** "
                                    f"başlıyor; aşağıdaki numaralar PDF'tekilerle birebir aynıdır."
                                )
                            answers = []
                            for _sira, _soru_no in enumerate(numaralar):
                                prev = saved_subject[_sira] if _sira < len(saved_subject) else "Boş"
                                default_index = options.index(prev) if prev in options else 4
                                ans = st.radio(
                                    f"{subject} - Soru {_soru_no}",
                                    options,
                                    index=default_index,
                                    horizontal=True,
                                    key=f"ans_{selected_exam_id}_{attempt_no}_{subject}_{_soru_no}",
                                )
                                answers.append(ans)
                            user_answers[section][subject] = answers

                # Giriş yapılmış olsun ya da olmasın, cevaplar her zaman
                # oturum belleğine yazılır (bkz. yukarıdaki buf_key notu).
                st.session_state[buf_key] = user_answers
                if student_name:
                    db.save_progress(selected_exam_id, student_name, attempt_no, user_answers)

                # ---- İlerleme sayacı: kaç soru işaretlendi / toplam kaç soru ----
                _total_q = sum(
                    structure[sec][sub]["count"] for sec, sub in all_subjects
                )
                _done_q = sum(
                    1
                    for sec, sub in all_subjects
                    for a in user_answers[sec][sub]
                    if a != "Boş"
                )
                _pct = _done_q / _total_q if _total_q else 0
                st.progress(
                    _pct,
                    text=f"**{_done_q} / {_total_q} soru işaretlendi**  ·  %{round(_pct * 100)}",
                )
                if _done_q < _total_q:
                    st.caption(f"Kalan: {_total_q - _done_q} soru")

                submitted = st.button(
                    "✅ Sınavı Bitir ve Puanla",
                    type="primary",
                    use_container_width=True,
                    key=f"submit_{selected_exam_id}_{attempt_no}",
                )

                if submitted and not student_name:
                    st.error(
                        "Sonuç kaydedilemedi: önce soldaki menüden giriş yapmanız gerekiyor. "
                        "**İşaretlediğiniz cevaplar duruyor** — giriş yaptıktan sonra bu düğmeye "
                        "tekrar basmanız yeterli."
                    )
                elif submitted:
                    per_subject, total_net, weighted_score = scoring.score_exam(
                        user_answers, answer_key, structure
                    )
                    answers_detail = scoring.build_answer_detail(user_answers, answer_key, structure)
                    db.add_result(
                        selected_exam_id, student_name, per_subject, total_net,
                        weighted_score, answers_detail=answers_detail, attempt_no=attempt_no,
                    )
                    db.clear_progress(selected_exam_id, student_name, attempt_no)
                    st.session_state.pop(buf_key, None)
                    st.success("Sınav tamamlandı! Sonuçlarınız kaydedildi.")
                    st.rerun()


# ================================================================= TAB: GELİŞİM RAPORU
with tabs[1]:
    st.subheader("📊 Gelişim Raporum")
    student = st.session_state.student_name
    if not student:
        st.info("Sonuçlarınızı görmek için soldaki menüden giriş yapın.")
    else:
        results = db.get_results(student_name=student)
        if not results:
            st.info("Henüz çözülmüş bir sınav yok.")
        else:
            df = pd.DataFrame(
                [
                    {
                        "Tarih": r["created_at"],
                        "Sınav": r["exam_title"],
                        "Kategori": r["category"],
                        "Toplam Net": r["total_net"],
                        "Ağırlıklı Puan": r["weighted_score"],
                    }
                    for r in results
                ]
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
            chart_df = df[["Tarih", "Toplam Net"]].set_index("Tarih").sort_index()
            st.line_chart(chart_df)

            # ---- Her sınavın üstüne basınca açılan detay penceresi ----
            # (Aynı dökum admin tarafındaki "Öğrenci Raporları" bölümünde de var;
            #  öğrenci kendi sayfasında SADECE görebilir, silemez.)
            st.divider()
            st.markdown("### 📋 Sınav Detayları")
            st.caption("Bir sınavın üzerine dokunarak kaç doğru, kaç yanlış, kaç boş yaptığını görebilirsin.")
            for r in results:
                _title = (
                    f"📝 {r['exam_title']}  ·  {r['created_at']}  ·  Net: {r['total_net']}"
                )
                with st.expander(_title):
                    st.markdown(f"**Sınav:** {r['exam_title']}  ·  *{r['category']}*")
                    per_subject = r["per_subject"]
                    _t_d = sum(v["dogru"] for v in per_subject.values())
                    _t_y = sum(v["yanlis"] for v in per_subject.values())
                    _t_b = sum(v["bos"] for v in per_subject.values())
                    a1, a2, a3, a4 = st.columns(4)
                    a1.metric("✅ Doğru", _t_d)
                    a2.metric("❌ Yanlış", _t_y)
                    a3.metric("⬜ Boş", _t_b)
                    a4.metric("📊 Toplam Net", r["total_net"])
                    if r.get("weighted_score") is not None:
                        st.caption(f"Tahmini ağırlıklı puan göstergesi: {r['weighted_score']}")
                    st.markdown("**Ders bazında**")
                    _cols = st.columns(len(per_subject))
                    for _c, (_subj, _res) in zip(_cols, per_subject.items()):
                        _c.metric(
                            _subj, f"Net: {_res['net']}",
                            f"D:{_res['dogru']} Y:{_res['yanlis']} B:{_res['bos']}",
                        )
                    _detail = r.get("answers_detail")
                    if _detail:
                        st.markdown("**Yanlış ve boş bıraktığın sorular**")
                        for _section, _subjects in _detail.items():
                            for _subject, _rows in _subjects.items():
                                _wrong = [x for x in _rows if x["durum"] != "dogru"]
                                if not _wrong:
                                    st.caption(f"{_subject}: tüm sorular doğru! 🎉")
                                    continue
                                _dfd = pd.DataFrame(_wrong)
                                _dfd["durum"] = _dfd["durum"].map(
                                    {"yanlis": "❌ Yanlış", "bos": "⬜ Boş"}
                                )
                                st.caption(f"{_subject}")
                                st.dataframe(_dfd, use_container_width=True, hide_index=True)


# ================================================================= TAB: ADMIN
if st.session_state.is_admin:
    with tabs[2]:
        st.subheader("⚙️ Admin Paneli")

        # Bir işlem (örn. deneme ekleme) bittiğinde st.rerun() çağırıyoruz ki
        # "Sınav Çöz" sekmesi de hemen güncellensin -- ama bu, o an ekranda
        # olan yeşil "başarılı" mesajının göz açıp kapayana kadar kaybolup
        # gitmesine sebep oluyordu. Bunun yerine mesajı burada, oturumda
        # saklayıp bir SONRAKI (rerun sonrası) sayfa yüklemesinde gösteriyoruz
        # -- böylece siz bir sonraki işleme geçene kadar ekranda kalıyor.
        _flashes = st.session_state.pop("_admin_flash", None)
        if _flashes:
            if isinstance(_flashes, tuple):
                _flashes = [_flashes]
            for _kind, _text in _flashes:
                (st.success if _kind == "success" else st.error)(_text)

        admin_section = st.radio(
            "İşlem seçin",
            [
                "8. Sınıf LGS Denemesi Ekle",
                "📚 Soru Bankasını Test Test Ayır",
                "Diğer Kategori / Soru Bankası Ekle (Manuel)",
                "Otomatik İndirme (Resmi EBA Arşivi)",
                "🎓 Bursluluk (İOKBS) Otomatik İndir",
                "URL'den PDF İndir",
                "Google Drive'dan İçe Aktar",
                "Kayıtlı Denemeler",
                "Öğrenci Hesapları (Ekle/Sil/Şifre)",
                "Öğrenci Raporları",
                "Hesap Ayarları",
            ],
            horizontal=True,
        )
        st.divider()

        # ---------------- Soru bankasını test test ayır ----------------
        if admin_section == "📚 Soru Bankasını Test Test Ayır":
            st.markdown(
                "Çok dersli bir **soru bankası PDF'i** yükleyin. Sistem kitabı tarayıp "
                "içindeki her testi (**Türkçe - Sözcükte Anlam Test 3** gibi) ayrı ayrı bulur "
                "ve kitabın sonundaki cevap anahtarıyla eşleştirir. Böylece çocuğunuz 50 soruluk "
                "koca bir deneme yerine, **6-8 soruluk kısa konu testleri** çözebilir."
            )
            qb_file = st.file_uploader("Soru bankası PDF'i", type=["pdf"], key="qb_pdf")
            qb_path_state = "_qb_path"

            if qb_file is not None and st.button("📖 Kitabı Tara", type="primary"):
                _qb_path = os.path.join(PRIVATE_DIR, "_soru_bankasi.pdf")
                with open(_qb_path, "wb") as f:
                    f.write(qb_file.getbuffer())
                with st.spinner("Kitap taranıyor, testler ve cevap anahtarı bulunuyor..."):
                    try:
                        _testler, _anahtar, _uyarilar = soru_bankasi.testleri_bul(_qb_path)
                    except Exception as e:
                        _testler, _anahtar, _uyarilar = [], {}, [f"Kitap okunamadı: {e}"]
                st.session_state[qb_path_state] = _qb_path
                st.session_state["_qb_testler"] = _testler
                st.session_state["_qb_uyarilar"] = _uyarilar
                st.rerun()

            _testler = st.session_state.get("_qb_testler")
            if _testler:
                _eklenebilir = [t for t in _testler if t.get("cevaplar") and t.get("numaralar")]
                st.success(
                    f"Kitapta **{len(_testler)} test** bulundu; bunlardan "
                    f"**{len(_eklenebilir)} tanesi** eklenebilir durumda."
                )
                _kirpik = [t for t in _eklenebilir if (t.get("numaralar") or [1])[0] != 1]
                if _kirpik:
                    st.info(
                        f"ℹ️ Bu PDF bir **tanıtım/örnek sürüm** gibi görünüyor: {len(_kirpik)} testin "
                        "kitaptaki ilk sayfası dosyada yok, o testler ortadan (örneğin 4. sorudan) "
                        "başlıyor. **Sorun değil** — sistem her testin sayfada gerçekten basılı olan "
                        "sorularını bulur ve optik formu tam o numaralarla oluşturur, yani PDF'te "
                        "4. soruyu okuyan çocuk formda da 4. soruyu işaretler. Sadece o testlerin "
                        "baştaki soruları hiç sorulmaz. Kitabın tam sürümünü bulursanız aynı "
                        "işlem bütün soruları ekler."
                    )
                for _u in st.session_state.get("_qb_uyarilar", []):
                    st.warning(_u)
                if not _eklenebilir:
                    st.error("Cevap anahtarı okunabilen test yok, ekleme yapılamıyor.")
                else:
                    _dersler = sorted({t["ders"] for t in _eklenebilir})
                    _secili_ders = st.selectbox("Ders", _dersler, key="qb_ders")
                    _bu_ders = [t for t in _eklenebilir if t["ders"] == _secili_ders]

                    def _qb_etiket(t):
                        _nums = t.get("numaralar") or []
                        if _nums and _nums[0] != 1:
                            _ek = f"{len(_nums)} soru: kitapta {_nums[0]}-{_nums[-1]}"
                        else:
                            _ek = f"{len(_nums)} soru"
                        return f"Test {t['test_no']} · {t['konu']} ({_ek})"

                    _secilenler = st.multiselect(
                        "Eklenecek testler",
                        _bu_ders,
                        default=_bu_ders,
                        format_func=_qb_etiket,
                        key="qb_secim",
                    )
                    st.caption(
                        f"{len(_secilenler)} test seçili. Her test ayrı bir deneme olarak "
                        f"**Soru Bankası - {_secili_ders}** kategorisine eklenir."
                    )
                    if st.button("✅ Seçilen Testleri Ekle", type="primary", disabled=not _secilenler):
                        _kategori = f"Soru Bankası - {_secili_ders}"
                        db.add_category(_kategori)
                        _kaynak = st.session_state.get(qb_path_state)
                        _flash, _eklendi, _atlandi = [], 0, 0
                        _bar = st.progress(0.0, text="Testler ekleniyor...")
                        for _n, _t in enumerate(_secilenler, start=1):
                            _baslik = f"{_secili_ders} · Test {_t['test_no']} · {_t['konu']}"
                            if db.exam_exists(_baslik, _kategori):
                                _atlandi += 1
                                _bar.progress(_n / len(_secilenler), text=f"{_n}/{len(_secilenler)}")
                                continue
                            _hedef = os.path.join(
                                PDF_DIR,
                                f"sb_{slugify(_secili_ders)}_{_t['test_no']}_{slugify(_t['konu'])}.pdf",
                            )
                            try:
                                # Sayfada GERÇEKTEN basılı olan soru numaraları
                                # (kitabın ilk sayfası yoksa test 4. sorudan
                                # başlayabilir) -- optik form bunlarla birebir
                                # aynı numaraları gösterecek.
                                _numaralar = soru_bankasi.gorunen_sorular(
                                    _kaynak, _t["sayfalar"], list(_t["cevaplar"].keys())
                                )
                                if not _numaralar:
                                    _flash.append((
                                        "error",
                                        f"⚠️ {_baslik}: sayfadaki soru numaraları okunamadı, atlandı.",
                                    ))
                                    _bar.progress(_n / len(_secilenler), text=f"{_n}/{len(_secilenler)}")
                                    continue
                                soru_bankasi.test_pdf_olustur(_kaynak, _t["sayfalar"], _hedef)
                                parsing._compress_pdf_for_display(_hedef)
                                # Anahtarlar her ihtimale karşı sayıya çevriliyor
                                # (oturumda saklanıp geri okunurken metne
                                # dönüşmüş olabilir).
                                _cev = {int(k): v for k, v in _t["cevaplar"].items()}
                                _sirali = [_cev[k] for k in _numaralar]
                                _yapi = {
                                    "Genel": {
                                        _secili_ders: {
                                            "count": len(_sirali),
                                            "coef": 1,
                                            "numbers": _numaralar,
                                        }
                                    }
                                }
                                # ÖNEMLİ: Cevap anahtarı, yapı ile AYNI iki
                                # katmanlı biçimde olmalı: bölüm -> ders -> liste.
                                # (Tek katmanlı verildiğinde puanlama sessizce
                                # 0 net üretiyordu -- testte yakalandı.)
                                db.add_exam(
                                    _baslik, _kategori, _hedef, _yapi,
                                    {"Genel": {_secili_ders: _sirali}},
                                    source="soru-bankasi",
                                )
                                _eklendi += 1
                            except Exception as e:
                                _flash.append(("error", f"❌ {_baslik}: {e}"))
                            _bar.progress(_n / len(_secilenler), text=f"{_n}/{len(_secilenler)}")
                        _flash.insert(0, (
                            "success",
                            f"✅ {_eklendi} test eklendi"
                            + (f", {_atlandi} test zaten vardı (atlandı)." if _atlandi else "."),
                        ))
                        st.session_state["_admin_flash"] = _flash
                        st.rerun()

        # ---------------- 8. Sınıf LGS ----------------
        elif admin_section == "8. Sınıf LGS Denemesi Ekle":
            st.markdown("Sözel ve Sayısal kitapçıklarını (son sayfasında cevap anahtarı olan hallerini) yükleyin. "
                        "Sistem cevap anahtarını otomatik okuyup son sayfaları kırpacak; öğrenci cevap anahtarını göremeyecek.")
            exam_title = st.text_input("Deneme Adı (Örn: 2026 LGS A Kitapçığı)", key="lgs_title")
            c1, c2 = st.columns(2)
            with c1:
                sozel_pdf = st.file_uploader("Sözel PDF", type=["pdf"], key="lgs_sozel")
            with c2:
                sayisal_pdf = st.file_uploader("Sayısal PDF", type=["pdf"], key="lgs_sayisal")

            if st.button("Denemeyi İşle, Ayrıştır ve Kaydet", type="primary"):
                if not (exam_title and sozel_pdf and sayisal_pdf):
                    st.warning("Lütfen deneme adını girin ve iki PDF'i de yükleyin.")
                elif db.exam_exists(exam_title, LGS_CATEGORY):
                    st.error("Bu isimde bir deneme zaten var. Farklı bir ad girin.")
                else:
                    sozel_subjects = [(n, c) for n, c, _ in LGS_SUBJECTS["Sözel"]]
                    sayisal_subjects = [(n, c) for n, c, _ in LGS_SUBJECTS["Sayısal"]]

                    sozel_key, sozel_msg, sozel_idx = parsing.extract_answer_key(sozel_pdf, sozel_subjects)
                    sayisal_pdf.seek(0)
                    sayisal_key, sayisal_msg, sayisal_idx = parsing.extract_answer_key(sayisal_pdf, sayisal_subjects)

                    ok = True
                    if sozel_key is None:
                        st.error(f"Sözel PDF cevap anahtarı otomatik okunamadı: {sozel_msg}")
                        ok = False
                    if sayisal_key is None:
                        st.error(f"Sayısal PDF cevap anahtarı otomatik okunamadı: {sayisal_msg}")
                        ok = False

                    if not ok:
                        st.info(
                            "Otomatik okuma başarısız oldu. Aşağıdan cevapları elle girip devam edebilirsiniz "
                            "(her ders için harfleri virgülle ayırarak yazın, örn: A,B,C,D,A,...)."
                        )
                        manual_key = {"Sözel": {}, "Sayısal": {}}
                        for name, cnt in sozel_subjects:
                            txt = st.text_input(f"[Sözel] {name} cevapları ({cnt} adet)", key=f"manual_sozel_{name}")
                            manual_key["Sözel"][name] = [x.strip().upper() for x in txt.split(",") if x.strip()]
                        for name, cnt in sayisal_subjects:
                            txt = st.text_input(f"[Sayısal] {name} cevapları ({cnt} adet)", key=f"manual_sayisal_{name}")
                            manual_key["Sayısal"][name] = [x.strip().upper() for x in txt.split(",") if x.strip()]
                        if st.button("Elle Girilen Cevaplarla Kaydet"):
                            valid = all(
                                len(manual_key[sec][name]) == cnt
                                for sec, subs in [("Sözel", sozel_subjects), ("Sayısal", sayisal_subjects)]
                                for name, cnt in subs
                            )
                            if not valid:
                                st.error("Bazı derslerde cevap sayısı eksik/fazla. Kontrol edin.")
                            else:
                                safe_path = os.path.join(PDF_DIR, f"{slugify(exam_title, 'deneme')}_guvenli.pdf")
                                parsing.crop_and_merge(
                                    [(sozel_pdf, parsing.pdf_page_count(sozel_pdf) - 1),
                                     (sayisal_pdf, parsing.pdf_page_count(sayisal_pdf) - 1)],
                                    safe_path,
                                )
                                orig_path = os.path.join(
                                    PRIVATE_DIR,f"{slugify(exam_title, 'deneme')}_orijinal.pdf"
                                )
                                with st.spinner("PDF hazırlanıyor ve küçültülüyor, bu birkaç saniye sürebilir..."):
                                    parsing.merge_full([sozel_pdf, sayisal_pdf], orig_path)
                                db.add_exam(
                                    exam_title, LGS_CATEGORY, safe_path, LGS_STRUCTURE, manual_key,
                                    source="manuel-elle-cevap", pdf_path_original=orig_path,
                                )
                                st.session_state["_admin_flash"] = (
                                    "success", f"'{exam_title}' kaydedildi." + _compression_note(safe_path)
                                )
                                st.rerun()
                    else:
                        safe_path = os.path.join(PDF_DIR, f"{slugify(exam_title, 'deneme')}_guvenli.pdf")
                        with st.spinner("PDF hazırlanıyor ve küçültülüyor, bu birkaç saniye sürebilir..."):
                            parsing.crop_and_merge(
                                [(sozel_pdf, sozel_idx), (sayisal_pdf, sayisal_idx)], safe_path
                            )
                            orig_path = os.path.join(
                                PRIVATE_DIR,f"{slugify(exam_title, 'deneme')}_orijinal.pdf"
                            )
                            parsing.merge_full([sozel_pdf, sayisal_pdf], orig_path)
                        final_key = {"Sözel": sozel_key, "Sayısal": sayisal_key}
                        db.add_exam(
                            exam_title, LGS_CATEGORY, safe_path, LGS_STRUCTURE, final_key,
                            source="otomatik-ayrıştırma", pdf_path_original=orig_path,
                        )
                        st.session_state["_admin_flash"] = (
                            "success",
                            f"✅ '{exam_title}' başarıyla işlendi ve sisteme eklendi! Cevap anahtarı otomatik "
                            f"okundu ve son sayfalar gizlendi." + _compression_note(safe_path)
                        )
                        st.balloons()
                        st.rerun()

        # ---------------- Diğer kategori / manuel ----------------
        elif admin_section == "Diğer Kategori / Soru Bankası Ekle (Manuel)":
            st.markdown("6. Sınıf, 7. Sınıf, İOKBS (Bursluluk) veya konu bazlı soru bankası testleri için kullanın.")
            cats = db.get_categories()
            other_cats = [c for c in cats if c != LGS_CATEGORY] or cats
            colc1, colc2 = st.columns([2, 1])
            with colc1:
                gcat = st.selectbox("Kategori", other_cats, key="gen_cat")
            with colc2:
                new_cat = st.text_input("Yeni kategori adı (opsiyonel)")
                if st.button("Kategori Ekle") and new_cat:
                    db.add_category(new_cat)
                    st.rerun()

            uploaded = st.file_uploader("Test PDF'i (opsiyonel; sadece cevap anahtarı da girebilirsiniz)", type=["pdf"], key="gen_pdf")
            default_title, default_subject = ("", None)
            if uploaded:
                default_title, default_subject = guess_title_and_subject(uploaded.name)

            title = st.text_input("Test Adı", value=default_title, key="gen_title")
            st.caption("Bu PDF'teki ders sütunlarını PDF'te SOLDAN SAĞA hangi sırayla göründüğüyle AYNI sırada girin.")
            subj_text = st.text_area(
                "Dersler ve soru sayıları (bir satıra bir ders: Ders Adı,Soru Sayısı)",
                value=f"{default_subject or 'Ders'},10",
                height=100,
                key="gen_subjects",
            )
            try:
                subject_rows = []
                for line in subj_text.strip().splitlines():
                    name, cnt = line.split(",")
                    subject_rows.append((name.strip(), int(cnt.strip())))
            except Exception:
                subject_rows = []
                st.error("Ders listesi formatı hatalı. Her satır 'Ders Adı,Soru Sayısı' şeklinde olmalı.")

            auto_try = st.checkbox("Cevap anahtarını PDF'in son sayfasından otomatik okumayı dene", value=bool(uploaded))

            parsed_key = None
            if uploaded and auto_try and subject_rows:
                key, msg, idx = parsing.extract_answer_key(uploaded, subject_rows)
                if key:
                    st.success("Cevap anahtarı otomatik okundu ✅")
                    parsed_key = {"Genel": key}
                    st.session_state["_gen_key_idx"] = idx
                else:
                    st.warning(f"Otomatik okunamadı: {msg} Aşağıdan elle girebilirsiniz.")

            manual_answers = {}
            if parsed_key is None and subject_rows:
                st.markdown("**Cevap anahtarını elle girin** (virgülle ayrılmış, örn: A,B,C,D,...)")
                for name, cnt in subject_rows:
                    txt = st.text_input(f"{name} cevapları ({cnt} adet)", key=f"gen_manual_{name}")
                    manual_answers[name] = [x.strip().upper() for x in txt.split(",") if x.strip()]

            if st.button("Testi Kaydet", type="primary"):
                if not (title and subject_rows):
                    st.warning("Test adı ve en az bir ders girilmelidir.")
                elif db.exam_exists(title, gcat):
                    st.error("Bu isimde bir test zaten var.")
                else:
                    structure = build_generic_structure(subject_rows)
                    if parsed_key:
                        final_key = parsed_key
                    else:
                        valid = all(len(manual_answers.get(n, [])) == c for n, c in subject_rows)
                        if not valid:
                            st.error("Elle girilen cevap sayıları soru sayılarıyla eşleşmiyor.")
                            st.stop()
                        final_key = {"Genel": manual_answers}

                    safe_title = slugify(title, "test")
                    orig_path = None
                    if uploaded:
                        idx = st.session_state.get("_gen_key_idx")
                        safe_path = os.path.join(PDF_DIR, f"{safe_title}_guvenli.pdf")
                        with st.spinner("PDF hazırlanıyor ve küçültülüyor, bu birkaç saniye sürebilir..."):
                            parsing.crop_and_merge([(uploaded, idx if idx is not None else parsing.pdf_page_count(uploaded) - 1)], safe_path)
                            orig_path = os.path.join(PRIVATE_DIR,f"{safe_title}_orijinal.pdf")
                            parsing.merge_full([uploaded], orig_path)
                    else:
                        safe_path = ""  # PDF yok, sadece cevap anahtarı / metin bazlı çalışılabilir
                    db.add_exam(title, gcat, safe_path, structure, final_key, source="manuel", pdf_path_original=orig_path)
                    note = _compression_note(safe_path) if safe_path else ""
                    st.session_state["_admin_flash"] = ("success", f"'{title}' {gcat} kategorisine eklendi." + note)
                    st.rerun()

        # ---------------- Bursluluk (İOKBS) otomatik indirme ----------------
        elif admin_section == "🎓 Bursluluk (İOKBS) Otomatik İndir":
            st.markdown(
                "MEB'in **bursluluk (İOKBS) çıkmış sorular** sayfasını tarayıp, seçtiğiniz "
                "yıl ve sınıfların kitapçıklarını otomatik indirir. İOKBS'de her sınıfta "
                "**4 ders × 25 soru = 100 soru** vardır (8. sınıfta Sosyal Bilgiler yerine "
                "T.C. İnkılap Tarihi sorulur)."
            )
            _bl_key = "_bursluluk_liste"
            if st.button("🔎 Sayfayı Tara ve Kitapçıkları Bul", type="primary"):
                with st.spinner("MEB sayfası taranıyor..."):
                    _bulunan = bot.scrape_bursluluk()
                st.session_state[_bl_key] = {f"{y}|{s}": u for (y, s), u in _bulunan.items()}
                st.rerun()

            _liste = st.session_state.get(_bl_key)
            if _liste is not None and not _liste:
                st.error(
                    "Sayfaya ulaşılamadı ya da hiç kitapçık bağlantısı bulunamadı. "
                    "İnternet bağlantınızı kontrol edip tekrar deneyin."
                )
            elif _liste:
                _cozulmus = sorted(
                    (int(k.split("|")[0]), int(k.split("|")[1])) for k in _liste
                )
                _yillar = sorted({y for y, _ in _cozulmus}, reverse=True)
                _siniflar = sorted({s for _, s in _cozulmus})
                st.success(
                    f"**{len(_cozulmus)} kitapçık** bulundu — "
                    f"{min(_yillar)}-{max(_yillar)} yılları, {_siniflar} sınıflar."
                )
                _bc1, _bc2 = st.columns(2)
                _sec_yil = _bc1.multiselect("Yıllar", _yillar, default=_yillar, key="bl_yil")
                _sec_sinif = _bc2.multiselect(
                    "Sınıflar", _siniflar,
                    default=[s for s in _siniflar if s in bot.IOKBS_YAPISI],
                    format_func=lambda s: f"{s}. Sınıf", key="bl_sinif",
                )
                _hedefler = [
                    (y, s) for (y, s) in _cozulmus if y in _sec_yil and s in _sec_sinif
                ]
                _desteklenmeyen = sorted({s for _, s in _hedefler if s not in bot.IOKBS_YAPISI})
                if _desteklenmeyen:
                    st.warning(
                        f"{_desteklenmeyen} sınıf(lar)ı için soru dağılımı tanımlı değil "
                        "(bu sayfada lise sınıfları da olabilir). Bunlar indirilir ama "
                        "otomatik puanlama kurulamaz."
                    )
                st.caption(f"**{len(_hedefler)} kitapçık** seçili. Zaten eklenmiş olanlar atlanır.")

                if st.button("⬇️ Seçilenleri İndir ve Ekle", type="primary", disabled=not _hedefler):
                    _kategori = "İOKBS (Bursluluk)"
                    db.add_category(_kategori)
                    _bl_flash, _ekl, _atl = [], 0, 0
                    # Tek bir ilerleme çubuğu kullanılıyor; her adımda yeni
                    # öğe yaratmak arayüz hatasına yol açıyordu (bkz. EBA bölümü).
                    _bar = st.progress(0.0, text="Başlıyor...")
                    for _i, (_y, _s) in enumerate(_hedefler, start=1):
                        _oran = _i / len(_hedefler)
                        _baslik = f"{_y} Bursluluk (İOKBS) - {_s}. Sınıf"
                        if db.exam_exists(_baslik, _kategori):
                            _atl += 1
                            _bar.progress(_oran, text=f"{_baslik}: zaten vardı")
                            continue
                        _bar.progress(_oran, text=f"{_baslik} indiriliyor... ({_i}/{len(_hedefler)})")
                        _ham = os.path.join(PDF_DIR, f"_iokbs_{_y}_{_s}.pdf")
                        _ok, _msg = bot.bursluluk_indir(_liste[f"{_y}|{_s}"], _ham)
                        if not _ok:
                            _bl_flash.append(("error", f"❌ {_baslik}: indirilemedi ({_msg})."))
                            continue
                        _yapi_satir = bot.IOKBS_YAPISI.get(_s)
                        if not _yapi_satir:
                            _bl_flash.append((
                                "error",
                                f"⚠️ {_baslik}: indirildi ama bu sınıf için soru dağılımı "
                                f"tanımlı değil. Dosya kaydedildi, 'Diğer Kategori' bölümünden "
                                f"elle ekleyebilirsiniz.",
                            ))
                            continue
                        _bar.progress(_oran, text=f"{_baslik}: cevap anahtarı okunuyor...")
                        _key, _kmsg, _kidx = parsing.extract_answer_key(_ham, _yapi_satir)
                        if _key is None:
                            _bl_flash.append((
                                "error",
                                f"⚠️ {_baslik}: indirildi ama cevap anahtarı otomatik "
                                f"okunamadı ({_kmsg}). Dosya kaydedildi; 'Diğer Kategori / "
                                f"Soru Bankası Ekle (Manuel)' bölümünden yükleyip cevapları "
                                f"elle girebilirsiniz.",
                            ))
                            continue
                        _guvenli = os.path.join(PDF_DIR, f"iokbs_{_y}_{_s}_guvenli.pdf")
                        _bar.progress(_oran, text=f"{_baslik}: PDF hazırlanıyor...")
                        parsing.crop_and_merge([(_ham, _kidx)], _guvenli)
                        _orij = os.path.join(PRIVATE_DIR, f"iokbs_{_y}_{_s}_orijinal.pdf")
                        parsing.merge_full([_ham], _orij)
                        db.add_exam(
                            _baslik, _kategori, _guvenli,
                            build_generic_structure(_yapi_satir),
                            {"Genel": _key}, source="otomatik-iokbs",
                            pdf_path_original=_orij,
                        )
                        _ekl += 1
                        _bl_flash.append(("success", f"✅ {_baslik}: eklendi." + _compression_note(_guvenli)))
                    _bar.empty()
                    _bl_flash.insert(0, (
                        "success",
                        f"Bitti: **{_ekl} kitapçık eklendi**"
                        + (f", {_atl} tanesi zaten vardı." if _atl else "."),
                    ))
                    st.session_state["_admin_flash"] = _bl_flash
                    st.rerun()

        # ---------------- Otomatik indirme (EBA) ----------------
        elif admin_section == "Otomatik İndirme (Resmi EBA Arşivi)":
            _ready = bot.available_years()
            st.markdown(
                "Yıl girerek resmi MEB arşivinden **8. Sınıf LGS** Sözel+Sayısal kitapçıklarını "
                "otomatik indirip işler. Birden fazla yıl için aralık girebilirsiniz "
                "(örn: **2018-2025**), sistem her yılı sırayla indirip ekler. "
                "**Daha önce eklenmiş yıllar tekrar indirilmez, otomatik atlanır.**"
            )
            st.info(
                "Adresi doğrulanmış ve hazır olan yıllar: **"
                + ", ".join(str(y) for y in _ready)
                + "**. Listede olmayan yıllar için sistem önce kaynak sayfayı canlı tarar, "
                "sonra EBA adres kalıbını dener."
            )
            year_range = st.text_input(
                "Yıl veya yıl aralığı",
                value=f"{min(_ready)}-{max(_ready)}" if _ready else str(datetime.now().year),
            )
            if st.button("İndir ve İşle", type="primary"):
                years = []
                try:
                    if "-" in year_range:
                        a, b = year_range.split("-")
                        years = list(range(int(a.strip()), int(b.strip()) + 1))
                    else:
                        years = [int(year_range.strip())]
                except ValueError:
                    st.error("Yılı '2023' veya '2018-2025' biçiminde yazın.")
                    years = []

                sozel_subjects = [(n, c) for n, c, _ in LGS_SUBJECTS["Sözel"]]
                sayisal_subjects = [(n, c) for n, c, _ in LGS_SUBJECTS["Sayısal"]]
                _eba_flashes = []

                # ÖNEMLİ - EKRAN HATASI ("insertBefore ... NotFoundError"):
                # Bu döngü her yıl için ayrı ayrı st.spinner(...) açıp kapatıyordu.
                # Uzun bir aralıkta (2018-2025) bu, ekrandaki öğelerin sürekli
                # yaratılıp yok edilmesi demek; Streamlit'in arayüzü bu hızlı
                # değişime yetişemeyip tarayıcı hatası veriyordu. Artık döngü
                # boyunca TEK BİR ilerleme çubuğu kullanılıyor, sadece yazısı
                # güncelleniyor -- yeni öğe eklenip silinmiyor.
                _scraped = {}
                _durum = st.empty()
                _ilerleme = st.progress(0.0, text="Hazırlanıyor...")
                if years:
                    _ilerleme.progress(0.0, text="Kaynak sayfa taranıyor...")
                    _scraped = bot.scrape_source_page()

                for _yi, yil in enumerate(years, start=1):
                    _oran = (_yi - 1) / max(len(years), 1)
                    exam_title = f"{yil} LGS (Resmi Arşiv)"
                    if db.exam_exists(exam_title, LGS_CATEGORY):
                        _eba_flashes.append(("success", f"↩️ {yil}: zaten sistemde, tekrar indirilmedi."))
                        _ilerleme.progress(_yi / len(years), text=f"{yil}: zaten vardı, atlandı")
                        continue
                    _ilerleme.progress(_oran, text=f"{yil} indiriliyor... ({_yi}/{len(years)})")
                    res = bot.fetch_lgs_year(yil, PDF_DIR, scraped=_scraped)
                    if not res["Sözel"] or not res["Sayısal"]:
                        _eksik = [b for b in ("Sözel", "Sayısal") if not res[b]]
                        _eba_flashes.append((
                            "error",
                            f"❌ {yil}: {' ve '.join(_eksik)} kitapçığı indirilemedi "
                            f"(bu yıl için geçerli bir adres bulunamadı). Diğer yıllar etkilenmedi.",
                        ))
                        _ilerleme.progress(_yi / len(years), text=f"{yil}: bulunamadı")
                        continue
                    sozel_key, sozel_msg, sozel_idx = parsing.extract_answer_key(res["Sözel"], sozel_subjects)
                    sayisal_key, sayisal_msg, sayisal_idx = parsing.extract_answer_key(res["Sayısal"], sayisal_subjects)
                    if sozel_key is None or sayisal_key is None:
                        _eba_flashes.append((
                            "error",
                            f"⚠️ {yil}: indirildi ama cevap anahtarı otomatik okunamadı "
                            f"({sozel_msg or sayisal_msg}). Manuel yüklemeyi deneyin.",
                        ))
                        _ilerleme.progress(_yi / len(years), text=f"{yil}: cevap anahtarı okunamadı")
                        continue
                    safe_path = os.path.join(PDF_DIR, f"{yil}_LGS_guvenli.pdf")
                    _ilerleme.progress(_oran, text=f"{yil}: PDF hazırlanıyor... ({_yi}/{len(years)})")
                    parsing.crop_and_merge([(res["Sözel"], sozel_idx), (res["Sayısal"], sayisal_idx)], safe_path)
                    orig_path = os.path.join(PRIVATE_DIR, f"{yil}_LGS_orijinal.pdf")
                    parsing.merge_full([res["Sözel"], res["Sayısal"]], orig_path)
                    _ilerleme.progress(_yi / len(years), text=f"{yil}: eklendi ({_yi}/{len(years)})")
                    db.add_exam(
                        exam_title, LGS_CATEGORY, safe_path, LGS_STRUCTURE,
                        {"Sözel": sozel_key, "Sayısal": sayisal_key}, source="otomatik-eba",
                        pdf_path_original=orig_path,
                    )
                    _eba_flashes.append(("success", f"✅ {yil}: eklendi." + _compression_note(safe_path)))
                # İlerleme çubuğunu ve durum alanını temizle, sonuçları
                # yeniden çizilen sayfada tek seferde göster.
                _ilerleme.empty()
                _durum.empty()
                if _eba_flashes:
                    st.session_state["_admin_flash"] = _eba_flashes
                st.rerun()

        # ---------------- URL'den indir ----------------
        elif admin_section == "URL'den PDF İndir":
            st.markdown(
                "Farklı bir siteden (MEB'in okul sayfaları, yayınevleri, vb.) bulduğunuz **doğrudan PDF linkini** "
                "yapıştırın; bot indirsin. (Botun otomatik bulamadığı sitelerdeki dosyalar için kullanışlıdır.)"
            )
            url = st.text_input("PDF Linki")
            fname = st.text_input("Kaydedilecek dosya adı", value="indirilen_dosya.pdf")
            if st.button("İndir"):
                dest = os.path.join(PDF_DIR, slugify(os.path.splitext(fname)[0], "indirilen") + ".pdf")
                ok, msg = bot.fetch_from_url(url, dest)
                if ok:
                    st.success(f"İndirildi: {dest}. Şimdi 'Diğer Kategori / Soru Bankası Ekle' bölümünden bu dosyayı yükleyerek işleyebilirsiniz.")
                else:
                    st.error(f"İndirilemedi: {msg}")

        # ---------------- Google Drive ----------------
        elif admin_section == "Google Drive'dan İçe Aktar":
            if not drive_sync.is_configured():
                st.warning(
                    "Google Drive entegrasyonu henüz kurulmamış. README.md'deki adımları izleyerek "
                    "credentials.json dosyasını proje klasörüne ekleyin."
                )
            else:
                folder_id = st.text_input("Google Drive Klasör ID'si")
                if st.button("Klasördeki PDF'leri Listele") and folder_id:
                    try:
                        files = drive_sync.list_pdfs_in_folder(folder_id)
                        st.session_state["_drive_files"] = files
                    except Exception as e:
                        st.error(f"Drive'a bağlanılamadı: {e}")
                for f in st.session_state.get("_drive_files", []):
                    c1, c2 = st.columns([4, 1])
                    c1.write(f["name"])
                    if c2.button("İndir", key=f"drive_{f['id']}"):
                        dest = os.path.join(PDF_DIR, f["name"])
                        drive_sync.download_file(f["id"], dest)
                        st.success(f"İndirildi: {f['name']} — 'Diğer Kategori Ekle' bölümünden işleyebilirsiniz.")

        # ---------------- Kayıtlı denemeler ----------------
        elif admin_section == "Kayıtlı Denemeler":
            all_exams = db.get_exams()
            if not all_exams:
                st.info("Henüz kayıtlı deneme yok.")
            for e in all_exams:
                c1, c2, c3, c4 = st.columns([4, 2, 1, 2])
                c1.write(f"**{e['title']}**  ·  {e['category']}  ·  {e['source']}")
                c2.write(e["created_at"])
                if c3.button("Sil", key=f"del_{e['id']}"):
                    db.delete_exam(e["id"])
                    st.rerun()
                with c4:
                    if e.get("pdf_path_original") and os.path.exists(e["pdf_path_original"]):
                        # ONEMLI - PERFORMANS: Orijinal PDF'i (buyuk olabilir)
                        # base64'e cevirme islemini SADECE admin gercekten
                        # gormek istediginde yapiyoruz -- yoksa bu listede
                        # baska bir sey (orn. baska bir denemeyi SILMEK)
                        # her seferinde TUM buyuk PDF'leri gereksiz yere
                        # yeniden kodlar ve sayfa cok yavaslar.
                        show_key = f"show_orig_{e['id']}"
                        if not st.session_state.get(show_key):
                            if st.button("🔓 Orijinal PDF", key=f"origbtn_{e['id']}"):
                                st.session_state[show_key] = True
                                st.rerun()
                        else:
                            pdf_link_button(e["pdf_path_original"])
                    else:
                        st.caption("Orijinal PDF yok")
                st.divider()

        # ---------------- Öğrenci şifrelerini yönet ----------------
        elif admin_section == "Öğrenci Hesapları (Ekle/Sil/Şifre)":
            st.markdown(
                "Öğrenci hesaplarını buradan yönetirsiniz: **kullanıcı adını değiştirme**, "
                "**şifre sıfırlama** ve **öğrenciyi silme**."
            )
            students = db.get_students()
            if not students:
                st.info("Henüz kayıtlı öğrenci yok.")
            else:
                for s in students:
                    with st.expander(f"👤 {s['display_name']}  (kullanıcı adı: {s['username']})"):
                        st.markdown("**Kullanıcı adını değiştir**")
                        new_username = st.text_input(
                            "Yeni kullanıcı adı", value=s["username"], key=f"newuser_{s['username']}"
                        )
                        if st.button("Kullanıcı Adını Güncelle", key=f"renamebtn_{s['username']}"):
                            ok, msg = db.rename_student(s["username"], new_username)
                            if ok:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)

                        st.divider()
                        st.markdown("**Şifreyi sıfırla**")
                        new_pw = st.text_input(
                            "Yeni şifre", type="password", key=f"newpw_{s['username']}"
                        )
                        new_pw2 = st.text_input(
                            "Yeni şifre (tekrar)", type="password", key=f"newpw2_{s['username']}"
                        )
                        if st.button("Şifreyi Sıfırla", key=f"resetbtn_{s['username']}"):
                            if new_pw != new_pw2:
                                st.error("Girdiğiniz şifreler eşleşmiyor.")
                            else:
                                ok, msg = db.reset_student_password(s["username"], new_pw)
                                if ok:
                                    st.success(f"{s['display_name']} için şifre güncellendi.")
                                else:
                                    st.error(msg)

                        st.divider()
                        st.markdown("**🗑️ Öğrenciyi sil**")
                        _sonuc_sayisi = len(db.get_results(student_name=s["username"]))
                        st.caption(
                            f"Bu öğrencinin **{_sonuc_sayisi}** kayıtlı sınav sonucu var. "
                            "Silme işlemi geri alınamaz."
                        )
                        _sonuc_da = st.checkbox(
                            "Sınav sonuçları da silinsin",
                            value=True,
                            key=f"delres_{s['username']}",
                            help="İşareti kaldırırsanız hesap silinir ama geçmiş sonuçlar veritabanında kalır.",
                        )
                        # İki adımlı onay: yanlışlıkla tek tıkla silinmesin.
                        _onay_key = f"confirm_del_{s['username']}"
                        if not st.session_state.get(_onay_key):
                            if st.button("Öğrenciyi Sil", key=f"delbtn_{s['username']}"):
                                st.session_state[_onay_key] = True
                                st.rerun()
                        else:
                            st.warning(
                                f"**{s['display_name']}** adlı öğrenciyi silmek üzeresiniz. Emin misiniz?"
                            )
                            _d1, _d2 = st.columns(2)
                            if _d1.button("✅ Evet, sil", key=f"delyes_{s['username']}", type="primary"):
                                ok, msg = db.delete_student(s["username"], sonuclari_da_sil=_sonuc_da)
                                st.session_state.pop(_onay_key, None)
                                # Silinen öğrenci o an "öğrenci olarak devam et"
                                # modunda seçiliyse oturumdan da düşür.
                                if ok and st.session_state.student_name == s["username"]:
                                    st.session_state.student_name = ""
                                    st.session_state.student_display_name = ""
                                st.session_state["_admin_flash"] = (
                                    ("success", f"✅ {msg}") if ok else ("error", msg)
                                )
                                st.rerun()
                            if _d2.button("Vazgeç", key=f"delno_{s['username']}"):
                                st.session_state.pop(_onay_key, None)
                                st.rerun()

        # ---------------- Öğrenci raporları ----------------
        elif admin_section == "Öğrenci Raporları":
            students = db.get_students()
            if not students:
                st.info("Henüz kayıtlı öğrenci yok.")
            else:
                names = {s["username"]: s["display_name"] for s in students}
                chosen = st.selectbox(
                    "Öğrenci seçin",
                    list(names.keys()),
                    format_func=lambda u: names[u],
                    key="report_student_pick",
                )
                results = db.get_results(student_name=chosen)
                if not results:
                    st.info(f"{names[chosen]} henüz hiç sınav çözmemiş.")
                else:
                    nets = [r["total_net"] for r in results]
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Çözülen Test Sayısı", len(results))
                    c2.metric("Ortalama Net", round(sum(nets) / len(nets), 2))
                    c3.metric("En Yüksek Net", max(nets))

                    st.divider()
                    st.markdown("**Sınav Geçmişi**")
                    for r in results:
                        title = f"{r['created_at']} · {r['exam_title']} ({r['category']}) · Net: {r['total_net']}"
                        with st.expander(title):
                            # Sonuçları sadece admin, sadece burada silebilir --
                            # öğrenci tarafında (Gelişim Raporum) hiçbir silme seçeneği yoktur.
                            if st.button("🗑️ Bu sonucu sil", key=f"del_result_{r['id']}"):
                                db.delete_result(r["id"])
                                st.rerun()
                            cols = st.columns(len(r["per_subject"]))
                            for c, (subj, res) in zip(cols, r["per_subject"].items()):
                                c.metric(subj, f"Net: {res['net']}", f"D:{res['dogru']} Y:{res['yanlis']} B:{res['bos']}")
                            if r.get("weighted_score") is not None:
                                st.caption(f"Tahmini ağırlıklı puan göstergesi: {r['weighted_score']}")

                            detail = r.get("answers_detail")
                            if detail:
                                st.markdown("**Soru bazlı dökum**")
                                for section, subjects in detail.items():
                                    for subject, rows in subjects.items():
                                        yanlis_bos = [x for x in rows if x["durum"] != "dogru"]
                                        if not yanlis_bos:
                                            st.caption(f"{subject}: tüm sorular doğru! 🎉")
                                            continue
                                        df_detail = pd.DataFrame(rows)
                                        df_detail["durum"] = df_detail["durum"].map(
                                            {"dogru": "✅ Doğru", "yanlis": "❌ Yanlış", "bos": "⬜ Boş"}
                                        )
                                        st.caption(f"{subject}")
                                        st.dataframe(
                                            df_detail.rename(
                                                columns={
                                                    "soru": "Soru No",
                                                    "verilen": "Verilen Cevap",
                                                    "dogru_cevap": "Doğru Cevap",
                                                    "durum": "Durum",
                                                }
                                            ),
                                            hide_index=True,
                                            use_container_width=True,
                                        )
                            else:
                                st.caption("Bu sınav için soru bazlı dökum kaydedilmemiş (eski kayıt).")

        # ---------------- Hesap ayarları (admin kullanıcı adı / şifre) ----------------
        elif admin_section == "Hesap Ayarları":
            st.markdown("#### Kullanıcı adını değiştir")
            st.caption("Kullanıcı adınızı değiştirmek için mevcut şifrenizi girmeniz gerekir.")
            cur_username_display = st.session_state.get("admin_username", config.ADMIN_USERNAME)
            st.text_input("Mevcut kullanıcı adı", value=cur_username_display, disabled=True, key="acc_cur_username_display")
            new_username = st.text_input("Yeni kullanıcı adı", key="acc_new_username")
            cur_pw_for_username = st.text_input("Mevcut şifre", type="password", key="acc_cur_pw_for_username")
            if st.button("Kullanıcı Adını Değiştir", type="primary", key="acc_change_username_btn"):
                if not new_username.strip():
                    st.error("Yeni kullanıcı adı boş olamaz.")
                else:
                    ok, msg = db.change_admin_username(
                        cur_username_display, cur_pw_for_username, new_username.strip()
                    )
                    if ok:
                        st.session_state.admin_username = new_username.strip()
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

            st.divider()

            st.markdown("#### Şifreyi değiştir")
            st.markdown("Yönetici şifrenizi buradan değiştirebilirsiniz.")
            cur_pw = st.text_input("Mevcut şifre", type="password", key="acc_cur_pw")
            new_pw = st.text_input("Yeni şifre", type="password", key="acc_new_pw")
            new_pw2 = st.text_input("Yeni şifre (tekrar)", type="password", key="acc_new_pw2")
            if st.button("Şifreyi Değiştir", type="primary"):
                if new_pw != new_pw2:
                    st.error("Yeni şifreler birbiriyle eşleşmiyor.")
                else:
                    ok, msg = db.change_admin_password(
                        st.session_state.get("admin_username", config.ADMIN_USERNAME), cur_pw, new_pw
                    )
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)
