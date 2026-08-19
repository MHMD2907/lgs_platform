"""
LGS Eğitim Platformu - app.py
Tablet/bilgisayar tarayıcısından çalışan, geçmiş yıl LGS (ve 6-7. sınıf,
İOKBS, genel soru bankası) denemelerini çözüp otomatik puanlayan sistem.

Çalıştırmak için:
    pip install -r requirements.txt
    streamlit run app.py

Ayrıntılı kurulum ve kullanım için README.md dosyasına bakın.
"""

import base64
import io
import os
import re
import shutil
from datetime import datetime
from urllib.parse import quote

import pandas as pd
import pdfplumber
import streamlit as st

import bot
import config
import db
import drive_sync
import parsing
import scoring

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
        return (
            f" (⚠️ PDF boyutu: {size_mb} MB — Ghostscript sunucuda bulunamadığı için "
            f"küçültme YAPILAMADI; bu genelde `packages.txt` dosyası henüz devreye girmediğinde "
            f"olur, uygulamayı GitHub'dan yeniden dağıtmayı deneyin.)"
        )
    if size_mb > 8:
        return (
            f" (PDF boyutu: {size_mb} MB — küçültüldü ama yine de büyük kaldı, "
            f"tablette yüklenmesi biraz zaman alabilir.)"
        )
    return f" (PDF boyutu: {size_mb} MB — sisteme küçültülmüş olarak kaydedildi.)"


def _pdf_cache_entry(path):
    """Bir PDF'in ham baytlarini VE tarayicida gostermek icin gereken
    base64 'data:' adresini oturum icinde bir kez hesaplayip onbellekte
    tutar (yol + degisim zamani + boyut anahtar olarak).

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
        b64 = base64.b64encode(data).decode("utf-8")
        cache.clear()  # ayni anda birden fazla buyuk PDF'i bellekte tutmayalim
        cache[cache_key] = {
            "bytes": data,
            "b64": b64,
            "src": f"data:application/pdf;base64,{b64}",
        }
    return cache[cache_key]


def _pdf_page_count(path):
    """Bir PDF'in sayfa sayisini oturum icinde bir kez hesaplayip
    onbellekte tutar (yol + degisim zamani anahtar)."""
    stat = os.stat(path)
    cache_key = f"{path}|{stat.st_mtime_ns}|{stat.st_size}"
    cache = st.session_state.setdefault("_pdf_pagecount_cache", {})
    if cache_key not in cache:
        with pdfplumber.open(path) as pdf:
            cache.clear()
            cache[cache_key] = len(pdf.pages)
    return cache[cache_key]


def _pdf_page_image(path, page_num, dpi=130):
    """Bir PDF sayfasini DUZ BIR RESME (JPEG bayt dizisi) cevirip
    onbellekte tutar.

    ONEMLI - NEDEN RESIM: PDF'i tarayiciya gomup gostermenin denenen HER
    YOLU (dogrudan 'data:' adresi iframe'e verilmesi, 'data:' adresinin
    yeni sekmede acilmasi, base64'un JavaScript ile 'Blob'a cevrilip
    iframe'e verilmesi) tarayici guvenlik kisitlamalarina takildi -- Chrome
    hepsini "engellendi" diyerek reddetti, hem masaustunde hem telefonda.
    Duz bir RESIM (JPEG) ise sıradan bir fotoğraf gibi davranır; PDF'e
    özel HİÇBİR güvenlik kısıtlaması yoktur ve her cihazda çalışır. Bu
    yuzden her sayfa sunucu tarafinda (pdfplumber/pypdfium2 ile) resme
    cevrilip st.image() ile gosteriliyor."""
    stat = os.stat(path)
    cache_key = f"{path}|{stat.st_mtime_ns}|{page_num}|{dpi}"
    cache = st.session_state.setdefault("_pdf_page_img_cache", {})
    if cache_key not in cache:
        # Bellek şişmesin diye aynı anda en fazla birkaç sayfa tutulur.
        if len(cache) >= 6:
            del cache[next(iter(cache))]
        with pdfplumber.open(path) as pdf:
            page_img = pdf.pages[page_num].to_image(resolution=dpi)
            buf = io.BytesIO()
            page_img.original.convert("RGB").save(buf, format="JPEG", quality=80)
            cache[cache_key] = buf.getvalue()
    return cache[cache_key]


def show_pdf(path, height=780):
    """PDF'i SAYFA SAYFA RESIM olarak gosterir; ogrenci 'Onceki/Sonraki'
    ile gezinir ya da dogrudan sayfa numarasi girer. Boylece PDF'i ve
    Optik Form'u ayni ekranda, yan yana, disari cikmadan kullanabilir
    (bkz. _pdf_page_image() ustundeki not: bu, denenen onceki uc yontemin
    (data: URI, yeni sekme, Blob) hepsinin tarayici tarafindan engellenmesi
    uzerine bulunan cozum)."""
    try:
        page_count = _pdf_page_count(path)
    except Exception as e:
        st.error(f"PDF okunamadı: {e}")
        return

    state_key = f"_pdf_page_{path}"
    if state_key not in st.session_state:
        st.session_state[state_key] = 0
    st.session_state[state_key] = max(0, min(st.session_state[state_key], page_count - 1))

    nav1, nav2, nav3 = st.columns([1, 2, 1])
    with nav1:
        if st.button("◀ Önceki", key=f"prev_{path}", use_container_width=True,
                      disabled=st.session_state[state_key] <= 0):
            st.session_state[state_key] -= 1
            st.rerun()
    with nav3:
        if st.button("Sonraki ▶", key=f"next_{path}", use_container_width=True,
                      disabled=st.session_state[state_key] >= page_count - 1):
            st.session_state[state_key] += 1
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
            min_value=1, max_value=page_count,
            value=st.session_state[state_key] + 1,
            key=f"_pdf_jump_{path}_{st.session_state[state_key]}",
            label_visibility="collapsed",
        )
        if new_page - 1 != st.session_state[state_key]:
            st.session_state[state_key] = new_page - 1
            st.rerun()

    with st.spinner("Sayfa yükleniyor..."):
        try:
            img_bytes = _pdf_page_image(path, st.session_state[state_key])
        except Exception as e:
            st.error(f"Sayfa gösterilirken hata oluştu: {e}")
            return

    with st.container(height=height, border=True):
        st.image(img_bytes, use_container_width=True)

    st.caption(f"📄 Sayfa {st.session_state[state_key] + 1} / {page_count}")

    entry = _pdf_cache_entry(path)
    st.download_button(
        "⬇️ Kitapçığın Tamamını İndir",
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
        #MainMenu, footer {visibility: hidden;}
        .block-container {padding-top: 3.5rem; padding-left: 2rem; padding-right: 2rem; max-width: 100%;}
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

st.set_page_config(page_title=config.APP_TITLE, layout="wide", page_icon="📚")
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
        if st.button("Çıkış Yap", key="student_logout_btn", use_container_width=True):
            st.session_state.student_name = ""
            st.session_state.student_display_name = ""
            st.rerun()
    else:
        # Sadece yönetici girişi yapılmış: aynı şekilde "Öğrenci Girişi"
        # burada GÖSTERİLMEZ, tek bir durum net şekilde görünür.
        st.success("Yönetici olarak giriş yaptınız.")
        if st.button("Çıkış Yap", key="admin_logout_btn", use_container_width=True):
            st.session_state.is_admin = False
            st.rerun()

tab_names = ["📱 Sınav Çöz", "📊 Gelişim Raporum"]
if st.session_state.is_admin:
    tab_names.append("⚙️ Admin Paneli")
tabs = st.tabs(tab_names)


# ================================================================= TAB: SINAV ÇÖZ
with tabs[0]:
    st.markdown("### 📱 Sınav Çöz")
    st.caption("Aşağıdan bir kategori ve deneme seçerek başlayın.")
    categories = db.get_categories()
    col_a, col_b = st.columns([1, 2])
    with col_a:
        selected_cat = st.selectbox("Kategori", categories, key="solve_cat")
    exams = db.get_exams(category=selected_cat)
    with col_b:
        if exams:
            exam_titles = {e["id"]: e["title"] for e in exams}
            # Sayfa açılır açılmaz otomatik olarak bir sınavın içine
            # düşülmesin diye başta hiçbir deneme seçili gelmiyor; öğrenci
            # bilinçli olarak bir deneme seçmeden PDF/Optik Form görünmüyor.
            options = [None] + list(exam_titles.keys())
            selected_exam_id = st.selectbox(
                "Çözmek İstediğiniz Denemeyi Seçin",
                options,
                format_func=lambda x: "— Bir deneme seçin —" if x is None else exam_titles[x],
                key="solve_exam",
            )
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
                    st.warning("Sonucunuzun kaydedilmesi için önce soldaki menüden giriş yapın veya hesap oluşturun.")

                saved = db.load_progress(selected_exam_id, student_name, attempt_no) or {}

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
                            count = structure[section][subject]["count"]
                            saved_subject = saved.get(section, {}).get(subject, [])
                            answers = []
                            for i in range(1, count + 1):
                                prev = saved_subject[i - 1] if i - 1 < len(saved_subject) else "Boş"
                                default_index = options.index(prev) if prev in options else 4
                                ans = st.radio(
                                    f"{subject} - Soru {i}",
                                    options,
                                    index=default_index,
                                    horizontal=True,
                                    key=f"ans_{selected_exam_id}_{attempt_no}_{subject}_{i}",
                                )
                                answers.append(ans)
                            user_answers[section][subject] = answers

                if student_name:
                    db.save_progress(selected_exam_id, student_name, attempt_no, user_answers)

                submitted = st.button(
                    "✅ Sınavı Bitir ve Puanla",
                    type="primary",
                    use_container_width=True,
                    key=f"submit_{selected_exam_id}_{attempt_no}",
                )

                if submitted and not student_name:
                    st.error(
                        "Sonucunuzun kaydedilebilmesi için önce soldaki menüden giriş yapmanız "
                        "veya hesap oluşturmanız gerekiyor. Giriş yaptıktan sonra sınavı tekrar bitirin."
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
                "Diğer Kategori / Soru Bankası Ekle (Manuel)",
                "Otomatik İndirme (Resmi EBA Arşivi)",
                "URL'den PDF İndir",
                "Google Drive'dan İçe Aktar",
                "Kayıtlı Denemeler",
                "Öğrenci Şifrelerini Yönet",
                "Öğrenci Raporları",
                "Hesap Ayarları",
            ],
            horizontal=True,
        )
        st.divider()

        # ---------------- 8. Sınıf LGS ----------------
        if admin_section == "8. Sınıf LGS Denemesi Ekle":
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

        # ---------------- Otomatik indirme (EBA) ----------------
        elif admin_section == "Otomatik İndirme (Resmi EBA Arşivi)":
            st.markdown(
                "Yıl girerek resmi MEB/EBA arşivinden **8. Sınıf LGS** Sözel+Sayısal kitapçıklarını "
                "otomatik indirip işlemeyi dener. Birden fazla yıl için aralık girebilirsiniz "
                "(örn: 2022-2026), sistem her yılı sırayla indirip ekler."
            )
            year_range = st.text_input("Yıl veya yıl aralığı", value=str(datetime.now().year))
            if st.button("İndir ve İşle", type="primary"):
                years = []
                if "-" in year_range:
                    a, b = year_range.split("-")
                    years = list(range(int(a.strip()), int(b.strip()) + 1))
                else:
                    years = [int(year_range.strip())]

                sozel_subjects = [(n, c) for n, c, _ in LGS_SUBJECTS["Sözel"]]
                sayisal_subjects = [(n, c) for n, c, _ in LGS_SUBJECTS["Sayısal"]]
                _eba_flashes = []

                for yil in years:
                    exam_title = f"{yil} LGS (Resmi Arşiv)"
                    if db.exam_exists(exam_title, LGS_CATEGORY):
                        st.info(f"{yil}: zaten sistemde, atlandı.")
                        continue
                    with st.spinner(f"{yil} indiriliyor..."):
                        res = bot.fetch_lgs_year(yil, PDF_DIR)
                    if not res["Sözel"] or not res["Sayısal"]:
                        st.error(f"{yil}: indirilemedi. " + " | ".join(res["hatalar"]))
                        continue
                    sozel_key, sozel_msg, sozel_idx = parsing.extract_answer_key(res["Sözel"], sozel_subjects)
                    sayisal_key, sayisal_msg, sayisal_idx = parsing.extract_answer_key(res["Sayısal"], sayisal_subjects)
                    if sozel_key is None or sayisal_key is None:
                        st.error(f"{yil}: indirildi ama cevap anahtarı otomatik okunamadı ({sozel_msg or sayisal_msg}). Manuel yüklemeyi deneyin.")
                        continue
                    safe_path = os.path.join(PDF_DIR, f"{yil}_LGS_guvenli.pdf")
                    with st.spinner(f"{yil}: PDF hazırlanıyor ve küçültülüyor, bu birkaç saniye sürebilir..."):
                        parsing.crop_and_merge([(res["Sözel"], sozel_idx), (res["Sayısal"], sayisal_idx)], safe_path)
                        orig_path = os.path.join(PRIVATE_DIR,f"{yil}_LGS_orijinal.pdf")
                        parsing.merge_full([res["Sözel"], res["Sayısal"]], orig_path)
                    db.add_exam(
                        exam_title, LGS_CATEGORY, safe_path, LGS_STRUCTURE,
                        {"Sözel": sozel_key, "Sayısal": sayisal_key}, source="otomatik-eba",
                        pdf_path_original=orig_path,
                    )
                    _eba_flashes.append(("success", f"✅ {yil}: eklendi." + _compression_note(safe_path)))
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
        elif admin_section == "Öğrenci Şifrelerini Yönet":
            st.markdown(
                "Bir öğrenci şifresini unutursa, burada onun için yeni bir şifre "
                "belirleyebilirsiniz. Öğrenciye sadece yeni şifreyi söylemeniz yeterli."
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
