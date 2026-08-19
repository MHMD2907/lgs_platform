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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# PDF'ler "static" klasoru altinda tutulur; Streamlit bu klasoru dogrudan
# /app/static/... adresinden sunar. Boylece tablet PDF'i normal bir dosya
# gibi BIR KEZ indirip onbellege alir (base64 gomme yontemi ise 25 MB'lik
# bir kitapcigi ~33 MB metne cevirip her yenilemede tekrar gonderiyordu).
STATIC_DIR = os.path.join(BASE_DIR, "static")
PDF_DIR = os.path.join(STATIC_DIR, config.PDF_DIR_NAME)
os.makedirs(PDF_DIR, exist_ok=True)

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


def show_pdf(path, height=780):
    """PDF'i tablette hizli acilmasi icin mumkunse dogrudan URL uzerinden
    gosterir; dosya static klasoru disindaysa base64'e geri doner."""
    abs_path = os.path.abspath(path)
    static_root = os.path.abspath(STATIC_DIR) + os.sep
    if abs_path.startswith(static_root):
        rel = os.path.relpath(abs_path, STATIC_DIR).replace(os.sep, "/")
        src = f"app/static/{quote(rel)}#view=FitH"
    else:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        src = f"data:application/pdf;base64,{b64}#view=FitH"
    st.markdown(
        f'<iframe src="{src}" '
        f'width="100%" height="{height}px" style="border:1px solid #e2e8f0;border-radius:12px;"></iframe>',
        unsafe_allow_html=True,
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

def render_student_login_form():
    login_user = st.text_input("Kullanıcı Adı", key="login_user")
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

    if not st.session_state.student_name and not st.session_state.is_admin:
        login_type = st.selectbox("Giriş türü", ["Öğrenci", "Yönetici"], key="login_type")
        st.divider()
        if login_type == "Öğrenci":
            st.subheader("👤 Öğrenci Girişi")
            render_student_login_form()
        else:
            st.subheader("⚙️ Yönetici Girişi")
            render_admin_login_form()
    else:
        if st.session_state.student_name:
            st.success(f"Hoş geldin, {st.session_state.student_display_name}! 👋")
            if st.button("Çıkış Yap", key="student_logout_btn", use_container_width=True):
                st.session_state.student_name = ""
                st.session_state.student_display_name = ""
                st.rerun()
        else:
            with st.expander("👤 Öğrenci Girişi"):
                render_student_login_form()

        st.divider()

        if st.session_state.is_admin:
            st.success("Yönetici olarak giriş yaptınız.")
            if st.button("Çıkış Yap", key="admin_logout_btn", use_container_width=True):
                st.session_state.is_admin = False
                st.rerun()
        else:
            with st.expander("⚙️ Yönetici Girişi"):
                render_admin_login_form()

tab_names = ["📱 Sınav Çöz", "📊 Gelişim Raporum"]
if st.session_state.is_admin:
    tab_names.append("⚙️ Admin Paneli")
tabs = st.tabs(tab_names)


# ================================================================= TAB: SINAV ÇÖZ
with tabs[0]:
    categories = db.get_categories()
    col_a, col_b = st.columns([1, 2])
    with col_a:
        selected_cat = st.selectbox("Kategori", categories, key="solve_cat")
    exams = db.get_exams(category=selected_cat)
    with col_b:
        if exams:
            exam_titles = {e["id"]: e["title"] for e in exams}
            selected_exam_id = st.selectbox(
                "Çözmek İstediğiniz Denemeyi Seçin",
                list(exam_titles.keys()),
                format_func=lambda x: exam_titles[x],
                key="solve_exam",
            )
        else:
            selected_exam_id = None
            st.info("Bu kategoride henüz bir deneme yok. Admin panelinden ekleyin.")

    if selected_exam_id:
        exam = db.get_exam(selected_exam_id)
        structure = exam["structure"]
        answer_key = exam["answer_key"]
        attempt_no = st.session_state.attempt.get(selected_exam_id, 0)

        col_pdf, col_form = st.columns([6, 4])

        with col_pdf:
            st.subheader(exam["title"])
            if os.path.exists(exam["pdf_path"]):
                show_pdf(exam["pdf_path"])
            else:
                st.error("PDF dosyası bulunamadı.")

        with col_form:
            st.subheader("📝 Optik Form")
            all_subjects = [
                (section, subject)
                for section, subjects in structure.items()
                for subject in subjects
            ]
            subject_tabs = st.tabs([s for _, s in all_subjects])
            user_answers = {section: {} for section in structure}

            with st.form(f"form_{selected_exam_id}_{attempt_no}"):
                for (section, subject), stab in zip(all_subjects, subject_tabs):
                    with stab:
                        count = structure[section][subject]["count"]
                        answers = []
                        for i in range(1, count + 1):
                            ans = st.radio(
                                f"{subject} - Soru {i}",
                                ["A", "B", "C", "D", "Boş"],
                                index=4,
                                horizontal=True,
                                key=f"ans_{selected_exam_id}_{attempt_no}_{subject}_{i}",
                            )
                            answers.append(ans)
                        user_answers[section][subject] = answers

                submitted = st.form_submit_button("✅ Sınavı Bitir ve Puanla", type="primary", use_container_width=True)

            if submitted and not st.session_state.student_name:
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
                    selected_exam_id, st.session_state.student_name, per_subject, total_net,
                    weighted_score, answers_detail=answers_detail,
                )

                st.success("Sınav tamamlandı! Sonuçlarınız aşağıda ve 'Gelişim Raporum' sekmesinde kaydedildi.")
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

        if st.button("🔄 Testi Sıfırla / Yeniden Çöz"):
            st.session_state.attempt[selected_exam_id] = attempt_no + 1
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
                                db.add_exam(exam_title, LGS_CATEGORY, safe_path, LGS_STRUCTURE, manual_key, source="manuel-elle-cevap")
                                st.success(f"'{exam_title}' kaydedildi.")
                                st.rerun()
                    else:
                        safe_path = os.path.join(PDF_DIR, f"{slugify(exam_title, 'deneme')}_guvenli.pdf")
                        parsing.crop_and_merge(
                            [(sozel_pdf, sozel_idx), (sayisal_pdf, sayisal_idx)], safe_path
                        )
                        final_key = {"Sözel": sozel_key, "Sayısal": sayisal_key}
                        db.add_exam(exam_title, LGS_CATEGORY, safe_path, LGS_STRUCTURE, final_key, source="otomatik-ayrıştırma")
                        st.success(f"✅ '{exam_title}' başarıyla işlendi ve sisteme eklendi! Cevap anahtarı otomatik okundu ve son sayfalar gizlendi.")
                        st.balloons()

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
                    if uploaded:
                        idx = st.session_state.get("_gen_key_idx")
                        safe_path = os.path.join(PDF_DIR, f"{safe_title}_guvenli.pdf")
                        parsing.crop_and_merge([(uploaded, idx if idx is not None else parsing.pdf_page_count(uploaded) - 1)], safe_path)
                    else:
                        safe_path = ""  # PDF yok, sadece cevap anahtarı / metin bazlı çalışılabilir
                    db.add_exam(title, gcat, safe_path, structure, final_key, source="manuel")
                    st.success(f"'{title}' {gcat} kategorisine eklendi.")
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
                    parsing.crop_and_merge([(res["Sözel"], sozel_idx), (res["Sayısal"], sayisal_idx)], safe_path)
                    db.add_exam(exam_title, LGS_CATEGORY, safe_path, LGS_STRUCTURE, {"Sözel": sozel_key, "Sayısal": sayisal_key}, source="otomatik-eba")
                    st.success(f"✅ {yil}: eklendi.")
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
                c1, c2, c3 = st.columns([4, 2, 1])
                c1.write(f"**{e['title']}**  ·  {e['category']}  ·  {e['source']}")
                c2.write(e["created_at"])
                if c3.button("Sil", key=f"del_{e['id']}"):
                    db.delete_exam(e["id"])
                    st.rerun()

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

        # ---------------- Hesap ayarları (admin şifresi) ----------------
        elif admin_section == "Hesap Ayarları":
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
