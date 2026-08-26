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
import json
import os
import re
import shutil
from datetime import datetime

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
# ÖNEMLİ - GÜVENLİK AÇIĞI (kapatıldı): İnternetten inen HAM kitapçıklar
# (cevap anahtarı sayfası hâlâ içindeyken) doğrudan "static/pdfs" içine
# yazılıyordu. O klasör herkese açık bir adresten sunulduğu ve dosya adları
# tahmin edilebilir olduğu için ("_indirilen_2026_sayisal.pdf"), hiç giriş
# yapmadan cevap anahtarına ulaşmak mümkündü. Ham indirmeler artık bu
# korumalı klasöre iniyor; öğrenciye sadece kırpılmış "guvenli" sürüm
# gösteriliyor.
INDIRME_DIR = os.path.join(PRIVATE_DIR, "_ham")
os.makedirs(INDIRME_DIR, exist_ok=True)


def _ham_dosyalari_temizle():
    """Eski sürümlerin 'static/pdfs' içine bıraktığı, cevap anahtarı İÇEREN
    ham indirme dosyalarını siler. İşlenmiş '..._guvenli.pdf' dosyalarına
    dokunulmaz. Döner: silinen dosya adları."""
    silinen = []
    try:
        for ad in os.listdir(PDF_DIR):
            if ad.startswith("_indirilen_") or ad.startswith("_iokbs_"):
                try:
                    os.remove(os.path.join(PDF_DIR, ad))
                    silinen.append(ad)
                except OSError:
                    pass
    except OSError:
        pass
    return silinen

# =====================================================================
#  DOSYA SÜRÜMÜ KONTROLÜ
# =====================================================================
# ÖNEMLİ - GERÇEK BİR KAZA: Kullanıcı GitHub'a app.py'yi yükledi ama db.py'yi
# eski bıraktı. Yeni app.py, db.py'de HENÜZ OLMAYAN fonksiyonları çağırınca
# uygulama anlaşılmaz bir hatayla ("InFailedSqlTransaction ... get_students")
# tamamen çöktü. Hata mesajı da yanıltıcıydı: asıl sorun bambaşka yerdeydi.
#
# Artık uygulama, açılır açılmaz yanındaki dosyaların sürümünü kontrol ediyor
# ve eksik olan varsa ÇÖKMEK YERİNE ne yapılması gerektiğini Türkçe yazıyor.
# app.py'nin beklediği sürüm. Yardımcı dosyalar aynı sürümü taşımalı.
SURUM = "2026-08-26.9"

_GEREKLI_PARCALAR = [
    ("db.py", db, ["pdf_kaydet", "pdf_getir", "pdf_saklananlar",
                   "exam_pdf_guncelle", "PDF_SAKLAMA_SINIRI"]),
    ("soru_bankasi.py", soru_bankasi, ["unite_kitabini_coz", "testleri_bul"]),
    ("parsing.py", parsing, ["gorsel_kucult", "cevap_anahtari_bul"]),
    ("bot.py", bot, ["scrape_bursluluk", "fetch_lgs_year", "_adres_varyantlari"]),
]


def _dosya_surumleri():
    """Her yardımcı dosyanın sürümünü ve güncel olup olmadığını verir."""
    satirlar = [{"Dosya": "app.py", "Sürüm": SURUM, "Durum": "✅ güncel"}]
    for ad, modul, gerekli in _GEREKLI_PARCALAR:
        eksikler = [x for x in gerekli if not hasattr(modul, x)]
        surum = getattr(modul, "SURUM", None)
        if eksikler:
            durum = "❌ ESKİ — GitHub'a yükleyin"
        elif surum != SURUM:
            durum = "⚠️ sürüm farklı — yüklemeniz iyi olur"
        else:
            durum = "✅ güncel"
        satirlar.append({"Dosya": ad, "Sürüm": surum or "(sürüm yok = eski)",
                         "Durum": durum})
    return satirlar


def _eksik_dosyalar():
    """Hangi yardımcı dosyalar eski sürümde kalmış?"""
    eksik = []
    for ad, modul, gerekli in _GEREKLI_PARCALAR:
        if any(not hasattr(modul, x) for x in gerekli):
            eksik.append(ad)
    return eksik


def _surum_uyarisi_goster():
    eksik = _eksik_dosyalar()
    if not eksik:
        return False
    st.error(
        "### ⚠️ Dosyalardan bazıları eski sürümde\n\n"
        "Uygulamanın yeni sürümü, yanındaki şu dosyaların da yeni sürümüne "
        "ihtiyaç duyuyor ama onlar hâlâ eski:\n\n"
        + "\n".join(f"- **{d}**" for d in eksik)
        + "\n\n**Yapmanız gereken:** Bilgisayarınızdaki `Masaüstü\\lgs_platform` "
        "klasöründen bu dosyaları da GitHub'a yükleyin "
        "(**Add file → Upload files → Commit changes**), sonra Streamlit'te "
        "**Manage app → ⋮ → Reboot app** deyin.\n\n"
        "Bu ekranı görmeye devam ederseniz, dosyanın GitHub'a gerçekten "
        "yüklendiğinden emin olun — Streamlit bazen eski kodu bir süre daha "
        "sunmaya devam ediyor.",
        icon="🛑",
    )
    st.stop()
    return True


@st.cache_resource(show_spinner=False)
def _veritabanini_hazirla():
    """Tabloları oluşturur ve yönetici hesabını hazırlar -- SADECE BİR KEZ.

    ÖNEMLİ - YAVAŞLIĞIN ASIL SEBEBİ BURASIYDI: Bu iki satır doğrudan dosyanın
    içinde duruyordu. Streamlit ise her tıklamada sayfanın TAMAMINI baştan
    çalıştırdığı için, her tuşa basışta yeniden çalışıyorlardı. Ölçüldü: tek
    bir ekran yenilemesinde 7 adet CREATE TABLE, 4 ALTER TABLE ve 5 kategori
    ekleme komutu, toplam ~30 komut Frankfurt'taki sunucuya gidip geliyordu.
    Kendi bilgisayarındaki dosyada bu neredeyse bedavaydı, o yüzden hiç fark
    edilmiyordu; ama internet üzerinden her komut ~50 ms demek ve tek tıklama
    1,5-3 saniye sürüyordu.

    Üstelik daha kötüsü: bu komutlar veriyi DEĞİŞTİREN işlem sayıldığı için
    okuma önbelleğini de her seferinde siliyorlardı -- yani eklediğim önbellek
    hiç işe yaramıyordu.

    st.cache_resource sayesinde artık uygulama ömrü boyunca yalnızca bir kez
    çalışıyor."""
    db.init_db()
    db.ensure_default_admin(config.ADMIN_USERNAME, "Yönetici", config.ADMIN_PASSWORD)
    return True


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


def _istatistik_al():
    """Ana sayfa sayaclarini getirir.

    ONEMLI - KISMI GUNCELLEME KORUMASI: Bu ozellik db.py'deki yeni bir
    fonksiyona dayaniyor. Kullanici GitHub'a sadece app.py'yi yukleyip
    db.py'yi eski birakirsa, uygulama "AttributeError" ile TAMAMEN cokuyordu
    (Streamlit Cloud'da tam olarak bu yasandi). Artik boyle bir durumda
    uygulama calismaya devam ediyor, sadece sayaclar gosterilmiyor ve
    ekranda hangi dosyanin eksik oldugu net sekilde yaziyor."""
    try:
        return db.genel_istatistikler(), None
    except AttributeError:
        return None, (
            "⚠️ Sayaçlar gösterilemiyor: **db.py dosyası eski sürümde.** "
            "Bu özellik `db.py` içindeki yeni bir fonksiyona ihtiyaç duyuyor. "
            "GitHub'a `app.py` ile birlikte **`db.py`, `bot.py`, `config.py`, "
            "`scoring.py` ve `soru_bankasi.py`** dosyalarını da yükleyin."
        )
    except Exception as e:
        return None, f"Sayaçlar hesaplanamadı: {e}"


def _kutu(anahtar, **kw):
    """CSS ile hedeflenebilen bir kutu ureti (st-key-<anahtar> sinifi alir).

    Streamlit'in eski surumlerinde 'key' parametresi yoktur; o durumda
    sade bir kutu doner (gorunum biraz farkli olur, ama calisir)."""
    try:
        return st.container(key=anahtar, **kw)
    except TypeError:
        return st.container(**kw)


def _sadelestir(s):
    """Aramada büyük/küçük harf ve Türkçe harf farkını yok sayar."""
    return re.sub(r"\s+", " ", (s or "").translate(TR_MAP)).strip().lower()


def _dogal_sira(baslik):
    """'Matematik Test 10' basligini DOGRU siralamak icin anahtar uretir.

    Duz metin siralamasinda 'Test 10' < 'Test 9' cikar (cunku '1' < '9').
    Bu fonksiyon metindeki sayilari gercek sayi olarak ayirir; boylece
    liste 1, 2, 3 ... 9, 10, 11 diye dogru sirayla dizilir."""
    parcalar = re.split(r"(\d+)", str(baslik or ""))
    return [int(p) if p.isdigit() else p.lower().translate(TR_MAP) for p in parcalar]


def _gun_bicimle(ham):
    """'2026-08-20T07:11:11' -> '20.08.2026' (sadece gun -- gunluk toplamlar icin)."""
    try:
        return datetime.fromisoformat(str(ham)).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return str(ham or "")[:10]


def _tarih_bicimle(ham):
    """'2026-08-20T07:11:11' -> '20.08.2026  07:11' (okunakli hale getirir)."""
    try:
        return datetime.fromisoformat(str(ham)).strftime("%d.%m.%Y  %H:%M")
    except (TypeError, ValueError):
        return str(ham or "")


def _tablo(df):
    """Basit, SABIT bir tablo cizer.

    ONEMLI - TABLETTEKI "KAYAN KUTU" SORUNU: Streamlit'in normal tablosu
    (st.dataframe) etkilesimli bir bilesendir; bir hucrenin metni sutuna
    sigmadiginda, uzerine gelindiginde/dokunuldugunda o hucreyi buyutup
    tablonun DISINA tasan ayri bir kutu olarak gosterir. Tablette parmakla
    dokunmak "uzerine gelme" sayildigi icin bu kutu surekli aciliyor ve
    ekranda bagimsiz, bozuk gorunumlu bir kare olarak kaliyordu (paylasilan
    ekran goruntusundeki "8. Sınıf (LGS)" karesi tam olarak buydu).

    Bu yuzden rapor tablolari artik duz HTML olarak ciziliyor: dokunmaya
    tepki vermez, hucre baloncugu yoktur, metin sigmazsa alt satira kayar
    ve dar ekranda yatay kaydirilabilir."""
    html = df.to_html(index=False, escape=False, border=0)
    st.markdown(
        f'<div class="lgs-tablo">{html}</div>',
        unsafe_allow_html=True,
    )


# =====================================================================
#  AÇILIR PENCERE (MODAL) YÖNETİMİ
# =====================================================================
# ÖNEMLİ - İKİ AYRI HATANIN KÖK NEDENİ BURASIYDI:
#
# 1) "Basmadığım halde sonuç penceresi kendiliğinden açılıyor":
#    Pencereyi açan işaret (ör. "_ozet_sonuc") oturum belleğine yazılıyor
#    ama SADECE "Kapat" düğmesine basılınca siliniyordu. Pencerenin sağ
#    üstündeki X ile ya da dışına dokunarak kapatıldığında işaret yerinde
#    kalıyor, bir sonraki ekran yenilemesinde pencere yeniden açılıyordu.
#    Üstelik başka bir teste geçilse bile ESKİ testin sonucu açılıyordu --
#    "her seferinde ilk çözdüğüm bilgisi geliyor" şikâyetinin sebebi buydu.
#    Çözüm: X ile kapatma da (on_dismiss) işareti siliyor.
#
# 2) Program tamamen çöküyordu (AssertionError):
#    Streamlit'te bir ekran yenilemesinde EN FAZLA BİR pencere açılabilir.
#    "Sınav Çöz" ile "Gelişim Raporum" aynı anda çizildiği için ikisi de
#    pencere açmak isteyince uygulama çöküyordu. Çözüm: aşağıdaki sayaç --
#    turda ilk isteyen pencereyi açar, ikincisi sessizce beklemeye alınır.
_PENCERE = {"acildi": False}


def _pencere_tasima_scripti():
    """Açılan pencereyi başlığından tutup SÜRÜKLEYEREK taşımayı sağlar.

    Streamlit'in kendi penceresi ekranın ortasına sabitlenmiştir ve
    kıpırdatılamaz; arkasındaki tabloyu görmek isteyen kullanıcı için bu
    can sıkıcıydı. Aşağıdaki küçük betik pencerenin başlık çubuğuna
    'tutulabilir' özelliği ekler (hem fare hem parmakla)."""
    try:
        import streamlit.components.v1 as _bilesenler
    except Exception:
        return
    _bilesenler.html(
        """
        <script>
        (function () {
          const doc = window.parent && window.parent.document;
          if (!doc) return;
          function hazirla() {
            // Streamlit surumune gore pencerenin etiketi degisebiliyor.
            const p = doc.querySelector(
              '[data-testid="stDialog"] [role="dialog"], [role="dialog"], [data-testid="stDialog"] section'
            );
            if (!p || p.dataset.lgsTasinabilir) return;
            p.dataset.lgsTasinabilir = "1";
            const bas = p.querySelector('h2') || p.firstElementChild;
            if (!bas) return;
            bas.style.cursor = "move";
            bas.title = "Bu başlıktan tutup pencereyi taşıyabilirsiniz";
            let sx = 0, sy = 0, dx = 0, dy = 0, tutuluyor = false;
            const nokta = (e) => (e.touches && e.touches[0]) ? e.touches[0] : e;
            function basla(e) {
              const n = nokta(e); tutuluyor = true;
              sx = n.clientX - dx; sy = n.clientY - dy;
              e.preventDefault();
            }
            function hareket(e) {
              if (!tutuluyor) return;
              const n = nokta(e);
              dx = n.clientX - sx; dy = n.clientY - sy;
              p.style.transform = "translate(" + dx + "px," + dy + "px)";
              e.preventDefault();
            }
            function bitir() { tutuluyor = false; }
            bas.addEventListener("mousedown", basla);
            bas.addEventListener("touchstart", basla, {passive: false});
            doc.addEventListener("mousemove", hareket);
            doc.addEventListener("touchmove", hareket, {passive: false});
            doc.addEventListener("mouseup", bitir);
            doc.addEventListener("touchend", bitir);
          }
          hazirla();
          setInterval(hazirla, 400);
        })();
        </script>
        """,
        height=0,
    )


def _pencere_ac(baslik, govde, temizlenecek=(), genislik="large"):
    """Tek bir açılır pencere çizer.

    baslik      : pencerenin üst yazısı
    govde       : pencerenin içini çizen fonksiyon
    temizlenecek: pencere kapandığında oturum belleğinden silinecek anahtarlar
                  (X ile kapatmada da silinir -- bkz. yukarıdaki not)"""
    if _PENCERE["acildi"]:
        return  # bu turda zaten bir pencere açıldı; ikincisi çökmeye yol açardı
    _PENCERE["acildi"] = True

    def _kapat_isaretlerini_sil():
        for k in temizlenecek:
            st.session_state.pop(k, None)

    def _govde_ve_kapat():
        govde()
        st.divider()
        if st.button("✖ Kapat", key=f"_kapat_{baslik}", use_container_width=True):
            _kapat_isaretlerini_sil()
            st.rerun()

    dialog = getattr(st, "dialog", None) or getattr(st, "experimental_dialog", None)
    if dialog is None:
        with st.container(border=True):
            st.markdown(f"#### {baslik}")
            _govde_ve_kapat()
        return

    kw = {"width": genislik}
    try:
        import inspect as _ins
        if "on_dismiss" in _ins.signature(dialog).parameters:
            kw["on_dismiss"] = _kapat_isaretlerini_sil
    except Exception:
        pass

    try:
        @dialog(baslik, **kw)
        def _p():
            _govde_ve_kapat()
        _p()
        _pencere_tasima_scripti()
    except Exception:
        # Streamlit'in eski sürümlerinde pencere açılamazsa program çökmesin.
        with st.container(border=True):
            st.markdown(f"#### {baslik}")
            _govde_ve_kapat()


def _ozet_penceresi(r):
    """Bir sinav sonucunun OZETINI (net, dogru/yanlis/bos) tam genislikte bir
    pencerede gosterir. Ayrintili soru dokumu burada YOK -- o, PIN korumali
    _grup_penceresi()'nde. Amac tablette sonucun kirpilmadan gorunmesi."""
    def _govde():
        st.markdown(
            f"**{r['exam_title']}**  ·  *{r['category']}*  \n"
            f"🗓️ **{_tarih_bicimle(r.get('created_at'))}**"
            + ("  ·  🎯 Yanlışları Düzeltme Turu" if r.get("mode") == "yanlis" else "")
        )
        ps = r["per_subject"]
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("✅ Doğru", sum(v["dogru"] for v in ps.values()))
        a2.metric("❌ Yanlış", sum(v["yanlis"] for v in ps.values()))
        a3.metric("⬜ Boş", sum(v["bos"] for v in ps.values()))
        a4.metric("📊 Toplam Net", r["total_net"])
        _tablo(pd.DataFrame([
            {
                "Ders": k, "✅ Doğru": v["dogru"], "❌ Yanlış": v["yanlis"],
                "⬜ Boş": v["bos"], "Net": v["net"],
            }
            for k, v in ps.items()
        ]))
        if r.get("weighted_score") is not None:
            st.caption(f"Tahmini ağırlıklı puan göstergesi: {r['weighted_score']}")
        st.info("Hangi soruyu yanlış yaptığını görmek için **Gelişim Raporum** sekmesindeki "
                "**🔍 Sınav Detayı** düğmesine dokun (PIN kodu gerekir).")

    _pencere_ac("📊 Sınav Sonucu", _govde, temizlenecek=("_ozet_sonuc",))


def _sonuc_sayilari(r):
    """Bir sonuc kaydindan (soru, dogru, yanlis, bos, net) degerlerini cikarir."""
    ps = r.get("per_subject") or {}
    d = sum(int(v.get("dogru", 0) or 0) for v in ps.values())
    y = sum(int(v.get("yanlis", 0) or 0) for v in ps.values())
    b = sum(int(v.get("bos", 0) or 0) for v in ps.values())
    return d + y + b, d, y, b, r.get("total_net")


def _gunluk_gruplar(results):
    """Aynı sınavın AYNI GÜN çözülen turlarını TEK SATIRDA toplar.

    ÖNEMLİ - KULLANICI İSTEĞİ: Bir denemeyi çözüp ardından "yanlışlarımı
    düzelt" turunu da yapınca listede iki ayrı satır çıkıyordu; üstelik
    düzeltme turunun neti (sadece birkaç soru üzerinden hesaplandığı için)
    "Net 0.0" gibi görünüp asıl sonucu gölgeliyordu. Artık aynı sınavın
    aynı gün yapılan bütün turları tek satır; satıra girilince turların
    hepsi ayrı ayrı açılıyor.

    Döner: en yeni gün en üstte olacak şekilde grup listesi."""
    gruplar = {}
    for r in results:
        gun = _gun_bicimle(r.get("created_at"))
        g = gruplar.setdefault((r["exam_id"], gun), {
            "id": f"{r['exam_id']}_{re.sub(r'[^0-9]', '', gun)}",
            "exam_id": r["exam_id"],
            "exam_title": r.get("exam_title"),
            "category": r.get("category"),
            "gun": gun,
            "turlar": [],
        })
        g["turlar"].append(r)
    cikti = []
    for g in gruplar.values():
        g["turlar"].sort(
            key=lambda x: ((x.get("attempt_no") or 0), str(x.get("created_at") or ""))
        )
        # Satırda gösterilecek "asıl" net: gün içindeki son TAM sınav.
        # (Düzeltme turunun neti sadece birkaç soruluktur, tek başına
        #  yanıltıcı olur.)
        _tamlar = [x for x in g["turlar"] if x.get("mode") != "yanlis"]
        g["esas"] = _tamlar[-1] if _tamlar else g["turlar"][-1]
        g["net"] = g["esas"].get("total_net")
        _s = _d = _y = _b = 0
        for x in g["turlar"]:
            a, b_, c_, d_, _n = _sonuc_sayilari(x)
            _s, _d, _y, _b = _s + a, _d + b_, _y + c_, _b + d_
        g["toplam"] = {"soru": _s, "dogru": _d, "yanlis": _y, "bos": _b}
        g["duzeltme"] = sum(1 for x in g["turlar"] if x.get("mode") == "yanlis")
        g["son_zaman"] = max(str(x.get("created_at") or "") for x in g["turlar"])
        cikti.append(g)
    cikti.sort(key=lambda g: g["son_zaman"], reverse=True)
    return cikti


def _tur_etiketi(r, sira):
    if r.get("mode") == "yanlis":
        return f"{sira}. tur · 🎯 Düzeltme"
    return f"{sira}. tur · 📝 Tam sınav"


def _grup_icerigi(g, pin_gerekli, vurgu_ders=None, silme=False):
    """Bir günün bütün turlarını tek pencerede gösterir."""
    st.markdown(
        f"**{g['exam_title']}**  ·  *{g.get('category') or ''}*  \n"
        f"🗓️ **{g['gun']}**  ·  {len(g['turlar'])} tur"
    )
    t = g["toplam"]
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("❓ Sorulan (tüm turlar)", t["soru"])
    a2.metric("✅ Doğru", t["dogru"])
    a3.metric("❌ Yanlış", t["yanlis"])
    a4.metric("⬜ Boş", t["bos"])
    st.caption(
        "Yukarıdaki sayılar günün BÜTÜN turlarının toplamıdır. Her turun "
        "kendi dökümü aşağıdaki sekmelerde."
    )
    st.divider()
    sekmeler = st.tabs([_tur_etiketi(r, i) for i, r in enumerate(g["turlar"], start=1)])
    for r, sek in zip(g["turlar"], sekmeler):
        with sek:
            if pin_gerekli:
                _detay_icerigi_pinli(r, vurgu_ders)
            else:
                _detay_icerigi(r, vurgu_ders)
            if silme:
                if st.button("🗑️ Bu turun sonucunu sil", key=f"_grupdel_{r['id']}",
                             use_container_width=True):
                    db.delete_result(r["id"])
                    st.session_state["_admin_flash"] = ("success", "Sonuç silindi.")
                    for k in ("_detay_acik", "_detay_grup", "_adm_grup", "_adm_ders"):
                        st.session_state.pop(k, None)
                    st.rerun()


def _degerlendirme_penceresi(secilenler):
    """Isaretlenen sinavlarin TOPLU degerlendirmesini tek pencerede gosterir.

    Icerik (istenen sirayla):
      1. Secilen her sinavin dokumu (dogru/yanlis/bos/net)
      2. Ayni sinavin ILK cozumu ile SONRAKI (ikinci sans) turlarinin
         karsilastirmasi -- ilkte kac dogru, sonrakinde kac dogru
      3. AYNI GUN cozulen her seyin toplami: kac soru, kac dogru, kac yanlis"""

    def _govde():
        st.markdown(f"**{len(secilenler)} sınav** seçildi.")

        # ---- 1) Seçilen sınavların dökümü ----
        _t, _d, _y, _b = 0, 0, 0, 0
        _satir = []
        for r in sorted(secilenler, key=lambda x: str(x.get("created_at") or "")):
            s, d, y, b, net = _sonuc_sayilari(r)
            _t, _d, _y, _b = _t + s, _d + d, _y + y, _b + b
            _satir.append({
                "Sınav": r["exam_title"],
                "🗓️ Tarih": _tarih_bicimle(r["created_at"]),
                "Tür": "🎯 Düzeltme turu" if r.get("mode") == "yanlis" else "📝 Tam sınav",
                "Sorulan": s, "Cevapladığı": d + y,
                "✅ Doğru": d, "❌ Yanlış": y, "⬜ Boş": b, "Net": net,
            })
        _cevaplanan = _d + _y
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("❓ Sorulan soru", _t)
        m2.metric("✏️ Cevapladığın", _cevaplanan)
        m3.metric("✅ Doğru", _d)
        m4.metric("❌ Yanlış", _y)
        m5.metric("⬜ Boş", _b)
        if _cevaplanan:
            st.progress(
                _d / _cevaplanan,
                text=(f"**Cevapladığın {_cevaplanan} sorunun %{round(100 * _d / _cevaplanan)}'ini "
                      f"doğru yaptın.**"
                      + (f"  ·  {_b} soruyu hiç işaretlemedin." if _b else "")),
            )
        elif _t:
            st.warning(f"Bu turlarda hiç soru işaretlenmemiş ({_t} soru boş bırakılmış).")
        st.caption(
            "**Sorulan soru**, seçtiğin turlarda karşına çıkan soruların toplamıdır — "
            "aynı sınavı iki kez çözdüysen iki turun soruları toplanır. "
            "**Cevapladığın**, gerçekten işaretlediğin soru sayısıdır."
        )
        _tablo(pd.DataFrame(_satir))

        # ---- 2) İlk çözüm / düzeltme turu karşılaştırması ----
        # ÖNEMLİ - KULLANICI HAKLIYDI, BURASI YANLIŞ SAYIYORDU:
        # Ekranda "90 sorudan 89 tanesini yanlış yaptın" yazıyordu; oysa
        # çocuk sadece 5 soru cevaplamış, 1 doğru 4 yanlış yapmış, 85 soruyu
        # HİÇ OKUMAMIŞTI. Eski kod "yanlış + boş"u tek torbaya koyup hepsine
        # "yanlış" diyordu. Boş bırakılan soru YANLIŞ DEĞİLDİR -- çözülmemiş
        # sorudur. Artık üçü de ayrı ayrı yazılıyor ve düzeltme turu
        # karşılaştırması SADECE gerçekten yanlış yapılan sorular üzerinden
        # kuruluyor.
        st.markdown("#### 🔁 İlk Çözüm ve Düzeltme Turu")
        _gruplar = {}
        for r in secilenler:
            _gruplar.setdefault(r["exam_id"], []).append(r)
        _karsilastirma, _cumleler = [], []
        for _eid, _rs in _gruplar.items():
            if len(_rs) < 2:
                continue
            _rs = sorted(_rs, key=lambda x: (x.get("attempt_no") or 0, str(x.get("created_at") or "")))
            _ilk, _son = _rs[0], _rs[-1]
            _si, _di, _yi, _bi, _ni = _sonuc_sayilari(_ilk)
            _ss, _ds, _ys, _bs, _ns = _sonuc_sayilari(_son)
            _cevaplanan_ilk = _di + _yi          # gerçekten işaretlediği soru
            _karsilastirma.append({
                "Sınav": _ilk["exam_title"],
                "Tur": len(_rs),
                "İlk: soru": _si,
                "İlk: cevapladığı": _cevaplanan_ilk,
                "İlk: ✅": _di, "İlk: ❌": _yi, "İlk: ⬜": _bi, "İlk net": _ni,
                "Son turda sorulan": _ss,
                "Son: ✅": _ds, "Son: ❌": _ys, "Son: ⬜": _bs, "Son net": _ns,
            })
            if _son.get("mode") == "yanlis":
                _cumleler.append(
                    f"**{_ilk['exam_title']}** — İlk turda {_si} sorunun "
                    f"**{_cevaplanan_ilk} tanesini cevapladın**: "
                    f"**{_di} doğru, {_yi} yanlış**"
                    + (f", {_bi} soruyu boş bıraktın. " if _bi else ". ")
                    + f"Düzeltme turunda sana **{_ss} soru** soruldu; "
                    f"bunların **{_ds} tanesini doğru** çözdün"
                    + (f", {_ys} tanesi yine yanlış" if _ys else "")
                    + (f", {_bs} tanesini boş bıraktın" if _bs else "")
                    + "."
                )
            else:
                _fark = round((_ns or 0) - (_ni or 0), 2)
                _cumleler.append(
                    f"**{_ilk['exam_title']}** — Sınavı baştan {len(_rs)} kez çözdün. "
                    f"İlk çözümde **{_di} doğru / {_yi} yanlış / net {_ni}**, "
                    f"son çözümde **{_ds} doğru / {_ys} yanlış / net {_ns}** "
                    + (f"(**{_fark:+} net**)." if _fark else "(net değişmedi).")
                )
        if _karsilastirma:
            for _c in _cumleler:
                st.markdown(f"- {_c}")
            with st.expander("📋 Aynı bilgiler tablo hâlinde"):
                _tablo(pd.DataFrame(_karsilastirma))
        else:
            st.caption(
                "Seçtiklerin arasında aynı sınavın birden fazla turu yok. "
                "Karşılaştırma için bir sınavın hem **ilk çözümünü** hem de "
                "**düzeltme turunu** birlikte işaretle."
            )

        # ---- 3) Aynı gün toplamları ----
        st.markdown("#### 🗓️ Gün Gün Toplam")
        _gunler = {}
        for r in secilenler:
            g = _gunler.setdefault(
                _gun_bicimle(r.get("created_at")),
                {"sinav": 0, "soru": 0, "dogru": 0, "yanlis": 0, "bos": 0, "net": 0.0},
            )
            s, d, y, b, net = _sonuc_sayilari(r)
            g["sinav"] += 1
            g["soru"] += s
            g["dogru"] += d
            g["yanlis"] += y
            g["bos"] += b
            g["net"] += float(net or 0)
        _tablo(pd.DataFrame([
            {
                "🗓️ Gün": k, "Sınav": v["sinav"], "Soru": v["soru"],
                "✅ Doğru": v["dogru"], "❌ Yanlış": v["yanlis"], "⬜ Boş": v["bos"],
                "Toplam Net": round(v["net"], 2),
            }
            for k, v in sorted(_gunler.items())
        ]))
        st.caption(
            "Not: Soru soru hangi soruyu yanlış yaptığını görmek için listedeki "
            "**🔍 Sınav Detayı** düğmesini kullan (PIN kodu ile korunur)."
        )

    _pencere_ac(
        "📊 Seçilen Sınavların Değerlendirmesi", _govde,
        temizlenecek=("_dege_acik", "_dege_ids"),
    )


def _detay_icerigi(r, vurgu_ders=None):
    """Bir sınav sonucunun ayrıntılı dökümünü çizer."""
    st.markdown(
        f"**{r['exam_title']}**  ·  *{r['category']}*  \n"
        f"🗓️ **{_tarih_bicimle(r.get('created_at'))}**"
        + ("  ·  🎯 Yanlışları Düzeltme Turu" if r.get("mode") == "yanlis" else "")
    )
    ps = r["per_subject"]
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("✅ Doğru", sum(v["dogru"] for v in ps.values()))
    a2.metric("❌ Yanlış", sum(v["yanlis"] for v in ps.values()))
    a3.metric("⬜ Boş", sum(v["bos"] for v in ps.values()))
    a4.metric("📊 Toplam Net", r["total_net"])
    if r.get("weighted_score") is not None:
        st.caption(f"Tahmini ağırlıklı puan göstergesi: {r['weighted_score']}")

    detay = r.get("answers_detail")
    if not detay:
        st.warning(
            "Bu sonuç için soru bazlı döküm kaydedilmemiş. Soru soru döküm, "
            "**bu güncellemeden önce çözülen** sınavlarda tutulmuyordu. Bu sınavı "
            "yeniden çözerseniz dökümü görebilirsiniz."
        )
        return
    dersler = [(b, d) for b, ds in detay.items() for d in ds]
    if vurgu_ders:
        dersler.sort(key=lambda x: x[1] != vurgu_ders)

    # ÖNEMLİ - KULLANICI GERİ BİLDİRİMİ ("PIN girdim ama soruları göstermiyor"):
    # Burada eskiden SADECE yanlış/boş sorular listeleniyordu. Bir derste hiç
    # yanlış yoksa tablo hiç çizilmiyor, kullanıcı "hiçbir şey göstermiyor"
    # diye görüyordu. Artık BÜTÜN sorular listeleniyor; isterse yalnızca
    # yanlışlara süzebiliyor.
    _sadece_yanlis = st.toggle(
        "Sadece yanlış ve boş soruları göster",
        value=False,
        key=f"_detay_suz_{r['id']}",
        help="Kapatırsan doğru yaptığın sorular da listelenir.",
    )
    _durum_yazi = {"dogru": "✅ Doğru", "yanlis": "❌ Yanlış", "bos": "⬜ Boş"}
    sekmeler = st.tabs([d for _, d in dersler])
    for (bolum, ders), sek in zip(dersler, sekmeler):
        with sek:
            satirlar = list(detay[bolum][ders] or [])
            if not satirlar:
                st.info(f"{ders} için soru dökümü kaydedilmemiş.")
                continue
            _d = sum(1 for x in satirlar if x.get("durum") == "dogru")
            _y = sum(1 for x in satirlar if x.get("durum") == "yanlis")
            _b = sum(1 for x in satirlar if x.get("durum") == "bos")
            st.markdown(
                f"**{ders}** — {len(satirlar)} soru · ✅ {_d} doğru · "
                f"❌ {_y} yanlış · ⬜ {_b} boş"
            )
            gosterilecek = [x for x in satirlar if x.get("durum") != "dogru"] \
                if _sadece_yanlis else satirlar
            if not gosterilecek:
                st.success(f"{ders}: tüm sorular doğru! 🎉")
                continue
            dfd = pd.DataFrame([
                {
                    "Soru No": x.get("soru"),
                    "Senin Cevabın": (x.get("verilen") or "—").replace("Boş", "—"),
                    "Doğru Cevap": x.get("dogru_cevap"),
                    "Durum": _durum_yazi.get(x.get("durum"), x.get("durum")),
                }
                for x in gosterilecek
            ])
            _tablo(dfd)


def _detay_icerigi_pinli(r, vurgu_ders=None):
    """_detay_icerigi ile aynı, ama önce PIN sorar.

    PIN, yöneticinin 'Hesap Ayarları' bölümünde belirlediği koddur; amaç
    öğrencinin doğru cevapları izinsiz görmesini engellemek. İzin yalnızca
    AÇIK OLAN pencere için geçerlidir: pencere kapanınca düşer."""
    pin = db.get_setting("rapor_pin", "") or ""
    _izin = f"_pin_ok_{r['id']}"
    if pin and not st.session_state.get(_izin):
        st.warning("Bu döküm doğru cevapları içerir, PIN kodu ile korunuyor.")
        girilen = st.text_input(
            "PIN kodu", type="password", key=f"_pin_giris_{r['id']}",
            help="Bu kodu yönetici belirler (Admin Paneli → Hesap Ayarları).",
        )
        if st.button("🔓 Aç", key=f"_pin_ac_{r['id']}", type="primary",
                     use_container_width=True):
            if girilen == pin:
                st.session_state[_izin] = True
                st.rerun()
            else:
                st.error("PIN kodu yanlış.")
        return
    _detay_icerigi(r, vurgu_ders)


def _grup_penceresi(g, pin_gerekli=True, vurgu_ders=None, silme=False,
                    temizlenecek=()):
    """Bir günün turlarını tek pencerede açar."""
    _temiz = list(temizlenecek) + [f"_pin_ok_{r['id']}" for r in g["turlar"]]
    _pencere_ac(
        f"🔍 {g['exam_title']} · {g['gun']}",
        lambda: _grup_icerigi(g, pin_gerekli, vurgu_ders, silme),
        temizlenecek=tuple(_temiz),
    )


# NOT: Buradaki _detay_penceresi() kaldırıldı. Tek bir sonucu açan o
# pencerenin yerini, aynı sınavın aynı gün yapılan BÜTÜN turlarını tek
# pencerede sekme sekme gösteren _grup_penceresi() aldı (bkz. yukarısı).


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
def _pdf_page_image(path, mtime_ns, page_num, dpi=175):
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
    # ÖNEMLİ - NETLİK: Çözünürlük 130 dpi / kalite 80 idi; büyük bir
    # bilgisayar ekranında yazılar puslu görünüyordu ("PC'de neden net
    # gözükmüyor"). Ölçüldü: 175 dpi + kalite 88 ile sayfa 1075px yerine
    # 1447px genişliğinde geliyor, dosya 115 KB'tan 211 KB'a çıkıyor ve
    # çizim süresi DEĞİŞMİYOR (0,11 sn). Yani netlik bedavaya iki katına
    # yakın arttı.
    pil.convert("RGB").save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _yenile(sadece_bolum=True):
    """Ekrani yeniler.

    ONEMLI - HIZ: Streamlit'te normalde tek bir dugmeye basmak SAYFANIN
    TAMAMINI bastan calistirir; yani PDF sayfasini cevirmek optik formu da,
    sayaclari da, veritabani sorgularini da yeniden yaptiriyordu. Streamlit'in
    'fragment' (parca) ozelligi ile artik sadece ilgili parca yenileniyor.
    Eski Streamlit surumlerinde bu ozellik yoksa eski davranisa donuluyor."""
    if sadece_bolum:
        try:
            st.rerun(scope="fragment")
        except Exception:
            pass
    st.rerun()


def _fragman(f):
    """Bir fonksiyonu 'bagimsiz yenilenebilir parca' haline getirir."""
    frag = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
    return frag(f) if frag else f


_NETLIK = {
    "🔍 Normal": 175,
    "🔍 Yüksek netlik": 220,
    "🔍 En yüksek (yavaş)": 270,
    "⚡ Hızlı (düşük netlik)": 130,
}


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
                _yenile()
        with nav3:
            if st.button("Sonraki ▶", key=f"next_{where}_{path}", use_container_width=True,
                         disabled=cur >= page_count - 1):
                st.session_state[state_key] = cur + 1
                _yenile()
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
                _yenile()

    _nav_row("top")

    _netlik_ad = st.session_state.get("_pdf_netlik") or "🔍 Normal"
    try:
        img_bytes = _pdf_page_image(
            path, os.stat(path).st_mtime_ns, st.session_state[state_key],
            dpi=_NETLIK.get(_netlik_ad, 175),
        )
    except Exception as e:
        st.error(f"Sayfa gösterilirken hata oluştu: {e}")
        return

    with st.container(height=height, border=True):
        st.image(img_bytes, use_container_width=True)

    _c1, _c2 = st.columns([2, 3])
    _c1.caption(f"📄 Sayfa {st.session_state[state_key] + 1} / {page_count}")
    with _c2:
        st.selectbox(
            "Görüntü netliği",
            list(_NETLIK.keys()),
            key="_pdf_netlik",
            label_visibility="collapsed",
            help=("Yazılar puslu görünüyorsa netliği artırın. Yüksek netlik "
                  "sayfayı biraz daha yavaş getirir ama okumak kolaylaşır."),
        )
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


def _deneme_ekle(baslik, kategori, guvenli_yol, yapi, anahtar, source="manuel",
                 pdf_path_original=None, source_url=None):
    """Denemeyi kaydeder VE kitapçığın kalıcı kopyasını veritabanına yazar.

    ÖNEMLİ - "PDF DOSYASI SUNUCUDA BULUNAMADI" HATASININ ASIL ÇÖZÜMÜ:
    Streamlit'in ücretsiz bulut sunucusunda uygulamanın diski her yeniden
    başlatmada/güncellemede SIFIRLANIYOR. Sınav kaydı kalıcı veritabanında
    durduğu için listede görünmeye devam ediyor ama kitapçık dosyası
    silinmiş oluyordu -- kullanıcının gördüğü "hiçbir sınava giremiyorum"
    sorunu tam olarak buydu. Artık her deneme eklendiğinde kitapçık da
    veritabanına yazılıyor; dosya kaybolursa oradan geri getiriliyor.

    Dosya çok büyükse önce sayfaları resme çevirerek küçültülüyor
    (ölçüldü: 20 sayfa 4,7 MB -> 1,8 MB; ekranda fark edilmiyor çünkü
    uygulama PDF'i zaten sayfa sayfa resim olarak gösteriyor).

    Döner: (exam_id, uyari_metni_or_None)"""
    exam_id = db.add_exam(
        baslik, kategori, guvenli_yol, yapi, anahtar,
        source=source, pdf_path_original=pdf_path_original, source_url=source_url,
    )
    if not guvenli_yol or not os.path.exists(guvenli_yol):
        return exam_id, None
    # FİLİGRAN: ÖSYM/MEB kitapçıklarının üstündeki büyük gri damga
    # yazıların okunmasını zorlaştırıyor. Damga PDF'in içinde ayrı bir
    # katman olarak durduğu için tamamen ve kayıpsız çıkarılabiliyor.
    try:
        parsing.filigrani_kaldir(guvenli_yol)
    except Exception:
        pass
    try:
        if os.path.getsize(guvenli_yol) > db.PDF_SAKLAMA_SINIRI:
            parsing.gorsel_kucult(guvenli_yol, sinir=db.PDF_SAKLAMA_SINIRI)
        with open(guvenli_yol, "rb") as f:
            ok, mesaj = db.pdf_kaydet(exam_id, os.path.basename(guvenli_yol), f.read())
        if not ok:
            return exam_id, (
                f"⚠️ **{baslik}** eklendi ama kitapçığın kalıcı kopyası saklanamadı "
                f"({mesaj}). Sunucu yeniden başlarsa bu denemenin PDF'i kaybolabilir."
            )
    except Exception as e:
        return exam_id, f"⚠️ **{baslik}**: kalıcı kopya yazılamadı ({e})."
    return exam_id, None


def _pdf_yolunu_hazirla(exam):
    """Denemenin PDF'ini KESİNLİKLE kullanılabilir hâle getirir.

    ÖNEMLİ - "PDF DOSYASI SUNUCUDA BULUNAMADI" HATASI: Streamlit'in ücretsiz
    bulut sunucusunda uygulamanın diski her yeniden başlatmada sıfırlanıyor.
    Sınav kaydı kalıcı veritabanında durduğu için listede görünüyor ama
    dosyası silinmiş oluyordu. Artık üç aşama var:
      1. Dosya diskte duruyorsa doğrudan kullanılır.
      2. Yoksa veritabanında saklanan kopyası diske geri yazılır.
      3. O da yoksa ve kaynak adresi biliniyorsa (MEB arşivinden inen
         kitapçıklar) dosya yeniden indirilir.
    Döner: (yol, mesaj). Yol None ise mesaj sebebi anlatır."""
    yol = exam.get("pdf_path") or ""
    if yol and os.path.exists(yol) and os.path.getsize(yol) > 0:
        return yol, None

    hedef = yol if yol else os.path.join(PDF_DIR, f"exam_{exam['id']}.pdf")
    os.makedirs(os.path.dirname(hedef) or PDF_DIR, exist_ok=True)

    # 2) Veritabanındaki kopya
    try:
        ad, veri = db.pdf_getir(exam["id"])
    except Exception:
        ad, veri = None, None
    if veri:
        try:
            with open(hedef, "wb") as f:
                f.write(veri)
            return hedef, None
        except Exception as e:
            return None, f"Kayıtlı kopya diske yazılamadı: {e}"

    # 3) Kaynak adresinden yeniden indirme
    kaynak = exam.get("source_url")
    if kaynak:
        try:
            ok, mesaj = bot.fetch_from_url(kaynak, hedef)
        except Exception as e:
            ok, mesaj = False, str(e)
        if ok and os.path.exists(hedef) and os.path.getsize(hedef) > 0:
            return hedef, None
        return None, f"Kitapçık kaynağından indirilemedi: {mesaj}"
    return None, "yok"


@st.cache_data(show_spinner=False, max_entries=32)
def _pdf_yolu_onbellekli(exam_id, pdf_path, source_url):
    """Aynı deneme için geri yükleme işini oturumda bir kez yapar."""
    return _pdf_yolunu_hazirla(
        {"id": exam_id, "pdf_path": pdf_path, "source_url": source_url}
    )


@_fragman
def _pdf_bolumu(pdf_path, baslik, height=780):
    """Kitapcik gosterimi -- kendi basina yenilenen bagimsiz bir parca.

    Boylece 'Sonraki sayfa' dendiginde optik form, sayaclar ve veritabani
    sorgulari yeniden calismiyor; sadece PDF resmi degisiyor."""
    st.subheader(baslik)
    if not pdf_path or not os.path.exists(pdf_path):
        st.error(
            "PDF dosyası sunucuda bulunamadı. Bu deneme muhtemelen bir önceki "
            "yayına almadan (deploy) kalan bir kayıt; admin panelinden silip "
            "yeniden eklemeniz gerekebilir."
        )
        return
    if os.path.getsize(pdf_path) == 0:
        st.error("PDF dosyası boş (0 byte) kaydedilmiş. Denemeyi silip yeniden eklemeniz gerekiyor.")
        return
    try:
        show_pdf(pdf_path, height=height)
    except Exception as e:
        st.error(f"PDF gösterilirken bir hata oluştu: {e}")


@_fragman
def _optik_form(exam_id, attempt_no, student_name, aktif_yapi, aktif_anahtar,
                ikinci_sans, tam_anahtar=None):
    """Optik form -- kendi basina yenilenen bagimsiz bir parca.

    ONEMLI - HIZ: Her sik isaretlemesi eskiden SAYFANIN TAMAMINI yeniden
    calistiriyordu: kitapcik resmi, sayaclar, deneme listesi, hatta gizli
    duran diger sekmeler bile. 40 soruluk bir testte bu, 40 kez gereksiz
    tam yenileme demekti. 'Parca' haline getirildigi icin artik sik
    isaretlendiginde SADECE bu form yeniden ciziliyor."""
    # ---- Bu deneme numarası henüz bitirilmemiş: formu göster ----
    st.subheader("📝 Optik Form")
    if ikinci_sans:
        _ikinci_n = sum(
            m["count"] for b in aktif_yapi.values() for m in b.values()
        )
        st.info(
            f"🎯 **Yanlışları Düzeltme Turu** — sadece daha önce yanlış yaptığın veya boş "
            f"bıraktığın **{_ikinci_n} soru** soruluyor. Soru numaraları "
            f"kitapçıktakiyle aynı."
        )
    if student_name:
        st.caption(
            "İşaretlediğiniz cevaplar otomatik kaydedilir; sayfa kapanırsa veya "
            "internet kesilirse tekrar açtığınızda kaldığınız yerden devam edebilirsiniz."
        )
    elif st.session_state.get("is_admin"):
        # Yönetici giriş yapmış ama sonucun kime yazılacağı belli değil.
        # Eskiden burada "kimse giriş yapmadı" yazıyordu; kullanıcı giriş
        # yaptığı hâlde bu uyarıyı görüp ne yapacağını bilemiyordu.
        _liste = db.get_students()
        if _liste:
            st.warning(
                "Yönetici olarak giriş yaptınız, ama sınav sonucunun **kimin adına** "
                "kaydedileceği seçilmedi. Aşağıdan öğrenciyi seçin; işaretlediğiniz "
                "cevaplar kaybolmaz."
            )
            _ad = {x["username"]: x["display_name"] for x in _liste}
            _sec = st.selectbox(
                "Sonuç kimin adına kaydedilsin?",
                [None] + list(_ad),
                format_func=lambda u: "— Seçilmedi —" if u is None else _ad.get(u, u),
                key=f"_optik_ogrenci_{exam_id}",
            )
            if _sec and st.button("Bu öğrenci olarak devam et",
                                  key=f"_optik_ogrenci_btn_{exam_id}",
                                  use_container_width=True, type="primary"):
                st.session_state.student_name = _sec
                st.session_state.student_display_name = _ad.get(_sec, _sec)
                st.session_state.pop("_ogrenci_secimi_birakildi", None)
                st.rerun()
        else:
            st.warning(
                "Sistemde kayıtlı öğrenci yok, bu yüzden sonuç kaydedilemez. "
                "**Admin Paneli → Öğrenci Hesapları** bölümünden bir öğrenci ekleyin."
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
    buf_key = f"_ans_buf_{exam_id}_{attempt_no}"
    saved = (
        st.session_state.get(buf_key)
        or db.load_progress(exam_id, student_name, attempt_no)
        or {}
    )

    # "İkinci şans" modunda yapı, sadece yanlış yapılan soruları
    # içeren küçültülmüş sürümdür (aktif_yapi); normal turda ise
    # denemenin kendi yapısıdır.
    all_subjects = [
        (section, subject)
        for section, subjects in aktif_yapi.items()
        for subject in subjects
    ]
    user_answers = {section: {} for section in aktif_yapi}

    # ÖNEMLİ - 5 ŞIKLI SINAVLAR (KPSS/ALES/YDS): Şık listesi eskiden
    # "A B C D" olarak SABİTTİ. LGS ve bursluluk 4 şıklı olduğu için
    # sorun çıkmıyordu; ama KPSS gibi 5 şıklı bir sınav eklendiğinde
    # çocuk "E" cevabını İŞARETLEYEMİYOR, cevabı E olan her soru
    # zorunlu olarak yanlış/boş sayılıyordu. Artık şık sayısı, o
    # dersin cevap anahtarına bakılarak DERS DERS belirleniyor:
    # anahtarda "E" geçen derste 5 şık, geçmeyende 4 şık gösterilir.
    def _dersin_sikleri(section, subject):
        # Düzeltme turunda elimizdeki anahtar sadece seçili soruları
        # içerir; o soruların hiçbirinin cevabı E değilse şık sayısı
        # yanlışlıkla 4'e düşerdi. Bu yüzden önce denemenin TAM
        # anahtarına bakılıyor.
        _harfler = set()
        try:
            _bolum = ((tam_anahtar or aktif_anahtar) or {}).get(section) or {}
            for _h in (_bolum.get(subject) or []):
                if isinstance(_h, str):
                    _harfler.add(_h.strip().upper())
        except Exception:
            pass
        _son = "E" if "E" in _harfler else "D"
        return [c for c in "ABCDE" if c <= _son] + ["Boş"]

    # PDF görüntüleyici ile aynı yükseklikte, kaydırılabilir bir
    # kutu içinde gösteriliyor -- böylece PDF bittiğinde Optik
    # Form aşağıya doğru uzayıp gitmiyor, ikisi de aynı boyda
    # kalıp kendi içinde kayıyor.
    with st.container(height=780, border=True):
        subject_tabs = st.tabs([s for _, s in all_subjects])
        for (section, subject), stab in zip(all_subjects, subject_tabs):
            with stab:
                _meta = aktif_yapi[section][subject]
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
                options = _dersin_sikleri(section, subject)
                _bos_index = options.index("Boş")
                saved_subject = saved.get(section, {}).get(subject, [])
                # ÖNEMLİ - "SORU NUMARASI GÖRÜNMÜYOR, YA KAYARSA?":
                # Numara satırın sonunda, ders adının arkasında kalıyordu
                # ("İngilizce - Soru 7"). Tablette hızlı işaretlerken göz
                # numarayı değil ders adını görüyor. Artık numara satırın
                # BAŞINDA ve kalın; ayrıca her bölümün üstünde kitapçıktaki
                # numara aralığı yazıyor ki kayma ihtimali gözle görülsün.
                if numaralar:
                    _aralik = (f"{numaralar[0]}" if len(numaralar) == 1
                               else f"{numaralar[0]} – {numaralar[-1]}")
                    _bas = ("ℹ️ Bu testin soruları kitapçıkta "
                            f"**{numaralar[0]}. sorudan** başlıyor. "
                            if numaralar[0] != 1 else "")
                    st.caption(
                        f"{_bas}Aşağıdaki numaralar **kitapçıktakilerle birebir "
                        f"aynıdır** ({_aralik}). PDF'te gördüğünüz numarayı "
                        f"formda da arayın."
                    )
                answers = []
                for _sira, _soru_no in enumerate(numaralar):
                    prev = saved_subject[_sira] if _sira < len(saved_subject) else "Boş"
                    default_index = options.index(prev) if prev in options else _bos_index
                    ans = st.radio(
                        f"**{_soru_no}.** soru",
                        options,
                        index=default_index,
                        horizontal=True,
                        key=f"ans_{exam_id}_{attempt_no}_{subject}_{_soru_no}",
                    )
                    answers.append(ans)
                user_answers[section][subject] = answers

    # Giriş yapılmış olsun ya da olmasın, cevaplar her zaman
    # oturum belleğine yazılır (bkz. yukarıdaki buf_key notu).
    st.session_state[buf_key] = user_answers
    # ÖNEMLİ - HIZ: İlerleme her ekran yenilemesinde veritabanına
    # yazılıyordu. Ama ekran, cevap değişmeden de yenileniyor
    # (sayfa çevirme, görünüm değiştirme...). Boşuna yazmak hem
    # Frankfurt'a fazladan gidiş-dönüş, hem de her yazma okuma
    # önbelleğini sıfırladığı için tüm sayfayı yeniden sorgulatıyordu.
    # Artık sadece cevaplar GERÇEKTEN değiştiyse yazılıyor.
    _son_key = f"_son_kayit_{exam_id}_{attempt_no}"
    if student_name and st.session_state.get(_son_key) != user_answers:
        db.save_progress(exam_id, student_name, attempt_no, user_answers)
        st.session_state[_son_key] = json.loads(json.dumps(user_answers))

    # ---- İlerleme sayacı: kaç soru işaretlendi / toplam kaç soru ----
    _total_q = sum(
        aktif_yapi[sec][sub]["count"] for sec, sub in all_subjects
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
        key=f"submit_{exam_id}_{attempt_no}",
    )

    if submitted and not student_name:
        st.error(
            "Sonuç kaydedilemedi: sınavın **kimin adına** çözüldüğü belli değil. "
            "Yukarıdaki kutudan öğrenciyi seçin (ya da soldaki menüden öğrenci "
            "girişi yapın). **İşaretlediğiniz cevaplar duruyor** — seçimden sonra "
            "bu düğmeye tekrar basmanız yeterli."
        )
    elif submitted:
        per_subject, total_net, weighted_score = scoring.score_exam(
            user_answers, aktif_anahtar, aktif_yapi
        )
        answers_detail = scoring.build_answer_detail(
            user_answers, aktif_anahtar, aktif_yapi
        )
        db.add_result(
            exam_id, student_name, per_subject, total_net,
            weighted_score, answers_detail=answers_detail, attempt_no=attempt_no,
            mode="yanlis" if ikinci_sans else "tam",
        )
        db.clear_progress(exam_id, student_name, attempt_no)
        st.session_state.pop(buf_key, None)
        st.success("Sınav tamamlandı! Sonuçlarınız kaydedildi.")
        st.session_state["_sinav_balon"] = True
        st.rerun()


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
        /* NOT: Burada eskiden stStatusWidget de gizleniyordu. Ama sağ üstte
           "çalışıyor" anlamına gelen KOŞAN ADAM animasyonu tam olarak o
           bileşen; gizlenince kullanıcı bir işlem sürerken hiçbir belirti
           göremiyordu. Geri açıldı. */
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

        /* Rapor tablolari (bkz. _tablo()): dokunmaya tepki vermeyen, dar
           ekranda yatay kaydirilabilen sade tablo. */
        .lgs-tablo {overflow-x: auto; -webkit-overflow-scrolling: touch;}
        .lgs-tablo table {
            border-collapse: collapse; width: 100%; font-size: 0.95rem;
        }
        .lgs-tablo th {
            background: #E2E8F0; color: #0F172A; text-align: left;
            padding: 10px 12px; border-bottom: 2px solid #CBD5E1; white-space: nowrap;
        }
        .lgs-tablo td {
            padding: 9px 12px; border-bottom: 1px solid #E2E8F0; vertical-align: top;
        }
        .lgs-tablo tr:nth-child(even) td {background: #F8FAFC;}

        /* ---- TABLET / TELEFON: yan yana iki sutun sigmadiginda alt alta ---- */
        /* ÖNEMLİ: Bu eşik önce 1100px idi; ama tabletin YATAY (yan çevrilmiş)
           genişliği çoğu modelde 1024-1080px olduğu için, tam da yan yana
           çalışması gereken durumda alt alta geçiyordu. Eşik 700px'e indirildi:
           artık sadece TELEFON ve tabletin DİKEY kullanımında alt alta geçer,
           yatay çevrildiğinde PDF ile optik form yan yana kalır. Ayrıca
           öğrenci bunu "Görünüm" düğmesinden elle de seçebiliyor.
           Ölçüm: tablet DİKEY ~768-820px (alt alta olmalı), tablet YATAY
           ~1024-1180px (yan yana olmalı). Bu yüzden eşik 900px. */
        @media (max-width: 900px) {
            div[data-testid="stHorizontalBlock"] {flex-wrap: wrap !important;}
            div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
                min-width: 100% !important; flex: 1 1 100% !important;
            }
            .block-container {padding-left: 0.8rem; padding-right: 0.8rem;}

            /* ÖNEMLİ - TABLETTE "LİSTE AŞAĞIYA DOĞRU UZAYIP GİDİYOR" SORUNU:
               Yukarıdaki kural, DAR ekranda yan yana duran her şeyi alt alta
               diziyor. Bu, PDF ile optik form için doğru; ama sayaç kutuları
               ve rapor listesindeki tek satırlık kayıtlar için felaketti --
               4 sayaç 4 ayrı satır, her sınav kaydı 4 ayrı satır oluyor,
               sayfa metrelerce uzuyordu. Aşağıdaki iki kural bu iki yeri
               kuraldan MUAF tutar. */
            div[data-testid="stColumn"]:has(div[data-testid="stMetric"]) {
                min-width: 46% !important; flex: 1 1 46% !important;
            }
            /* NOT: Asagidaki secicilerin uzun olmasinin sebebi, yukaridaki
               "hepsini alt alta diz" kuralindan DAHA BELIRGIN olmasi
               gerekmesidir; aksi halde tarayici o kurali uygular. */
            /* Esit genislikte, dar ekranda da yan yana kalan dugme satiri */
            .st-key-lgs_satir div[data-testid="stHorizontalBlock"] {
                flex-wrap: nowrap !important;
            }
            .st-key-lgs_satir div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
                min-width: 0 !important; flex: 1 1 0 !important;
            }
            .st-key-lgs_kompakt div[data-testid="stHorizontalBlock"] {
                flex-wrap: nowrap !important;
            }
            .st-key-lgs_kompakt div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
                min-width: 0 !important;
            }
            .st-key-lgs_kompakt div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-child(1) {
                flex: 0 0 9% !important;
            }
            .st-key-lgs_kompakt div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-child(2) {
                flex: 1 1 45% !important;
            }
            .st-key-lgs_kompakt div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-child(3) {
                flex: 0 0 16% !important;
            }
            .st-key-lgs_kompakt div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-child(4) {
                flex: 0 0 28% !important;
            }
        }
        /* Rapor listesindeki satırlar sıkışık ve okunaklı dursun */
        .st-key-lgs_kompakt div[data-testid="stColumn"] {padding-top: 0 !important;}
        .st-key-lgs_kompakt .stButton>button {
            padding: 0.35rem 0.6rem; font-size: 0.85rem; font-weight: 600;
        }
        /* Dar ekranda ders sonuc kutulari da alt alta rahat sigsin */
        @media (max-width: 640px) {
            div[data-testid="stMetric"] {padding: 10px 8px;}
        }
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
        /* ÖNEMLİ - TELEFONDA DOKUNMA SORUNU:
           Yuvarlak seçenek kutucukları parmak için çok küçüktü. Dar
           ekranda seçenekler yükseltilip çerçeveleniyor. DİKKAT: burada
           "width: 100%" YOK -- eskiden vardı ve optik formdaki A/B/C/D/E
           şıklarını da alt alta tek sütuna diziyordu; 120 soruluk bir
           sınavda form uçsuz bucaksız uzuyordu. */
        @media (max-width: 900px) {
            div[data-testid="stRadio"] div[role="radiogroup"] {
                gap: 8px !important;
            }
            div[data-testid="stRadio"] div[role="radiogroup"] > label {
                min-height: 44px; display: flex !important; align-items: center;
                padding: 6px 10px !important; border: 1px solid #CBD5E1;
                border-radius: 12px; background: #F8FAFC; min-width: 56px;
            }
            div[data-testid="stRadio"] div[role="radiogroup"] > label * {
                pointer-events: none;   /* dokunma her zaman etikete gitsin */
            }
            /* Görünüm düğmeleri parmakla rahat basılsın */
            .st-key-lgs_gorunum .stButton>button {
                min-height: 48px; font-size: 1rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def build_generic_structure(subject_rows):
    """subject_rows: [("Türkçe", 10), ...] -> tek bölümlü ('Genel') structure sözlüğü."""
    return {"Genel": {name: {"count": count, "coef": 1} for name, count in subject_rows}}


# ---------------------------------------------------------------- app shell

# ÖNEMLİ - TELEFONDA HİÇBİR DÜĞME ÇALIŞMIYOR GİBİ GÖRÜNÜYORDU:
# Kenar menüsü telefonda sayfanın ÜSTÜNE açılan bir çekmecedir ve ekranın
# yaklaşık dörtte üçünü kaplar. Menü açık kaldığı sürece arkadaki hiçbir
# şeye dokunulamaz -- dokunuş menüye gider. Program menüyü "her zaman
# açık" başlatıyordu; ölçüldü: 390 piksel genişliğinde bir telefonda
# "Yan yana / Alt alta" düğmeleri menünün altında kalıyor ve basıldığında
# hiçbir şey olmuyordu. Kullanıcının "telefonda çalışmadı" dediği sorun
# buydu.
# Çözüm: menü SADECE henüz giriş yapılmamışken açık gelir (giriş kutusu
# görünsün diye); giriş yapılır yapılmaz kapanır ve ekran tamamen
# öğrenciye kalır.
st.set_page_config(
    page_title=config.APP_TITLE, layout="wide", page_icon="📚",
    initial_sidebar_state=(
        "collapsed"
        if (st.session_state.get("student_name") or st.session_state.get("is_admin"))
        else "expanded"
    ),
)
inject_css()

# Yanındaki dosyalar eski sürümdeyse ÇÖKMEDEN önce net bir mesaj göster.
_surum_uyarisi_goster()

# Tablolar ve yönetici hesabı: uygulama ömrü boyunca yalnızca BİR KEZ hazırlanır
# (bkz. _veritabanini_hazirla üstündeki açıklama). st.set_page_config'ten sonra
# çağrılıyor, çünkü Streamlit onun ilk komut olmasını şart koşuyor.
_veritabanini_hazirla()

if "is_admin" not in st.session_state:
    st.session_state.is_admin = False
if "attempt" not in st.session_state:
    st.session_state.attempt = {}  # exam_id -> deneme sayacı (sıfırla için)
if "student_name" not in st.session_state:
    st.session_state.student_name = ""  # login sonrası: kullanıcı adı (sonuçların anahtarı)
if "student_display_name" not in st.session_state:
    st.session_state.student_display_name = ""  # login sonrası: ekranda gösterilecek ad


# =====================================================================
#  SAYFA YENİLENDİĞİNDE OTURUMUN KORUNMASI
# =====================================================================
# ÖNEMLİ - "SAYFAYI YENİLEDİĞİMDE PROGRAMDAN ÇIKMIŞ OLUYORUM":
# Streamlit'in oturum belleği (session_state) tarayıcı bağlantısına
# bağlıdır. F5'e basmak, sekmeyi kapatıp açmak, tablette uygulamayı arka
# plana atıp geri dönmek -- hepsi YENİ bir bağlantı açar ve o bellek
# boşalır. Kullanıcı hiçbir şey yapmadığı hâlde giriş ekranına düşüyordu.
#
# Çözüm: giriş yapıldığında adres çubuğuna imzalı bir jeton konuyor
# (?oturum=...). Sayfa yenilenince adres aynı kaldığı için jeton okunup
# oturum kendiliğinden geri kuruluyor. Jeton ŞİFRE İÇERMEZ; kullanıcı
# adı + rol + son kullanma tarihinden ibarettir ve sunucudaki gizli bir
# anahtarla imzalanır (bkz. db.oturum_jetonu_uret).

def _adres_jetonu_oku():
    try:
        return st.query_params.get("oturum") or ""
    except Exception:
        try:
            return (st.experimental_get_query_params().get("oturum") or [""])[0]
        except Exception:
            return ""


def _adres_jetonu_yaz(jeton):
    try:
        if jeton:
            st.query_params["oturum"] = jeton
        else:
            try:
                del st.query_params["oturum"]
            except (KeyError, AttributeError):
                pass
    except Exception:
        pass  # eski Streamlit sürümü: jeton yazılamaz, davranış eskisi gibi olur


def oturumu_baslat(rol, kayit):
    """Giriş başarılı olduğunda oturumu kurar ve adres çubuğuna jetonu yazar."""
    if rol == "yonetici":
        st.session_state.is_admin = True
        st.session_state.admin_username = kayit["username"]
    else:
        st.session_state.student_name = kayit["username"]
        st.session_state.student_display_name = kayit["display_name"]
    try:
        _adres_jetonu_yaz(db.oturum_jetonu_uret(kayit["username"], rol))
    except Exception:
        pass


def oturumu_kapat():
    """Çıkış: hem oturum belleğini hem adresteki jetonu temizler.
    (Jeton silinmezse sayfa yenilenince kullanıcı geri giriş yapmış olurdu.)"""
    st.session_state.is_admin = False
    st.session_state.student_name = ""
    st.session_state.student_display_name = ""
    _adres_jetonu_yaz("")


if not st.session_state.student_name and not st.session_state.is_admin:
    _jeton = _adres_jetonu_oku()
    if _jeton:
        try:
            _rol, _kayit = db.oturum_jetonu_coz(_jeton)
        except Exception:
            _rol, _kayit = None, None
        if _rol == "yonetici" and _kayit:
            st.session_state.is_admin = True
            st.session_state.admin_username = _kayit["username"]
        elif _rol == "ogrenci" and _kayit:
            st.session_state.student_name = _kayit["username"]
            st.session_state.student_display_name = _kayit["display_name"]
        else:
            # Jeton geçersiz ya da süresi dolmuş: adresi temizle ki her
            # yenilemede boşuna denenmesin.
            _adres_jetonu_yaz("")

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
    oturumu_kapat()   # adresteki jeton da silinir, yoksa yenilemede geri girilirdi
    st.session_state["_timeout_notice"] = True

st.session_state._last_activity = datetime.now()

def render_student_login_form():
    # Admin kullanıcı adı kutusu gibi, kayıtlı TEK bir öğrenci varsa (tipik
    # kullanım: tek çocuk) kullanıcı adını otomatik dolduruyoruz; birden
    # fazla öğrenci kayıtlıysa hangisi olduğunu tahmin edemeyeceğimiz için
    # boş bırakıyoruz.
    _students = db.get_students()
    _default_user = _students[0]["username"] if len(_students) == 1 else ""
    # Yeni hesap oluşturulduysa: başarı mesajını BİR KEZ göster ve kullanıcı
    # adını giriş kutusuna hazır getir. (Önceden mesaj ekranda takılı kalıyor,
    # kayıt formu da dolu duruyordu; kullanıcı "kayıt oldu mu olmadı mı"
    # anlayamıyordu.)
    _kayit_mesaji = st.session_state.pop("_kayit_ok", None)
    if _kayit_mesaji:
        st.success(_kayit_mesaji, icon="✅")
    # ÖNEMLİ: Streamlit, ekrana çizilmiş bir kutunun değerini sonradan
    # DEĞİŞTİRMEYE izin vermez ("cannot be modified after the widget ... is
    # instantiated" hatası). Kayıt olduktan sonra kullanıcı adını giriş
    # kutusuna yazmak istediğimizde tam bu hata oluşuyor ve hesap oluşsa bile
    # ekran donuyordu. Çözüm: kutunun ANAHTARINI değiştirmek -- anahtar
    # değişince Streamlit bunu yepyeni bir kutu sayar ve 'value' geçerli olur.
    _kutu_no = st.session_state.get("_login_kutu_no", 0)
    _on_deger = st.session_state.get("_kayit_kullanici") or _default_user
    login_user = st.text_input("Kullanıcı Adı", value=_on_deger, key=f"login_user_{_kutu_no}")
    login_pw = st.text_input("Şifre", type="password", key=f"login_pw_{_kutu_no}")
    if st.button("Giriş Yap", key="student_login_btn", type="primary", use_container_width=True):
        student = db.verify_student(login_user, login_pw)
        if student:
            oturumu_baslat("ogrenci", student)
            st.rerun()
        else:
            st.error("Kullanıcı adı veya şifre yanlış.")

    with st.expander("🆕 Hesabınız yok mu? Kayıt olun"):
        # Kayıt kutuları da aynı "anahtarı değiştir" yöntemiyle temizleniyor:
        # hesap oluşturulduktan sonra numara artıyor, kutular yepyeni ve boş
        # olarak geliyor. (Doldurulmuş form ekranda takılı kalmıyor.)
        reg_display = st.text_input("Adınız Soyadınız", key=f"reg_display_{_kutu_no}")
        reg_user = st.text_input("Kullanıcı Adı", key=f"reg_user_{_kutu_no}",
                                 help="Boşluksuz, Türkçe karaktersiz olması önerilir.")
        reg_pw = st.text_input("Şifre", type="password", key=f"reg_pw_{_kutu_no}")
        reg_pw2 = st.text_input("Şifre (tekrar)", type="password", key=f"reg_pw2_{_kutu_no}")
        if st.button("Hesap Oluştur", key="register_btn", use_container_width=True):
            if reg_pw != reg_pw2:
                st.error("Girdiğiniz şifreler birbiriyle eşleşmiyor.")
            elif len(reg_pw) < 4:
                st.error("Şifre en az 4 karakter olmalı.")
            else:
                ok, msg = db.create_student(reg_user, reg_display, reg_pw)
                if ok:
                    # Kayıt formunu temizle, kullanıcı adını giriş kutusuna
                    # taşı ve sayfayı yenile: böylece hem "oldu mu?" belirsizliği
                    # kalmıyor hem de doldurulmuş form ekranda takılı kalmıyor.
                    _yeni_kullanici = (reg_user or "").strip().lower().replace(" ", "_")
                    # Giriş ve kayıt kutularını yeni kutular olarak yeniden yarat
                    # (bkz. yukarıdaki "_kutu_no" açıklaması).
                    st.session_state["_kayit_kullanici"] = _yeni_kullanici
                    st.session_state["_login_kutu_no"] = (
                        st.session_state.get("_login_kutu_no", 0) + 1
                    )
                    st.session_state["_kayit_ok"] = (
                        f"Hesap oluşturuldu: **{_yeni_kullanici}**. "
                        "Kullanıcı adı yukarıya yazıldı; şifrenizi girip **Giriş Yap** deyin."
                    )
                    st.rerun()
                else:
                    st.error(msg)


def render_admin_login_form():
    admin_user = st.text_input("Kullanıcı Adı", key="admin_user", value=config.ADMIN_USERNAME)
    admin_pw = st.text_input("Şifre", type="password", key="admin_pw")
    if st.button("Giriş Yap", key="admin_login_btn", type="primary", use_container_width=True):
        admin = db.verify_admin(admin_user, admin_pw)
        if admin:
            oturumu_baslat("yonetici", admin)
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
        # ÖNEMLİ - KAFA KARIŞIKLIĞI: Yönetici olarak giriş yapan kişiye
        # "Hoş geldin EMİR MİRAÇ ONUR" yazıyordu; kullanıcı haklı olarak
        # "ben admin girdim, niye çocuğun adı yazıyor?" diyordu. Artık
        # ASIL KİMLİK ne ise o yazıyor; öğrenci adı, sonucun kimin adına
        # kaydedileceğini belirten ikinci satırda duruyor.
        if st.session_state.is_admin:
            st.success("⚙️ Yönetici olarak giriş yaptınız.")
            st.info(
                f"📝 Çözülen sınavlar **{st.session_state.student_display_name}** "
                f"adına kaydedilecek.",
                icon="👤",
            )
        else:
            st.success(f"Hoş geldin, {st.session_state.student_display_name}! 👋")
        if st.button(
            "Öğrenci seçimini bırak" if st.session_state.is_admin else "Çıkış Yap",
            key="student_logout_btn", use_container_width=True,
        ):
            if st.session_state.is_admin:
                # Yönetici "öğrenci olarak devam et" modundaydı: sadece o modu bırak.
                st.session_state.student_name = ""
                st.session_state.student_display_name = ""
                st.session_state["_ogrenci_secimi_birakildi"] = True
            else:
                oturumu_kapat()
            st.rerun()
        if st.session_state.is_admin and st.button(
            "Yönetici oturumunu da kapat", key="admin_logout_btn2", use_container_width=True
        ):
            oturumu_kapat()
            st.rerun()
    else:
        # Sadece yönetici girişi yapılmış.
        st.success("Yönetici olarak giriş yaptınız.")
        if st.button("Çıkış Yap", key="admin_logout_btn", use_container_width=True):
            oturumu_kapat()
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

# =====================================================================
#  YÖNETİCİ GİRİŞİ = ÖĞRENCİ GİRİŞİ DEĞİLDİR
# =====================================================================
# ÖNEMLİ - "GİRİŞ YAPTIM AMA OPTİK FORM HÂLÂ 'GİRİŞ YAPIN' DİYOR":
# Bir sınav sonucu her zaman BİR ÖĞRENCİNİN adına kaydedilir. Yönetici
# olarak giriş yapmak tek başına yetmiyordu; sonucun kime yazılacağı
# ayrıca seçilmemişse optik form "şu an kimse giriş yapmadı" uyarısı
# veriyor ve "Sınavı Bitir" düğmesi çalışmıyordu. Kullanıcı haklı olarak
# "ama ben giriş yaptım" diyordu.
#
# Evde tipik kullanım tek çocuk olduğu için: yönetici giriş yapmışsa ve
# sistemde TEK öğrenci kayıtlıysa o öğrenci kendiliğinden seçilir.
# Yönetici bunu bilinçli olarak bıraktıysa (kenar çubuğundan "Çıkış Yap")
# otomatik seçim bir daha devreye girmez.
if (st.session_state.is_admin and not st.session_state.student_name
        and not st.session_state.get("_ogrenci_secimi_birakildi")):
    _tek_ogrenci = db.get_students()
    if len(_tek_ogrenci) == 1:
        st.session_state.student_name = _tek_ogrenci[0]["username"]
        st.session_state.student_display_name = _tek_ogrenci[0]["display_name"]
        st.session_state["_otomatik_ogrenci"] = True

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
    _ist, _ist_hata = _istatistik_al()
    if _ist_hata:
        st.warning(_ist_hata)
        _ist = {"toplam_deneme": 0, "toplam_soru": 0, "toplam_sonuc": 0,
                "ogrenci_sayisi": 0, "kategoriler": {}}
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
        _tablo(_kat_df)

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

# =====================================================================
#  BÖLÜM SEÇİMİ  (eskiden st.tabs idi)
# =====================================================================
# ÖNEMLİ - YAVAŞLIĞIN GERİYE KALAN BÜYÜK SEBEBİ BUYDU:
# Streamlit'in sekmeleri (st.tabs) görsel bir hiledir: ekranda tek bir
# sekme görünse de, HER ekran yenilemesinde BÜTÜN sekmelerin içeriği
# baştan hesaplanır. Yani öğrenci optik formda tek bir şıkka dokunduğunda,
# "Gelişim Raporum" ve "Admin Paneli" de sessizce yeniden çiziliyor,
# veritabanına onlarca gereksiz sorgu gidiyordu.
# Artık bölümler arasında geçiş bir düğme seridi ile yapılıyor ve
# YALNIZCA seçili bölüm hesaplanıyor.
SEK_SINAV, SEK_RAPOR, SEK_ADMIN = "📱 Sınav Çöz", "📊 Gelişim Raporum", "⚙️ Admin Paneli"
bolumler = [SEK_SINAV, SEK_RAPOR]
if st.session_state.is_admin:
    bolumler.append(SEK_ADMIN)
if st.session_state.get("_aktif_bolum") not in bolumler:
    st.session_state["_aktif_bolum"] = SEK_SINAV

_segment = getattr(st, "segmented_control", None)
_secim = None
if _segment is not None:
    try:
        _secim = _segment(
            "Bölüm", bolumler, key="_aktif_bolum", label_visibility="collapsed",
        )
    except Exception:
        _segment = None
if _segment is None:
    _secim = st.radio(
        "Bölüm", bolumler, key="_aktif_bolum",
        horizontal=True, label_visibility="collapsed",
    )
aktif_bolum = _secim or st.session_state.get("_aktif_bolum") or SEK_SINAV
st.divider()


# ================================================================= TAB: SINAV ÇÖZ
if aktif_bolum == SEK_SINAV:
    st.markdown("### 📱 Sınav Çöz")

    # ---- Sayaçlar: sistemde ne var, öğrenci ne kadarını çözmüş ----
    _ist, _ist_hata = _istatistik_al()
    if _ist_hata:
        st.warning(_ist_hata)
        _ist = {"toplam_deneme": 0, "toplam_soru": 0, "toplam_sonuc": 0,
                "ogrenci_sayisi": 0, "kategoriler": {}}
    _benim_sonuclar = (
        db.get_results(student_name=st.session_state.student_name)
        if st.session_state.student_name else []
    )
    _benim = len(_benim_sonuclar)
    # Hangi denemeyi çözmüş? (aşağıdaki listede ✅ ile işaretlenecek)
    _cozulmus = {}
    for _r in _benim_sonuclar:
        _k = _cozulmus.setdefault(_r["exam_id"], {"adet": 0, "en_iyi": None})
        _k["adet"] += 1
        if _k["en_iyi"] is None or (_r["total_net"] or 0) > _k["en_iyi"]:
            _k["en_iyi"] = _r["total_net"] or 0
    _s1, _s2, _s3, _s4 = st.columns(4)
    _s1.metric("📚 Toplam Deneme/Test", _ist["toplam_deneme"])
    _s2.metric("❓ Toplam Soru", _ist["toplam_soru"])
    _s3.metric("✅ Senin Çözdüğün", _benim)
    _s4.metric("📈 Tüm Çözülenler", _ist["toplam_sonuc"])
    with st.expander("📂 Bölüm bölüm dağılım"):
        if _ist["kategoriler"]:
            _tablo(pd.DataFrame([
                {
                    "Bölüm": k,
                    "Deneme/Test": v["deneme"],
                    "Toplam Soru": v["soru"],
                    "Çözülme Sayısı": v["cozulen"],
                }
                for k, v in _ist["kategoriler"].items()
            ]))
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
    _cat_prev = st.session_state.get("_solve_cat_val")
    _cat_index = categories.index(_cat_prev) if _cat_prev in categories else 0
    selected_cat = st.selectbox("Bölüm", categories, index=_cat_index, key="solve_cat")
    st.session_state["_solve_cat_val"] = selected_cat
    # ÖNEMLİ - LİSTE SIRALAMASI: Denemeler veritabanından "en son eklenen
    # en üstte" sırasıyla geliyordu; bu yüzden liste "Test 39, Test 38..."
    # diye TERSTEN başlıyor, üstelik "Test 10" ile "Test 9" düz metin
    # karşılaştırıldığı için sıra karışıyordu. Artık başlıktaki sayılar
    # gerçek sayı olarak okunup 1'den itibaren küçükten büyüğe diziliyor.
    exams = sorted(db.get_exams(category=selected_cat), key=lambda e: _dogal_sira(e["title"]))

    # ---- ARAMA VE FİLTRELER ----
    # ÖNEMLİ - KULLANICI İSTEĞİ: Bursluluk bölümünde 40 kitapçık birikince
    # açılır listeden aradığını bulmak zorlaşıyordu. Artık yıl ve sınıf
    # süzgeçleri ve serbest arama kutusu var; liste anında daralıyor.
    def _yil_bul(baslik):
        m = re.search(r"(20\d{2})", baslik or "")
        return m.group(1) if m else None

    def _sinif_bul(baslik):
        m = re.search(r"([5-8])\s*\.?\s*[Ss][ıiİI][nN][ıiİI][fF]", baslik or "")
        return f"{m.group(1)}. Sınıf" if m else None

    _yillar = sorted({y for e in exams if (y := _yil_bul(e["title"]))}, reverse=True)
    _siniflar = sorted({sf for e in exams if (sf := _sinif_bul(e["title"]))})
    if _yillar or _siniflar or len(exams) > 12:
        with st.expander("🔎 Listeyi daralt (yıl / sınıf / arama)", expanded=bool(_yillar and _siniflar)):
            _f1, _f2, _f3 = st.columns([1, 1, 2])
            _sec_yil = _f1.selectbox("Yıl", ["Hepsi"] + _yillar, key=f"_flt_yil_{selected_cat}") \
                if _yillar else "Hepsi"
            _sec_sinif = _f2.selectbox("Sınıf", ["Hepsi"] + _siniflar, key=f"_flt_sinif_{selected_cat}") \
                if _siniflar else "Hepsi"
            _ara = _f3.text_input("Ada göre ara", key=f"_flt_ara_{selected_cat}",
                                  placeholder="örn. Matematik, 2. Ünite, Türkçe...")
        _once = len(exams)
        if _sec_yil != "Hepsi":
            exams = [e for e in exams if _yil_bul(e["title"]) == _sec_yil]
        if _sec_sinif != "Hepsi":
            exams = [e for e in exams if _sinif_bul(e["title"]) == _sec_sinif]
        if _ara and _ara.strip():
            _ar = _sadelestir(_ara)
            exams = [e for e in exams if _ar in _sadelestir(e["title"])]
        if len(exams) != _once:
            st.caption(f"🔎 Süzgeç açık: {_once} testten **{len(exams)} tanesi** listeleniyor.")

    if True:
        if exams:
            exam_titles = {e["id"]: e["title"] for e in exams}

            def _deneme_etiketi(x):
                """Liste yazısı: çözülmüş denemelerin başında ✅ ve en iyi net."""
                if x is None:
                    return "— Bir deneme seçin —"
                _bilgi = _cozulmus.get(x)
                if not _bilgi:
                    return f"⬜ {exam_titles[x]}"
                _kez = "" if _bilgi["adet"] == 1 else f" ×{_bilgi['adet']}"
                return f"✅ {exam_titles[x]}  ·  net {_bilgi['en_iyi']}{_kez}"

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
                format_func=_deneme_etiketi,
                key="solve_exam",
                help="✅ işaretli olanları daha önce çözdün.",
            )
            st.session_state["_solve_exam_val"] = selected_exam_id
            _cz = len([1 for x in options if x is not None and x in _cozulmus])
            st.caption(
                f"Bu bölümde **{len(exams)}** test var; **{_cz}** tanesini çözdün, "
                f"**{len(exams) - _cz}** tanesi duruyor."
            )
        else:
            selected_exam_id = None
            st.info("Bu kategoride henüz bir deneme yok. Admin panelinden ekleyin.")

    # ÖNEMLİ - "BASMADIĞIM HALDE SONUÇ PENCERESİ AÇILIYOR" HATASI:
    # Başka bir teste geçildiğinde, bir önceki testte açılmış olan sonuç
    # penceresinin işareti oturum belleğinde kalıyor ve yeni testte de
    # (ESKİ testin sonucuyla) açılıyordu. Test değişince artık siliniyor.
    if st.session_state.get("_son_secili_sinav") != selected_exam_id:
        st.session_state["_son_secili_sinav"] = selected_exam_id
        st.session_state.pop("_ozet_sonuc", None)

    # Büyük pencerede sonuç özeti istendiyse burada açılır.
    if st.session_state.get("_ozet_sonuc"):
        _oz = next(
            (x for x in _benim_sonuclar if x["id"] == st.session_state["_ozet_sonuc"]),
            None,
        )
        if _oz:
            _ozet_penceresi(_oz)
        else:
            st.session_state.pop("_ozet_sonuc", None)

    if selected_exam_id:
        exam = db.get_exam(selected_exam_id)
        structure = exam["structure"]
        answer_key = exam["answer_key"]
        student_name = st.session_state.student_name

        def _soru_numaralari(meta, k_list):
            """Bir dersin soru numaralari: soru bankasindan gelen testlerde
            kitaptaki gercek numaralar ('numbers'), diger her yerde 1..N."""
            return (meta or {}).get("numbers") or list(range(1, len(k_list) + 1))

        def _kisitli_yapi_ve_anahtar(secili):
            """'Sadece yanlislari coz' modu icin, verilen soru numaralarina
            gore kucultulmus bir yapi ve cevap anahtari uretir.

            secili: {bolum: {ders: [soru_no, ...]}}"""
            y, a = {}, {}
            for bolum, dersler in secili.items():
                for ders, numaralar in dersler.items():
                    if not numaralar:
                        continue
                    meta = structure.get(bolum, {}).get(ders, {})
                    k_list = answer_key.get(bolum, {}).get(ders, [])
                    tum = _soru_numaralari(meta, k_list)
                    eslesme = {n: k_list[i] for i, n in enumerate(tum) if i < len(k_list)}
                    kalanlar = [n for n in numaralar if n in eslesme]
                    if not kalanlar:
                        continue
                    y.setdefault(bolum, {})[ders] = {
                        "count": len(kalanlar),
                        "coef": meta.get("coef", 1),
                        "numbers": kalanlar,
                    }
                    a.setdefault(bolum, {})[ders] = [eslesme[n] for n in kalanlar]
            return y, a

        # Bir öğrenci daha önce (belki de başka bir oturumda / cihazda) bu
        # denemeyi çözmeye başlamış ya da bitirmişse, deneme numarasını
        # veritabanından öğreniyoruz -- böylece sayfa yeniden açıldığında
        # sıfırdan boş bir form yerine kaldığı yer / sonuç gösterilir.
        # ÖNEMLİ: Eskiden bu numara oturumda BİR KEZ belirleniyordu. Öğrenci
        # başka bir cihazdan (ya da başka bir sekmeden) yeni bir tur çözmüş
        # olsa bile, bu oturum hâlâ ESKİ turu gösteriyordu -- "testin her
        # seferinde ilk çözdüğüm bilgisi geliyor" şikâyetinin sebeplerinden
        # biri buydu. Artık veritabanındaki tur numarası daha büyükse ona
        # güncelleniyor; yeni başlanmış (henüz kaydedilmemiş) bir tur varsa
        # o korunuyor.
        _db_tur = db.get_current_attempt_no(selected_exam_id, student_name)
        _oturum_tur = st.session_state.attempt.get(selected_exam_id)
        if _oturum_tur is None or _oturum_tur < _db_tur:
            st.session_state.attempt[selected_exam_id] = _db_tur
        attempt_no = st.session_state.attempt[selected_exam_id]

        def _yeni_tur_no():
            """Bir sonraki BOŞ tur numarası.

            ÖNEMLİ - "YENİDEN ÇÖZ DÜĞMESİ ÇALIŞMIYOR" HATASI: Yeni tur
            numarası eskiden "bakılan tur + 1" diye üretiliyordu. Öğrenci
            tur 0 ve tur 1'i çözdükten sonra tur seçiciyle tur 0'a dönüp
            "Yeniden Çöz" derse hedef 1 oluyor -- ama tur 1'in ZATEN bir
            sonucu var. Ekranda boş optik form yerine eski sonuç çıkıyor,
            düğme hiçbir şey yapmamış gibi görünüyordu. Artık her zaman
            EN YÜKSEK tur numarasının bir fazlası kullanılıyor."""
            _enb = max(
                [attempt_no, db.get_current_attempt_no(selected_exam_id, student_name)]
                + [(x.get("attempt_no") or 0) for x in _benim_sonuclar
                   if x["exam_id"] == selected_exam_id]
            )
            return _enb + 1

        # ---- Tur seçici: bu denemeyi birden çok kez çözdüyse hangisi? ----
        # Öğrenci geriye dönüp önceki turlarına da bakabilsin, istediği
        # turdan "ikinci şans"a geçebilsin diye.
        _bu_sinav_sonuclari = sorted(
            [x for x in _benim_sonuclar if x["exam_id"] == selected_exam_id],
            key=lambda x: (x.get("attempt_no") or 0),
        )
        if len(_bu_sinav_sonuclari) > 1:
            _tur_secenekleri = [x.get("attempt_no") or 0 for x in _bu_sinav_sonuclari]
            if attempt_no not in _tur_secenekleri:
                _tur_secenekleri.append(attempt_no)
                _tur_secenekleri.sort()
            _etiket = {}
            for _i, _no in enumerate(_tur_secenekleri):
                _kayit = next(
                    (x for x in _bu_sinav_sonuclari if (x.get("attempt_no") or 0) == _no), None
                )
                if _kayit is None:
                    _etiket[_no] = f"{_i + 1}. tur (devam ediyor)"
                elif _kayit.get("mode") == "yanlis":
                    _etiket[_no] = f"{_i + 1}. tur · 🎯 düzeltme turu · net {_kayit['total_net']}"
                else:
                    _etiket[_no] = f"{_i + 1}. tur · 📝 tam sınav · net {_kayit['total_net']}"
            _yeni_tur = st.selectbox(
                "🔁 Bu denemeyi birden çok kez çözdün — hangi turu görmek istersin?",
                _tur_secenekleri,
                index=_tur_secenekleri.index(attempt_no),
                format_func=lambda n: _etiket.get(n, str(n)),
                # ÖNEMLİ - "YENİDEN ÇÖZ HİÇBİR ŞEY YAPMIYOR" HATASININ İKİNCİ
                # YARISI: Streamlit, bir kutunun ANAHTARI aynı kaldığı sürece
                # o kutunun ESKİ değerini hatırlar ve yeni 'index=' değerini
                # YOK SAYAR. "Yeniden Çöz" yeni bir tur açıyordu, ama bu kutu
                # hâlâ eski turu döndürüyor ve hemen aşağıdaki satır turu geri
                # alıyordu -- yani düğme çalışıyor, sonra kendi kendini iptal
                # ediyordu. Anahtara tur numarası eklenince kutu her tur
                # değişiminde yeniden yaratılıyor ve doğru turu gösteriyor.
                key=f"_tur_sec_{selected_exam_id}_{attempt_no}",
            )
            if _yeni_tur != attempt_no:
                st.session_state.attempt[selected_exam_id] = _yeni_tur
                attempt_no = _yeni_tur

        # "İkinci şans" modu: bu deneme numarasında SADECE önceki yanlış/boş
        # sorular soruluyorsa, hangi sorular olduğu veritabanında saklanır.
        # (Oturum belleğinde tutulsaydı sayfa kapanınca kaybolur ve öğrenci
        #  yarım kalan ikinci şansına geri dönemezdi.)
        _wrong_key = f"wrongmode:{selected_exam_id}:{student_name}:{attempt_no}"
        _wrong_raw = db.get_setting(_wrong_key) if student_name else None
        _wrong_sel = json.loads(_wrong_raw) if _wrong_raw else None
        if _wrong_sel:
            aktif_yapi, aktif_anahtar = _kisitli_yapi_ve_anahtar(_wrong_sel)
        else:
            aktif_yapi, aktif_anahtar = structure, answer_key

        existing_result = db.get_result_for_attempt(selected_exam_id, student_name, attempt_no)

        # ---- Görünüm seçimi: yan yana mı, alt alta mı? ----
        # Tablet yatayken yan yana rahat okunur; dikeyken veya telefonda alt
        # alta daha iyi. Cihaza göre otomatik ayarlanıyor ama öğrenci
        # istediğinde elle de değiştirebilsin diye buraya bir düğme konuldu.
        # ÖNEMLİ - TELEFONDA ÇALIŞMIYORDU: Bu seçim önce "radio"
        # (yuvarlak seçenek) idi. Bilgisayarda fareyle sorunsuz
        # çalışıyordu ama telefonda parmakla basıldığında hiçbir şey
        # olmuyordu; kutucuğun kendisi çok küçük, dokunma ise etiketin
        # boşluğuna düşüyordu. CSS ile büyütmek de yetmedi. Artık
        # düğme kullanılıyor: düğmenin TAMAMI basılabilir alandır,
        # dokunmatik ekranlarda en güvenilir yol budur.
        if "_gorunum_alt" not in st.session_state:
            st.session_state["_gorunum_alt"] = False
        with st.container(key="lgs_gorunum"):
            _g1, _g2, _g3 = st.columns([1, 1, 3])
            if _g1.button(
                    ("✅ " if not st.session_state["_gorunum_alt"] else "") + "🖥️ Yan yana",
                    key="_gorunum_yan", use_container_width=True,
                    type="primary" if not st.session_state["_gorunum_alt"] else "secondary"):
                st.session_state["_gorunum_alt"] = False
                _yenile(sadece_bolum=False)
            if _g2.button(
                    ("✅ " if st.session_state["_gorunum_alt"] else "") + "📱 Alt alta",
                    key="_gorunum_altbtn", use_container_width=True,
                    type="primary" if st.session_state["_gorunum_alt"] else "secondary"):
                st.session_state["_gorunum_alt"] = True
                _yenile(sadece_bolum=False)
            _g3.caption("Tablet yatayken **Yan yana**, telefonda **Alt alta** daha rahat okunur.")
        if st.session_state["_gorunum_alt"]:
            col_pdf = st.container()
            col_form = st.container()
        else:
            col_pdf, col_form = st.columns([6, 4])

        with col_pdf:
            _pdf_yolu, _pdf_hata = _pdf_yolu_onbellekli(
                exam["id"], exam.get("pdf_path"), exam.get("source_url")
            )
            if _pdf_yolu:
                _pdf_bolumu(_pdf_yolu, exam["title"])
            else:
                st.subheader(exam["title"])
                st.error(
                    "📄 **Bu denemenin kitapçığı sunucuda bulunamadı.**\n\n"
                    "Sebebi: Streamlit'in ücretsiz sunucusu, uygulama her "
                    "güncellendiğinde diski sıfırlıyor. Bu deneme, PDF'i "
                    "veritabanına kaydedilmeden önce eklenmiş.\n\n"
                    "**Çözüm:** Yönetici olarak **Admin Paneli → Kayıtlı Denemeler** "
                    "bölümüne girip bu denemeyi silin, sonra yeniden ekleyin. "
                    "Yeni eklenen denemelerin PDF'i artık veritabanında da "
                    "saklanıyor, bir daha kaybolmayacak."
                    + ("" if _pdf_hata in (None, "yok") else f"\n\n_Ayrıntı: {_pdf_hata}_")
                )

        with col_form:
            if existing_result:
                # Sınav yeni bitirildiyse kutlama: ekrana uçan balonlar.
                if st.session_state.pop("_sinav_balon", False):
                    st.balloons()
                # ---- Bu deneme numarası için sınav zaten bitirilmiş: sonucu göster ----
                if existing_result.get("mode") == "yanlis":
                    st.success("🎯 **Yanlışları Düzeltme Turu**'nu tamamladınız. Sonuçlarınız:")
                else:
                    st.success("✅ Bu denemeyi zaten çözdünüz. Sonuçlarınız:")
                st.caption(f"🗓️ Çözüm tarihi: **{_tarih_bicimle(existing_result.get('created_at'))}**")

                # TABLET: Sonuç tablosu dar ekranda optik formun altında
                # kırpılıp tam görünmüyordu. Bu düğme sonucu ekranın ortasında,
                # tam genişlikte bir pencerede açar.
                if st.button("🔍 Sonucu Büyük Pencerede Aç", key=f"bigres_{existing_result['id']}",
                             use_container_width=True):
                    st.session_state["_ozet_sonuc"] = existing_result["id"]
                    st.rerun()
                st.caption("📊 Ayrıntılı döküm için **Gelişim Raporum** sekmesine geçebilirsiniz.")

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

                # ---- DÜZELTME TURU: yanlış yapılan soruları tekrar çöz ----
                # ÖNEMLİ - KULLANICI HAKLIYDI: Burada eskiden yanlışlar ile
                # BOŞ bırakılanlar aynı torbaya konuyordu. Çocuk 90 soruluk
                # denemenin sadece 5'ini cevaplayıp bıraktığında, düzeltme
                # turu 89 soru soruyordu -- yani aslında hiç çözülmemiş
                # sınavı baştan çözdürüyordu. Artık VARSAYILAN sadece
                # yanlışlar; boşları da isteyen ayrıca seçebiliyor.
                _detay = existing_result.get("answers_detail")
                _sadece_yanlis, _yanlis_ve_bos = {}, {}
                if _detay:
                    for _b, _dersler in _detay.items():
                        for _d, _satirlar in _dersler.items():
                            _y = [x["soru"] for x in _satirlar if x.get("durum") == "yanlis"]
                            _yb = [x["soru"] for x in _satirlar if x.get("durum") != "dogru"]
                            if _y:
                                _sadece_yanlis.setdefault(_b, {})[_d] = _y
                            if _yb:
                                _yanlis_ve_bos.setdefault(_b, {})[_d] = _yb
                _n_yanlis = sum(len(v) for d in _sadece_yanlis.values() for v in d.values())
                _n_hepsi = sum(len(v) for d in _yanlis_ve_bos.values() for v in d.values())
                _n_bos = _n_hepsi - _n_yanlis

                if _n_hepsi and student_name:
                    if _n_yanlis and _n_bos:
                        _mod = st.radio(
                            "Düzeltme turunda hangi sorular sorulsun?",
                            [
                                f"❌ Sadece yanlış yaptıklarım ({_n_yanlis} soru)",
                                f"❌+⬜ Yanlışlar ve boş bıraktıklarım ({_n_hepsi} soru)",
                            ],
                            key=f"_duzeltme_mod_{selected_exam_id}_{attempt_no}",
                        )
                        _secili_liste = _sadece_yanlis if _mod.startswith("❌ Sadece") else _yanlis_ve_bos
                    elif _n_yanlis:
                        _secili_liste = _sadece_yanlis
                    else:
                        _secili_liste = _yanlis_ve_bos
                    _yanlislar = _secili_liste
                    _yanlis_adet = sum(len(v) for d in _yanlislar.values() for v in d.values())
                else:
                    _yanlislar, _yanlis_adet = {}, 0

                if _yanlis_adet and student_name:
                    st.markdown(
                        f"**🎯 Yanlışları Düzeltme Turu:** Bu testte **{_n_yanlis} soruyu yanlış** "
                        f"yaptın"
                        + (f", **{_n_bos} soruyu** da boş bıraktın." if _n_bos else ".")
                    )
                    st.caption(
                        "Aşağıdaki düğme sadece o soruları yeniden sorar; doğru yaptıkların "
                        "tekrar karşına çıkmaz. Önceki sonucun silinmez, ayrıca saklanır ve "
                        "raporda ikisi karşılaştırılır."
                    )
                    if st.button(
                        f"🎯 Yanlışlarımı Düzelt ({_yanlis_adet} soru)",
                        key=f"wrongretry_{selected_exam_id}_{attempt_no}",
                        use_container_width=True,
                        type="primary",
                    ):
                        _yeni = _yeni_tur_no()
                        db.set_setting(
                            f"wrongmode:{selected_exam_id}:{student_name}:{_yeni}",
                            json.dumps(_yanlislar, ensure_ascii=False),
                        )
                        st.session_state.attempt[selected_exam_id] = _yeni
                        st.rerun()
                elif _n_hepsi and not student_name:
                    st.info("Yanlışları düzeltme turu için giriş yapmanız gerekiyor.")
                elif _detay:
                    st.success("🎉 Bu denemede hiç yanlışın yok, düzeltme turuna gerek kalmadı!")

                if st.button(
                    "🔄 Yeniden Çöz (sınavın tamamını baştan)",
                    key=f"retry_{selected_exam_id}_{attempt_no}",
                    use_container_width=True,
                ):
                    st.session_state.attempt[selected_exam_id] = _yeni_tur_no()
                    st.rerun()
            else:
                _optik_form(
                    selected_exam_id, attempt_no, student_name,
                    aktif_yapi, aktif_anahtar, bool(_wrong_sel),
                    tam_anahtar=answer_key,
                )


# ================================================================= TAB: GELİŞİM RAPORU
if aktif_bolum == SEK_RAPOR:
    st.subheader("📊 Gelişim Raporum")
    student = st.session_state.student_name
    if not student:
        st.info("Sonuçlarınızı görmek için soldaki menüden giriş yapın.")
    else:
        results = db.get_results(student_name=student)
        if not results:
            st.info("Henüz çözülmüş bir sınav yok.")
        else:
            # En son çözülen en üstte dursun.
            results = sorted(results, key=lambda x: str(x.get("created_at") or ""), reverse=True)

            # ---- Üst sayaçlar ----
            _netler = [r["total_net"] for r in results if r["total_net"] is not None]
            _k1, _k2, _k3, _k4 = st.columns(4)
            _k1.metric("📝 Toplam Çözüm", len(results))
            _k2.metric("📚 Farklı Sınav", len({r["exam_id"] for r in results}))
            _k3.metric("🏅 En Yüksek Net", max(_netler) if _netler else 0)
            _k4.metric("📈 Ortalama Net", round(sum(_netler) / len(_netler), 2) if _netler else 0)

            st.divider()
            # ÖNEMLİ - ESKİ TASARIMIN SORUNU: Her sınav için ekrana kocaman bir
            # kutu çiziliyordu; 30-40 sınav çözüldüğünde sayfa metrelerce
            # aşağıya uzuyor, aranan sınav bulunamıyordu. Artık her sınav TEK
            # SATIR; ayrıntılar düğmeyle açılan pencerede gösteriliyor.
            # ÖNEMLİ - KULLANICI İSTEĞİ: "Aynı tarihte çözülen aynı test tek
            # satırda gösterilsin, içine girince o ekran açılsın." Bir sınavı
            # çözüp ardından düzeltme turunu da yapınca liste ikiye çıkıyor,
            # üstelik düzeltme turunun neti (birkaç soruluk) asıl sonucun
            # yanında yanıltıcı duruyordu. Artık satırlar GÜN + SINAV bazında
            # toplanıyor; turların dökümü satıra girilince açılıyor.
            _gruplar = _gunluk_gruplar(results)
            st.markdown("#### 📋 Çözdüğün Sınavlar")
            st.caption(
                "Her satır **bir sınavın bir günü**; o gün birden çok tur "
                "yaptıysan hepsi tek satırda toplanır. Karşılaştırmak "
                "istediklerini soldaki **kutucuktan işaretle**, sonra "
                "**📊 Seçilenleri Değerlendir**'e bas. Soru soru döküm için "
                "satırın sağındaki **🔍 Sınav Detayı** düğmesini kullan."
            )

            with _kutu("lgs_satir"):
                _b1, _b2, _b3 = st.columns([2.2, 1, 1])
                _degerlendir_basildi = _b1.button(
                    "📊 Seçilenleri Değerlendir", type="primary", use_container_width=True,
                    key="_dege_btn",
                )
                if _b2.button("Tümünü Seç", use_container_width=True, key="_hepsi_btn"):
                    for _g in _gruplar:
                        st.session_state[f"_secg_{_g['id']}"] = True
                    st.rerun()
                if _b3.button("Temizle", use_container_width=True, key="_temizle_btn"):
                    for _g in _gruplar:
                        st.session_state[f"_secg_{_g['id']}"] = False
                    st.rerun()

            # ---- Sınav listesi: kaydırılabilir, sabit yükseklikli kutu ----
            # "key" verilmesinin sebebi: bu kutuya CSS ile ayrı davranabilmek
            # (dar ekranda satırların alt alta dağılmasını engellemek).
            _secilenler = []
            with _kutu("lgs_kompakt", height=430, border=True):
                for _g in _gruplar:
                    _n = len(_g["turlar"])
                    if _n == 1:
                        _tur = ("🎯 Düzeltme turu" if _g["turlar"][0].get("mode") == "yanlis"
                                else "📝 Tam sınav")
                    else:
                        _tur = f"🔁 {_n} tur" + (
                            f" ({_g['duzeltme']} düzeltme)" if _g["duzeltme"] else "")
                    _t = _g["toplam"]
                    _c0, _c1, _c2, _c3 = st.columns([0.7, 5, 1.5, 2.4])
                    if _c0.checkbox(
                        "seç", key=f"_secg_{_g['id']}", label_visibility="collapsed",
                    ):
                        _secilenler.extend(_g["turlar"])
                    _c1.markdown(
                        f"**{_g['exam_title']}**  \n"
                        f"<span style='background:#1E3A8A;color:#fff;padding:2px 9px;"
                        f"border-radius:14px;font-size:0.82rem;font-weight:600;'>"
                        f"🗓️ {_g['gun']}</span> "
                        f"<span style='color:#475569;font-size:0.85rem;'> · {_tur} · "
                        f"✅{_t['dogru']} ❌{_t['yanlis']} ⬜{_t['bos']}</span>",
                        unsafe_allow_html=True,
                    )
                    _c2.markdown(
                        f"<div style='text-align:center;line-height:1.15;'>"
                        f"<span style='font-size:0.75rem;color:#64748B;'>NET</span><br>"
                        f"<span style='font-size:1.25rem;font-weight:700;color:#1E3A8A;'>"
                        f"{_g['net']}</span></div>",
                        unsafe_allow_html=True,
                    )
                    if _c3.button("🔍 Sınav Detayı", key=f"_detbtn_{_g['id']}",
                                  use_container_width=True):
                        st.session_state["_detay_grup"] = _g["id"]
                        st.session_state["_detay_ders"] = None
                        st.session_state["_detay_acik"] = True
                        st.session_state.pop("_dege_acik", None)
                        st.rerun()
                    st.markdown(
                        "<hr style='margin:4px 0 8px 0;border:none;"
                        "border-top:1px solid #E2E8F0;'>",
                        unsafe_allow_html=True,
                    )

            if _degerlendir_basildi:
                if _secilenler:
                    st.session_state["_dege_ids"] = [x["id"] for x in _secilenler]
                    st.session_state["_dege_acik"] = True
                    st.session_state.pop("_detay_acik", None)
                    st.rerun()
                else:
                    st.warning(
                        "Önce listeden en az bir sınavı işaretleyin "
                        "(sınav adının solundaki kutucuk)."
                    )

            # ---- Gelişim grafiği (yer kaplamasın diye kapalı gelir) ----
            _tam = [r for r in results if r.get("mode") != "yanlis"]
            if len(_tam) >= 2:
                with st.expander("📈 Gelişim grafiği (net değişimi)"):
                    _cd = pd.DataFrame(
                        [{"Tarih": _tarih_bicimle(r["created_at"]), "Toplam Net": r["total_net"]}
                         for r in _tam]
                    ).set_index("Tarih").sort_index()
                    st.line_chart(_cd)

            # ---- Pencereler: bir turda sadece BİRİ açılır (bkz. _pencere_ac) ----
            if st.session_state.get("_dege_acik"):
                _sec_kayitlar = [
                    x for x in results if x["id"] in (st.session_state.get("_dege_ids") or [])
                ]
                if _sec_kayitlar:
                    _degerlendirme_penceresi(_sec_kayitlar)
                else:
                    st.session_state.pop("_dege_acik", None)

            if st.session_state.get("_detay_acik"):
                _hedef = next(
                    (g for g in _gruplar if g["id"] == st.session_state.get("_detay_grup")),
                    None,
                )
                if _hedef is not None:
                    _grup_penceresi(
                        _hedef, pin_gerekli=True,
                        vurgu_ders=st.session_state.get("_detay_ders"),
                        temizlenecek=("_detay_acik", "_detay_grup", "_detay_ders"),
                    )
                else:
                    st.session_state.pop("_detay_acik", None)

# ================================================================= TAB: ADMIN
if st.session_state.is_admin and aktif_bolum == SEK_ADMIN:
    with st.container():
        st.subheader("⚙️ Admin Paneli")

        # ---- Hangi dosya hangi sürümde? ----
        # ÖNEMLİ: Kullanıcı GitHub'a dosyaları yükledikten sonra "yüklendi mi,
        # eskisi mi duruyor" diye emin olamıyordu (GitHub'daki tarihler
        # yanıltıcı: içeriği değişmeyen dosya için yeni kayıt oluşmuyor).
        # Burası, uygulamanın O AN çalıştırdığı dosyaların sürümünü gösterir --
        # tek doğru kaynak budur.
        _sur = _dosya_surumleri()
        _hepsi_guncel = all(x["Durum"].startswith("✅") for x in _sur)
        with st.expander(
            ("✅ Dosya sürümleri — hepsi güncel" if _hepsi_guncel
             else "⚠️ Dosya sürümleri — bazıları eski!"),
            expanded=not _hepsi_guncel,
        ):
            _tablo(pd.DataFrame(_sur))
            # ÖNEMLİ - "DOSYALARI DEĞİŞTİRDİM AMA HİÇBİR ŞEY DEĞİŞMEDİ":
            # Uygulama çalışırken yardımcı dosyalar (db.py, soru_bankasi.py...)
            # bir kez belleğe alınır ve orada kalır. Dosyanın diskteki hâlini
            # değiştirseniz bile çalışan program ESKİ kopyayı kullanmaya devam
            # eder. Bu düğme onları bellekten atıp diskten yeniden okur --
            # yani programı kapatıp açmadan güncellemeyi devreye alır.
            _rc1, _rc2 = st.columns([2, 3])
            if _rc1.button("🔄 Kodu diskten yeniden yükle", type="primary",
                           use_container_width=True, key="_kod_yenile"):
                import importlib
                _hata = []
                for _mod in (config, db, parsing, scoring, soru_bankasi, bot, drive_sync):
                    try:
                        importlib.reload(_mod)
                    except Exception as _e:
                        _hata.append(f"{getattr(_mod, '__name__', '?')}: {_e}")
                try:
                    st.cache_data.clear()
                    st.cache_resource.clear()
                except Exception:
                    pass
                # Taranmış kitap sonucu da eski koddan kalmış olabilir.
                for _k in ("_qb_testler", "_qb_uyarilar", "_qb_tarama_bilgi"):
                    st.session_state.pop(_k, None)
                st.session_state["_admin_flash"] = (
                    ("error", "Bazı dosyalar yeniden yüklenemedi: " + "; ".join(_hata))
                    if _hata else
                    ("success", "🔄 Bütün dosyalar diskten yeniden okundu. "
                                "Yukarıdaki sürüm tablosunda beklediğiniz sürümler "
                                "yazıyorsa güncelleme devrede demektir.")
                )
                st.rerun()
            _rc2.caption(
                "Dosyaları değiştirdiniz ama ekranda bir şey değişmediyse önce "
                "bu düğmeye basın. Sürüm tablosu hâlâ eski sürümü gösteriyorsa "
                "dosya gerçekten güncellenmemiştir."
            )
            st.caption(
                "Bu tablo, uygulamanın **şu anda çalıştırdığı** dosyaları gösterir. "
                "GitHub'daki tarihlere bakmayın: içeriği değişmeyen bir dosya için "
                "GitHub yeni kayıt oluşturmaz, tarihi eski kalır — bu normaldir. "
                "Burada hepsi ✅ ise her şey yerindedir."
            )

        # ---- Verilerin kalıcı olup olmadığını açıkça göster ----
        # Streamlit'in ücretsiz bulut sunucusunda uygulama klasörüne yazılan
        # dosyalar her yeniden başlatmada silinir. Bu satır, kalıcı veritabanı
        # bağlantısının gerçekten devrede olup olmadığını tek bakışta gösterir.
        try:
            _kalici = db.is_kalici()
        except AttributeError:
            _kalici = None
        if _kalici is True:
            st.success(
                "🔒 **Veriler kalıcı veritabanında saklanıyor.** Öğrenci hesapları ve "
                "sınav sonuçları, uygulama güncellense de silinmez.",
                icon="✅",
            )
        elif _kalici is False:
            st.warning(
                "⚠️ **Veriler bu bilgisayarda/sunucuda yerel dosyada tutuluyor.** "
                "Kendi bilgisayarınızda çalışıyorsanız bu normaldir, veriler kalıcıdır. "
                "Ama **Streamlit bulutunda** çalışıyorsanız, uygulama her güncellendiğinde "
                "veya bir süre kullanılmayıp uykuya daldıktan sonra **öğrenci hesapları ve "
                "sonuçlar silinir.** Kalıcı hale getirmek için Streamlit Cloud → Settings → "
                "Secrets bölümüne `DB_URL` satırını ekleyin."
            )

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
        # Uzun süren işlemler (kitap tarama, indirme, PIN kaydetme) bittiğinde
        # ekrana uçan balonlar: "işlem gerçekten bitti" hissi veriyor.
        if st.session_state.pop("_pin_balon", False) or st.session_state.pop("_islem_balon", False):
            st.balloons()

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
                "📝 Cevap Anahtarı Bekleyenler",
                "🩹 Eksik Kitapçıkları Onar",
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
                "Bir **soru bankası** ya da **çalışma kitabı** PDF'i yükleyin. Sistem kitabın "
                "biçimini kendi anlar ve içindeki testleri tek tek bulup kitabın sonundaki "
                "cevap anahtarıyla eşleştirir:\n\n"
                "- **Konu testli soru bankaları** (sayfa üstünde *TEST 3* yazan kitaplar) → "
                "her test ayrı bir deneme olur.\n"
                "- **MEB LGS Çalışma Kitapları** ve benzeri **ünite/tema** düzenli kitaplar → "
                "her ünite ayrı bir deneme olur. Kitabın sonundaki **geçmiş yıl merkezî sınav "
                "soruları bölümü ALINMAZ** (onlar zaten *Otomatik İndirme* bölümünden ekleniyor)."
            )
            qb_file = st.file_uploader("Soru bankası / çalışma kitabı PDF'i", type=["pdf"], key="qb_pdf")
            qb_path_state = "_qb_path"

            _parca_secenek = {
                "30 soruluk testlere böl (önerilen)": 30,
                "40 soruluk testlere böl": 40,
                "25 soruluk testlere böl": 25,
                "15 soruluk testlere böl": 15,
                "Bölme — ünitenin tamamı tek test olsun": 0,
            }
            _parca_ad = st.selectbox(
                "Ünite/tema düzenli kitaplarda testleri nasıl bölelim?",
                list(_parca_secenek.keys()),
                index=0,
                key="qb_parca",
                help=(
                    "MEB çalışma kitaplarında bir ünitede 150 soru olabiliyor; tek oturumda "
                    "çözmek zor. Bölerseniz her parça ayrı bir test olarak eklenir, soru "
                    "numaraları kitaptakiyle aynı kalır. Sonda küçük bir artık kalırsa "
                    "(örneğin 7 soru) ayrı test yapılmaz, bir önceki teste eklenir."
                ),
            )
            st.caption(
                "Bu ayar sadece **ünite/tema** düzenli kitapları etkiler; konu testli soru "
                "bankalarında testler zaten kısa olduğu için bölünmez."
            )
            # ÖNEMLİ - KULLANICI ÖNERİSİ (ve doğru olan): Program eskiden
            # kitabın "geçmiş yıl merkezî sınav soruları" bölümlerini KENDİ
            # KARARIYLA atıyordu. Bu atlama çok dersli kitaplarda defalarca
            # yanlış yerde devreye girip kitabın yarısının hiç okunmamasına
            # yol açtı. Artık VARSAYILAN "her şeyi tara": ne çıkarsa çıksın
            # listede görürsünüz, istemediğinizi eklemeden önce listeden
            # çıkarırsınız. Karar programda değil sizde.
            _merkezi_atla = st.checkbox(
                "Kitabın sonundaki **geçmiş yıl merkezî sınav sorularını** tarama dışı bırak",
                value=False, key="qb_merkezi",
                help=("Kapalı bırakın (önerilen): kitapta ne varsa bulunur, "
                      "istemediğiniz testleri aşağıdaki listeden tek tıkla "
                      "çıkarabilirsiniz. Açarsanız program o bölümleri kendi "
                      "kararıyla atlar -- bazı kitaplarda yanlış yerde devreye "
                      "girip ders kaybına yol açabiliyor."),
            )

            # ÖNEMLİ - KULLANICI GERİ BİLDİRİMİ: Yeni bir kitap yüklendiğinde
            # ekranda ÖNCEKİ kitabın testleri duruyordu; hangi kitaba
            # baktığınız belli olmuyordu. Artık dosya değişince eski sonuç
            # otomatik siliniyor.
            _simdiki_dosya = f"{qb_file.name}|{qb_file.size}" if qb_file is not None else None
            if st.session_state.get("_qb_dosya") != _simdiki_dosya:
                st.session_state["_qb_dosya"] = _simdiki_dosya
                for _k in [k for k in list(st.session_state)
                           if k in ("_qb_testler", "_qb_uyarilar", "_qb_path")
                           or str(k).startswith("qb_secim")]:
                    st.session_state.pop(_k, None)
            if qb_file is None:
                st.info("⬆️ Önce yukarıdan bir PDF seçin; ardından tarama düğmesi çıkacak.")
            else:
                st.markdown(
                    f"✅ **{qb_file.name}** yüklendi ({qb_file.size / 1e6:.0f} MB). "
                    f"Şimdi aşağıdaki düğmeye basın 👇"
                )
                # ÖNEMLİ - "BEKLENMEYEN UYGULAMA HATASI / removeChild" HATASI:
                # Bu, koddaki bir mantık hatası değil; tarayıcının, sunucuyla
                # bağlantısı ortasında kopunca verdiği hatadır. Streamlit'in
                # ücretsiz bulut sunucusunda uygulamanın kullanabileceği
                # BELLEK sınırlıdır (yaklaşık 1 GB). 150-200 MB'lık bir
                # çalışma kitabı yüklendiğinde dosyanın tamamı belleğe
                # alınıyor, üstüne sayfa sayfa tarama geliyor ve sınır
                # aşılıyor: sunucu kendini yeniden başlatıyor, tarayıcı da
                # yarım kalan ekranı toparlayamayıp bu hatayı gösteriyor.
                if qb_file.size > 60 * 1024 * 1024 and db.is_kalici():
                    st.warning(
                        f"⚠️ **Bu kitap bulutta taranamayabilir** "
                        f"({qb_file.size / 1e6:.0f} MB).\n\n"
                        f"Streamlit'in ücretsiz sunucusunda uygulamanın kullanabileceği "
                        f"bellek sınırlıdır. Bu boyutta bir kitap yüklendiğinde sunucu "
                        f"belleği tükenip kendini yeniden başlatıyor; ekranda "
                        f"*\"Beklenmeyen Uygulama Hatası\"* çıkmasının sebebi budur — "
                        f"kitapta ya da programda bir bozukluk yok.\n\n"
                        f"**Yapılacak:** Bu kitabı **kendi bilgisayarınızda** "
                        f"(`BASLAT.bat`) tarayın. Bilgisayarınızın `.streamlit\\\\secrets.toml` "
                        f"dosyasına buluttaki `DB_URL` satırını da eklerseniz, yerelde "
                        f"eklediğiniz testler **aynı veritabanına** yazılır ve kitapçıkları "
                        f"veritabanında saklandığı için bulutta da açılır. Yani işi "
                        f"bilgisayarda yaparsınız, çocuk tabletten çözer."
                    )
            if qb_file is not None and st.button(
                    "📖 KİTABI TARA VE TESTLERİ BUL", type="primary",
                    use_container_width=True, key="qb_tara_btn"):
                _qb_path = os.path.join(PRIVATE_DIR, "_soru_bankasi.pdf")
                # Parça parça yazılıyor: getbuffer() dosyanın ikinci bir
                # kopyasını daha bellekte tutuyordu; büyük kitaplarda bu tek
                # başına sunucuyu devirmeye yetiyor.
                try:
                    qb_file.seek(0)
                except Exception:
                    pass
                with open(_qb_path, "wb") as f:
                    shutil.copyfileobj(qb_file, f, 4 * 1024 * 1024)
                # ÖNEMLİ - "PROGRAM DONDU MU, ÇALIŞIYOR MU?": Büyük kitaplarda
                # tarama dakikalarca sürüyor ve ekranda dönen bir çemberden
                # başka hiçbir şey görünmüyordu. Artık kaçıncı sayfada
                # olduğu yazıyor; hem beklerken içiniz rahat oluyor hem de
                # bir sorun çıkarsa nerede çıktığı belli oluyor.
                _tara_bar = st.progress(0.0, text="📖 Kitap açılıyor...")

                def _tara_ilerleme(_s, _n):
                    try:
                        _tara_bar.progress(
                            min(_s / max(_n, 1), 1.0),
                            text=f"📖 Taranıyor: sayfa {_s} / {_n}",
                        )
                    except Exception:
                        pass

                try:
                    try:
                        _testler, _anahtar, _uyarilar = soru_bankasi.testleri_bul(
                            _qb_path, parca_soru=_parca_secenek[_parca_ad],
                            ilerleme=_tara_ilerleme, merkezi_atla=_merkezi_atla,
                        )
                    except TypeError:
                        # Eski soru_bankasi.py: 'merkezi_atla' parametresini tanımaz.
                        _testler, _anahtar, _uyarilar = soru_bankasi.testleri_bul(
                            _qb_path, parca_soru=_parca_secenek[_parca_ad],
                        )
                except MemoryError:
                    _testler, _anahtar, _uyarilar = [], {}, [
                        "Bellek yetmedi: bu kitap tek seferde taranamıyor. "
                        "Kitabı bölüp (örneğin ders ders ayrı dosyalar hâlinde) "
                        "yeniden deneyin."
                    ]
                except Exception as e:
                    _testler, _anahtar, _uyarilar = [], {}, [f"Kitap okunamadı: {e}"]
                _tara_bar.empty()
                st.session_state[qb_path_state] = _qb_path
                st.session_state["_qb_testler"] = _testler
                st.session_state["_qb_uyarilar"] = _uyarilar
                # Hangi dosya, hangi kod sürümü, saat kaçta tarandı? Ekranda
                # eski bir tarama sonucuna bakıp "hâlâ düzelmemiş" sanmayın diye.
                st.session_state["_qb_tarama_bilgi"] = {
                    "dosya": qb_file.name,
                    "surum": getattr(soru_bankasi, "SURUM", "?"),
                    "saat": datetime.now().strftime("%H:%M:%S"),
                }
                st.session_state["_islem_balon"] = bool(_testler)
                st.rerun()

            _testler = st.session_state.get("_qb_testler")
            if _testler:
                _bilgi = st.session_state.get("_qb_tarama_bilgi") or {}
                _bc1, _bc2 = st.columns([4, 1])
                _bc1.caption(
                    f"📄 Ekrandaki sonuç: **{_bilgi.get('dosya', '?')}** · "
                    f"saat {_bilgi.get('saat', '?')} · tarayıcı sürümü "
                    f"**{_bilgi.get('surum', '?')}**"
                )
                if _bc2.button("🗑️ Sonucu temizle", key="_qb_temizle",
                               use_container_width=True):
                    for _k in ("_qb_testler", "_qb_uyarilar", "_qb_tarama_bilgi"):
                        st.session_state.pop(_k, None)
                    st.rerun()
                _eklenebilir = [t for t in _testler if t.get("cevaplar") and t.get("numaralar")]
                st.success(
                    f"Kitapta **{len(_testler)} test** bulundu; bunlardan "
                    f"**{len(_eklenebilir)} tanesi** eklenebilir durumda."
                )
                # Ünite kitaplarında parçalar zaten ortadan başlar (2. parça
                # 26. sorudan); bu normaldir, "tanıtım sürümü" uyarısı çıkmasın.
                _kirpik = [
                    t for t in _eklenebilir
                    if t.get("tur") != "unite" and (t.get("numaralar") or [1])[0] != 1
                ]
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
                # ---- Ders ders döküm: hangi test neden eklenemiyor? ----
                # Genel uyarı ("50 testin cevap anahtarı yok") kafa karıştırıyordu:
                # bir ders hem listede görünüyor hem de uyarıda geçiyordu. Sebebi,
                # bazı derslerin KISMEN eksik olması (ör. Matematik'in 4-5-6.
                # testlerinin anahtarı kitapta yok, diğerlerininki var). Bu tablo
                # durumu ders ders net gösteriyor.
                _ozet = {}
                for _t in _testler:
                    _d = _ozet.setdefault(_t["ders"], {"bulunan": 0, "eklenebilir": 0})
                    _d["bulunan"] += 1
                    if _t.get("cevaplar") and _t.get("numaralar"):
                        _d["eklenebilir"] += 1
                _unite_kitabi = any(t.get("tur") == "unite" for t in _testler)
                if _unite_kitabi:
                    st.info(
                        "📗 Bu bir **ünite/tema düzenli çalışma kitabı**. Aşağıdaki listede "
                        "her satır bir ünitedir. Kitabın sonundaki **geçmiş yıl merkezî sınav "
                        "soruları alınmadı** — siz sadece ünite sorularını istemiştiniz."
                    )
                st.markdown("##### 📊 Ders ders durum")
                _tablo(pd.DataFrame([
                    {
                        "Ders": _d,
                        "Kitapta bulunan": _v["bulunan"],
                        "Eklenebilir": _v["eklenebilir"],
                        "Eklenemeyen": _v["bulunan"] - _v["eklenebilir"],
                        "Sebep": (
                            "—"
                            if _v["eklenebilir"] == _v["bulunan"]
                            else ("Bu dersin cevap anahtarı PDF'te hiç yok"
                                  if _v["eklenebilir"] == 0
                                  else "Bazı testlerin cevap anahtarı PDF'te eksik")
                        ),
                    }
                    for _d, _v in sorted(_ozet.items())
                ]))
                if _unite_kitabi and _eklenebilir:
                    _tablo(pd.DataFrame([
                        {
                            "Ders": t["ders"],
                            "Ünite / Test": t["konu"],
                            "Soru": len(t.get("numaralar") or []),
                            "Sayfa": len(t.get("sayfalar") or []),
                            "Kitapta sayfa": (
                                f"{t['sayfalar'][0]}-{t['sayfalar'][-1]}" if t.get("sayfalar") else "—"
                            ),
                        }
                        for t in _eklenebilir
                    ]))
                if len(_eklenebilir) < len(_testler):
                  st.caption(
                    "**Eklenemeyenler neden eklenemiyor?** Bir testi puanlayabilmek için cevap "
                    "anahtarı şart. Bu PDF'in sonundaki cevap anahtarı bölümü eksik: bazı derslerin "
                    "anahtarı hiç yok, bazılarında birkaç test atlanmış. Anahtarı olmayan testi "
                    "eklemek, çocuğun çözüp sonuç alamaması demek olurdu; o yüzden sistem onları "
                    "atlıyor. Kitabın **tam sürümünü** bulursanız aynı işlem hepsini ekler."
                  )
                for _u in st.session_state.get("_qb_uyarilar", []):
                    # "ℹ️" ile başlayanlar hata değil, bilgi notudur.
                    (st.info if str(_u).startswith("ℹ️") else st.warning)(_u)
                if not _eklenebilir:
                    st.error("Cevap anahtarı okunabilen test yok, ekleme yapılamıyor.")
                else:
                    _dersler = sorted({t["ders"] for t in _eklenebilir})
                    _secili_ders = st.selectbox("Ders", _dersler, key="qb_ders")
                    _bu_ders = [t for t in _eklenebilir if t["ders"] == _secili_ders]

                    def _qb_etiket_ad(t):
                        """Denemenin ders adı OLMADAN adı -- düzenlenebilir
                        tabloda bu değer gösterilip elle değiştirilebiliyor."""
                        if t.get("tur") == "unite":
                            return t["konu"]
                        return f"Test {t['test_no']} · {t['konu']}"

                    def _qb_basligi(t, ad=None):
                        """Denemenin tam adı. Ünite kitaplarında 'Test 3' demek
                        anlamsız olurdu; ünite adı zaten 'konu' alanında.
                        `ad` verilirse (tablodan elle yazılmışsa) o kullanılır."""
                        return f"{t['ders']} · {(ad or _qb_etiket_ad(t)).strip()}"

                    def _qb_etiket(t):
                        _nums = t.get("numaralar") or []
                        if _nums and _nums[0] != 1:
                            _ek = f"{len(_nums)} soru · kitapta {_nums[0]}-{_nums[-1]}"
                        else:
                            _ek = f"{len(_nums)} soru"
                        _sy = t.get("sayfalar") or []
                        if _sy:
                            _ek += f" · {len(_sy)} sayfa"
                        _ad = t["konu"] if t.get("tur") == "unite" else f"Test {t['test_no']} · {t['konu']}"
                        return f"{_ad} ({_ek})"

                    # Tarama kimliği: hangi KİTABIN sonucuna bakıyoruz.
                    # Kutu anahtarlarına ekleniyor ki bir kitaptan kalan
                    # değerler bir sonraki kitaba taşınmasın.
                    _tarama_kimlik = slugify(os.path.basename(
                        str(st.session_state.get(qb_path_state) or "kitap")))[:24]
                    _unite_mi = any(t.get("tur") == "unite" for t in _bu_ders)
                    _varsayilan_kat = (
                        f"Ünite Testleri - {_secili_ders}" if _unite_mi
                        else f"Soru Bankası - {_secili_ders}"
                    )
                    # ÖNEMLİ - "TÜRKÇE KİTABI 'İNGİLİZCE' BÖLÜMÜNE KAYDEDİLDİ":
                    # Bu kutunun anahtarı sabit ("qb_kategori") idi. Streamlit'te
                    # bir kutunun anahtarı sabit kalırsa, `value=` ile verilen
                    # YENİ varsayılan YOK SAYILIR; kutu bir önceki değerini
                    # korur. Sonuç: Din Kültürü'nü ekledikten sonra derse
                    # İngilizce'ye geçince kutuda hâlâ eski bölüm adı duruyor,
                    # hatta BAŞKA BİR KİTABI taradığınızda bile duruyordu.
                    # Kullanıcının gördüğü tam olarak buydu: Türkçe kitabının
                    # testleri "Ünite Testleri - İngilizce" bölümüne düştü.
                    # Artık her dersin kendi kutusu var; ders değişince
                    # varsayılan bölüm adı da değişiyor.
                    _kategori_adi = st.text_input(
                        "Hangi bölüme eklensin?", value=_varsayilan_kat,
                        key=f"qb_kategori_{_tarama_kimlik}_{slugify(_secili_ders)}",
                        help="Öğrenci 'Sınav Çöz' ekranında bu adı görecek.",
                    )
                    # KULLANICI GERİ BİLDİRİMİ ("ekledim ama veriler ekranda
                    # aynen duruyor, eklendi mi anlamadım"): Eklenen testler
                    # artık listeden DÜŞÜYOR. Böylece ekranda sadece HENÜZ
                    # EKLENMEMİŞ testler kalıyor; iş bitince liste boşalıyor
                    # ve yeşil "hepsi ekli" yazısı çıkıyor.
                    _hedef_kat = (_kategori_adi or _varsayilan_kat).strip()
                    _mevcut = {
                        (x["title"], x["category"]) for x in db.get_exams()
                    }
                    _zaten = [t for t in _bu_ders if (_qb_basligi(t), _hedef_kat) in _mevcut]
                    _kalan = [t for t in _bu_ders if (_qb_basligi(t), _hedef_kat) not in _mevcut]
                    if _zaten:
                        st.success(
                            f"✅ Bu dersin **{len(_zaten)} testi** zaten "
                            f"**{_hedef_kat}** bölümüne eklenmiş; listede gösterilmiyor."
                        )
                    if not _kalan:
                        st.info(
                            f"**{_secili_ders}** dersinin eklenebilir bütün testleri "
                            f"eklendi. Yukarıdaki **Ders** kutusundan başka bir derse "
                            f"geçebilir ya da yeni bir kitap yükleyebilirsiniz."
                        )
                    # =====================================================
                    #  ADLARI ELLE DÜZELTME
                    # =====================================================
                    # ÖNEMLİ - "HER PDF YAPISINA GÖRE KOD MU YAZACAĞIM?":
                    # Hayır. Her yayınevinin kendi dizgisi olduğu için
                    # ünite adının PDF'ten HER ZAMAN okunabilmesi mümkün
                    # değil; bazı kitaplarda ad sayfada hiç yazmaz, bazısında
                    # yazı tipi bozuktur. Program okuyabildiğini okur, geri
                    # kalanı "3. Ünite" diye bırakır -- ve siz aşağıdaki
                    # tablodan **adı doğrudan yazarsınız**. Yeni bir kitap
                    # düzeni için kod değişikliği gerekmez.
                    st.markdown("##### ✏️ Eklenecek testler — adları düzenleyebilirsiniz")
                    st.caption(
                        "**Ad** sütununa tıklayıp istediğiniz gibi yazın (örn. "
                        "*3. Ünite · Basınç*). **Ekle** kutusunun işaretini "
                        "kaldırdığınız testler eklenmez. Sorular ve cevap anahtarı "
                        "bu düzenlemeden etkilenmez."
                    )
                    _tablo_satirlari = [
                        {
                            "Ekle": True,
                            "Ad": _qb_etiket_ad(t),
                            "Soru": len(t.get("numaralar") or []),
                            "Sayfa": (f"{t['sayfalar'][0]}-{t['sayfalar'][-1]}"
                                      if t.get("sayfalar") else "—"),
                        }
                        for t in _kalan
                    ]
                    _duzenleyici = getattr(st, "data_editor", None)
                    if _duzenleyici and _tablo_satirlari:
                        _duzenlenmis = _duzenleyici(
                            pd.DataFrame(_tablo_satirlari),
                            key=f"qb_duzen_{_tarama_kimlik}_{_secili_ders}_{len(_kalan)}",
                            use_container_width=True,
                            hide_index=True,
                            disabled=["Soru", "Sayfa"],
                            column_config={
                                "Ekle": st.column_config.CheckboxColumn(
                                    "Ekle", width="small"),
                                "Ad": st.column_config.TextColumn(
                                    "Ad (düzenlenebilir)", width="large",
                                    help="Öğrencinin listede göreceği ad."),
                                "Soru": st.column_config.NumberColumn("Soru", width="small"),
                                "Sayfa": st.column_config.TextColumn(
                                    "Kitapta sayfa", width="small"),
                            },
                        )
                        _secilenler, _adlar = [], {}
                        for _i, _satir in enumerate(_duzenlenmis.to_dict("records")):
                            if not _satir.get("Ekle"):
                                continue
                            _secilenler.append(_kalan[_i])
                            _adlar[id(_kalan[_i])] = (str(_satir.get("Ad") or "").strip()
                                                      or _qb_etiket_ad(_kalan[_i]))
                    else:
                        # Çok eski Streamlit sürümü: düzenlenebilir tablo yok,
                        # eski çoklu seçim kutusuna dönülür.
                        _secilenler = st.multiselect(
                            "Eklenecek testler", _kalan, default=_kalan,
                            format_func=_qb_etiket,
                            key=f"qb_secim_{_tarama_kimlik}_{_secili_ders}_{len(_kalan)}",
                        )
                        _adlar = {}
                    st.caption(
                        f"{len(_secilenler)} test seçili. Her biri ayrı bir deneme olarak "
                        f"**{_hedef_kat}** bölümüne eklenir."
                    )
                    if st.button("✅ Seçilen Testleri Ekle", type="primary", disabled=not _secilenler):
                        _kategori = (_kategori_adi or _varsayilan_kat).strip()
                        db.add_category(_kategori)
                        _kaynak = st.session_state.get(qb_path_state)
                        _flash, _eklendi, _atlandi = [], 0, 0
                        _bar = st.progress(0.0, text="Testler ekleniyor...")
                        for _n, _t in enumerate(_secilenler, start=1):
                            _baslik = _qb_basligi(_t, _adlar.get(id(_t)))
                            if db.exam_exists(_baslik, _kategori):
                                _atlandi += 1
                                _bar.progress(_n / len(_secilenler), text=f"{_n}/{len(_secilenler)}")
                                continue
                            _hedef = os.path.join(
                                PDF_DIR,
                                f"sb_{slugify(_secili_ders)}_{_t['test_no']}_{slugify(_t['konu'])[:40]}.pdf",
                            )
                            try:
                                # Sayfada GERÇEKTEN basılı olan soru numaraları
                                # (kitabın ilk sayfası yoksa test 4. sorudan
                                # başlayabilir) -- optik form bunlarla birebir
                                # aynı numaraları gösterecek.
                                # Ünite düzenli kitaplarda numaralar zaten
                                # tarama sırasında sayfa sayfa okundu; klasik
                                # soru bankalarında ise burada bulunuyor.
                                _numaralar = _t.get("numaralar") or soru_bankasi.gorunen_sorular(
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
                                _yeni_id, _uyari = _deneme_ekle(
                                    _baslik, _kategori, _hedef, _yapi,
                                    {"Genel": {_secili_ders: _sirali}},
                                    source="soru-bankasi",
                                )
                                if _uyari:
                                    _flash.append(("error", _uyari))
                                _eklendi += 1
                            except Exception as e:
                                _flash.append(("error", f"❌ {_baslik}: {e}"))
                            _bar.progress(_n / len(_secilenler), text=f"{_n}/{len(_secilenler)}")
                        _flash.insert(0, (
                            "success",
                            f"✅ {_eklendi} test eklendi"
                            + (f", {_atlandi} test zaten vardı (atlandı)." if _atlandi else "."),
                        ))
                        if _eklendi:
                            st.session_state["_islem_balon"] = True
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
                                _eid, _uy = _deneme_ekle(
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
                        _eid, _uy = _deneme_ekle(
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
            st.markdown(
                "**Her türlü sınav buradan eklenir** — 6/7. sınıf denemeleri, bursluluk, "
                "konu testleri, hatta **KPSS, ALES, YDS** gibi sınavlar. Ayrı bir bölüm "
                "açmaya gerek yok: yeni bir **kategori adı** yazın (örn. *KPSS*), sınav "
                "oraya eklensin.\n\n"
                "Sistem PDF'i tarayıp **hangi dersler var, her birinde kaç soru var ve "
                "cevaplar neler** — hepsini kendi bulmayı dener. Cevap anahtarı **ayrı bir "
                "PDF'teyse** (ÖSYM sınavlarında genelde öyledir) onu da aşağıya yükleyin."
            )
            cats = db.get_categories()
            other_cats = [c for c in cats if c != LGS_CATEGORY] or cats
            colc1, colc2 = st.columns([2, 1])
            with colc1:
                gcat = st.selectbox("Kategori", other_cats, key="gen_cat")
            with colc2:
                new_cat = st.text_input("Yeni kategori adı (örn. KPSS)")
                if st.button("Kategori Ekle") and new_cat:
                    db.add_category(new_cat)
                    st.rerun()

            _u1, _u2 = st.columns(2)
            uploaded = _u1.file_uploader(
                "📘 Soru kitapçığı PDF'i", type=["pdf"], key="gen_pdf")
            key_pdf = _u2.file_uploader(
                "🔑 Cevap anahtarı AYRI PDF'te ise (isteğe bağlı)", type=["pdf"], key="gen_key_pdf",
                help="ÖSYM sınavlarında cevap anahtarı ayrı yayımlanır. Boş bırakırsanız "
                     "anahtar, kitapçığın kendi sayfalarında aranır.")

            # ---- Otomatik keşif ----
            # ÖNEMLİ: Eskiden ders adlarını ve soru sayılarını ELLE yazmak
            # gerekiyordu; KPSS gibi 120 soruluk sınavlarda bu hem zahmetli
            # hem hataya açıktı. Artık cevap anahtarı sayfasındaki ders
            # başlıkları okunup her şey otomatik dolduruluyor.
            _kesif = st.session_state.get("_man_kesif")
            if uploaded or key_pdf:
                if st.button("🔎 PDF'i tara — dersleri ve cevapları otomatik bul",
                             type="primary", use_container_width=True, key="_man_tara"):
                    _kaynak = key_pdf or uploaded
                    _gecici = os.path.join(PRIVATE_DIR, "_manuel_tarama.pdf")
                    try:
                        _kaynak.seek(0)
                        with open(_gecici, "wb") as _f:
                            _f.write(_kaynak.read())
                        _kaynak.seek(0)
                        with st.spinner("PDF taranıyor: dersler ve cevap anahtarı aranıyor..."):
                            _cev, _idx, _msj = parsing.anahtar_kesfet(_gecici)
                        if _cev:
                            st.session_state["_man_kesif"] = {
                                "cevaplar": _cev, "sayfa": _idx,
                                "ayri": key_pdf is not None, "mesaj": _msj,
                            }
                            st.session_state["_islem_balon"] = True
                        else:
                            st.session_state["_man_kesif"] = None
                            st.session_state["_man_hata"] = _msj
                    except Exception as _e:
                        st.session_state["_man_kesif"] = None
                        st.session_state["_man_hata"] = str(_e)
                    st.rerun()
            if st.session_state.pop("_man_hata", None):
                st.warning(
                    "Cevap anahtarı otomatik bulunamadı. Aşağıya dersleri ve cevapları "
                    "elle girebilirsiniz — kayıt yine sorunsuz yapılır."
                )
            if _kesif:
                _kt = ", ".join(f"**{d}** {len(v)} soru" for d, v in _kesif["cevaplar"].items())
                st.success(
                    f"✅ Bulundu: {_kt}  ·  toplam "
                    f"**{sum(len(v) for v in _kesif['cevaplar'].values())} soru**"
                    + ("  ·  anahtar ayrı dosyadan okundu." if _kesif["ayri"]
                       else f"  ·  anahtar kitapçığın {(_kesif['sayfa'] or 0) + 1}. sayfasında "
                            f"(o sayfa ve sonrası öğrenciye gösterilmez).")
                )
                if st.button("↩️ Tarama sonucunu temizle, elle gireyim", key="_man_temizle"):
                    st.session_state.pop("_man_kesif", None)
                    st.rerun()

            default_title, default_subject = ("", None)
            if uploaded:
                default_title, default_subject = guess_title_and_subject(uploaded.name)
            title = st.text_input("Test Adı", value=default_title, key="gen_title")

            if _kesif:
                subject_rows = [(d, len(v)) for d, v in _kesif["cevaplar"].items()]
                st.caption("Dersler ve soru sayıları PDF'ten okundu; elle girmenize gerek yok.")
            else:
                st.caption(
                    "Bu PDF'teki ders sütunlarını, PDF'te SOLDAN SAĞA hangi sırayla "
                    "göründüğüyle AYNI sırada girin."
                )
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

            parsed_key = None
            _kes_sayfa = None
            if _kesif:
                parsed_key = {"Genel": _kesif["cevaplar"]}
                _kes_sayfa = None if _kesif["ayri"] else _kesif["sayfa"]
            elif uploaded and subject_rows and st.checkbox(
                    "Cevap anahtarını PDF'in son sayfasından okumayı dene", value=True,
                    key="gen_autotry"):
                key, msg, idx = parsing.extract_answer_key(uploaded, subject_rows)
                if key:
                    st.success("Cevap anahtarı otomatik okundu ✅")
                    parsed_key = {"Genel": key}
                    _kes_sayfa = idx
                else:
                    st.warning(f"Otomatik okunamadı: {msg} Aşağıdan elle girebilirsiniz.")

            manual_answers = {}
            if parsed_key is None and subject_rows:
                st.markdown("**Cevap anahtarını elle girin**")
                st.caption("Harfleri sırayla yazın; virgül, boşluk veya bitişik — hepsi olur.")
                for name, cnt in subject_rows:
                    txt = st.text_input(f"{name} cevapları ({cnt} adet)", key=f"gen_manual_{name}")
                    _h = [c for c in (txt or "").upper() if c in "ABCDE"]
                    manual_answers[name] = _h
                    if _h:
                        st.caption(("✅ " if len(_h) == cnt else "⚠️ ")
                                   + f"{len(_h)} cevap okundu ({cnt} olmalı).")

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
                            st.error("Girilen cevap sayıları soru sayılarıyla eşleşmiyor.")
                            st.stop()
                        final_key = {"Genel": manual_answers}

                    safe_title = slugify(title, "test")
                    orig_path = None
                    if uploaded:
                        safe_path = os.path.join(PDF_DIR, f"{safe_title}_guvenli.pdf")
                        with st.spinner("PDF hazırlanıyor ve küçültülüyor..."):
                            # Anahtar AYRI dosyadaysa kitapçıktan sayfa kırpılmaz.
                            _kes = _kes_sayfa if _kes_sayfa is not None \
                                else parsing.pdf_page_count(uploaded)
                            parsing.crop_and_merge([(uploaded, _kes)], safe_path)
                            orig_path = os.path.join(PRIVATE_DIR, f"{safe_title}_orijinal.pdf")
                            parsing.merge_full([uploaded], orig_path)
                    else:
                        safe_path = ""
                    _eid, _uy = _deneme_ekle(title, gcat, safe_path, structure, final_key,
                                             source="manuel", pdf_path_original=orig_path)
                    _msj = [("success", f"✅ '{title}' → **{gcat}** kategorisine eklendi "
                                        f"({sum(c for _n, c in subject_rows)} soru).")]
                    if _uy:
                        _msj.append(("error", _uy))
                    st.session_state["_admin_flash"] = _msj
                    st.session_state["_islem_balon"] = True
                    st.session_state.pop("_man_kesif", None)
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
                _tani = []
                with st.spinner("MEB sayfası taranıyor..."):
                    try:
                        _bulunan = bot.scrape_bursluluk(ayrinti=_tani)
                    except TypeError:
                        # KISMİ GÜNCELLEME KORUMASI: GitHub'a app.py yüklenip
                        # bot.py eski bırakılırsa, eski sürüm 'ayrinti'
                        # parametresini tanımaz ve uygulama komple çökerdi.
                        # Artık eski sürümle de çalışıyor, sadece teşhis bilgisi
                        # olmuyor ve ekranda uyarı çıkıyor.
                        _bulunan = bot.scrape_bursluluk()
                        _tani.append(
                            "⚠️ bot.py dosyanız eski sürümde — ayrıntılı teşhis yapılamıyor. "
                            "GitHub'a güncel bot.py dosyasını da yükleyin."
                        )
                    except Exception as e:
                        _bulunan = {}
                        _tani.append(f"Beklenmeyen hata: {e}")
                st.session_state[_bl_key] = {f"{y}|{s}": u for (y, s), u in _bulunan.items()}
                st.session_state["_bursluluk_tani"] = _tani
                st.rerun()

            _liste = st.session_state.get(_bl_key)
            if _liste is not None and not _liste:
                st.error(
                    "Kitapçık bağlantısı bulunamadı. Aşağıdaki teşhis bilgisi sebebini gösteriyor:"
                )
                for _t in st.session_state.get("_bursluluk_tani", []):
                    st.code(_t)
                st.caption(
                    "Bu sayfa MEB'e ait bir okul sitesinde barındırılıyor ve zaman zaman "
                    "erişime kapanabiliyor. Bu arada kitapçıkları elle de ekleyebilirsiniz: "
                    "**URL'den PDF İndir** bölümüne bağlantıyı yapıştırın, ardından "
                    "**Diğer Kategori / Soru Bankası Ekle (Manuel)** ile işleyin."
                )
                with st.expander("🔧 Ayrıntılı teşhis (sayfanın ham içeriği)"):
                    if st.button("Sayfayı ham haliyle getir", key="bl_ham"):
                        st.code(bot.sayfa_ham_getir()[:1500])
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
                        _ham = os.path.join(INDIRME_DIR, f"_iokbs_{_y}_{_s}.pdf")
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
                            # ÖNEMLİ - ASIL SORUN BUYDU: Kod "her ders 25 soru,
                            # toplam 100" diye SABİT bir yapı bekliyordu. Ama
                            # bursluluk kitapçıklarında soru sayısı yıla ve
                            # sınıfa göre değişiyor -- 2024 5. sınıfta her ders
                            # 20 soru (toplam 80). Cevaplar doğru okunuyor,
                            # sonra "100 bekliyordum, 80 buldum" denip HEPSİ
                            # atılıyordu. Artık soru sayısı VARSAYILMIYOR:
                            # cevap anahtarı sayfasındaki ders başlıkları ve
                            # her sütundaki cevap sayısı SAYFADAN öğreniliyor.
                            try:
                                _kesif, _kidx2, _kmsg2 = parsing.anahtar_kesfet(_ham)
                            except Exception as _ex:
                                _kesif, _kidx2, _kmsg2 = None, None, str(_ex)
                            if _kesif:
                                _yapi_satir = [(_d, len(_v)) for _d, _v in _kesif.items()]
                                _key, _kidx = _kesif, _kidx2
                                _kmsg = _kmsg2
                        if _key is None:
                            # ÖNEMLİ - "HER FARKLI PDF İÇİN KOD MU YAZACAĞIZ?"
                            # Hayır. Cevap anahtarı okunamayan kitapçık artık
                            # ÇÖPE GİTMİYOR: dosya saklanıyor ve aşağıdaki
                            # "Cevap anahtarını elle gir" bölümünde listeleniyor.
                            # Kullanıcı anahtarı bir kez yapıştırınca kitapçık
                            # eklenir -- kod değişikliği gerekmez.
                            _bekleyen = os.path.join(PRIVATE_DIR, f"bekleyen_iokbs_{_y}_{_s}.pdf")
                            try:
                                shutil.copyfile(_ham, _bekleyen)
                                _bek = json.loads(db.get_setting("bekleyen_anahtar", "") or "{}")
                                _bek[_baslik] = {
                                    "yol": _bekleyen, "kategori": _kategori,
                                    "yapi": _yapi_satir, "sebep": _kmsg,
                                    "sayfa": _kidx if _kidx is not None else -1,
                                }
                                db.set_setting("bekleyen_anahtar", json.dumps(_bek, ensure_ascii=False))
                                _bl_flash.append((
                                    "error",
                                    f"⚠️ {_baslik}: indirildi ama cevap anahtarı otomatik "
                                    f"okunamadı ({_kmsg}).  **Kitapçık saklandı** — aşağıdaki "
                                    f"**📝 Cevap anahtarı bekleyenler** bölümünden anahtarı "
                                    f"bir kez yapıştırın, deneme hemen eklenecek.",
                                ))
                            except Exception as _ex:
                                _bl_flash.append((
                                    "error",
                                    f"⚠️ {_baslik}: cevap anahtarı okunamadı ve dosya "
                                    f"saklanamadı ({_ex}).",
                                ))
                            continue
                        _guvenli = os.path.join(PDF_DIR, f"iokbs_{_y}_{_s}_guvenli.pdf")
                        _bar.progress(_oran, text=f"{_baslik}: PDF hazırlanıyor...")
                        parsing.crop_and_merge([(_ham, _kidx)], _guvenli)
                        _orij = os.path.join(PRIVATE_DIR, f"iokbs_{_y}_{_s}_orijinal.pdf")
                        parsing.merge_full([_ham], _orij)
                        _eid, _uy = _deneme_ekle(
                            _baslik, _kategori, _guvenli,
                            build_generic_structure(_yapi_satir),
                            {"Genel": _key}, source="otomatik-iokbs",
                            pdf_path_original=_orij,
                        )
                        if _uy:
                            _bl_flash.append(("error", _uy))
                        _ekl += 1
                        _ozet = " + ".join(f"{_d} {_c}" for _d, _c in _yapi_satir)
                        _bl_flash.append((
                            "success",
                            f"✅ {_baslik}: eklendi ({_ozet} = {sum(c for _n, c in _yapi_satir)} soru)."
                            + _compression_note(_guvenli),
                        ))
                    _bar.empty()
                    _bl_flash.insert(0, (
                        "success",
                        f"Bitti: **{_ekl} kitapçık eklendi**"
                        + (f", {_atl} tanesi zaten vardı." if _atl else "."),
                    ))
                    if _ekl:
                        st.session_state["_islem_balon"] = True
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
                    res = bot.fetch_lgs_year(yil, INDIRME_DIR, scraped=_scraped)
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
                    _eid, _uy = _deneme_ekle(
                        exam_title, LGS_CATEGORY, safe_path, LGS_STRUCTURE,
                        {"Sözel": sozel_key, "Sayısal": sayisal_key}, source="otomatik-eba",
                        pdf_path_original=orig_path,
                    )
                    if _uy:
                        _eba_flashes.append(("error", _uy))
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
                dest = os.path.join(INDIRME_DIR, slugify(os.path.splitext(fname)[0], "indirilen") + ".pdf")
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
                        dest = os.path.join(INDIRME_DIR, f["name"])
                        drive_sync.download_file(f["id"], dest)
                        st.success(f"İndirildi: {f['name']} — 'Diğer Kategori Ekle' bölümünden işleyebilirsiniz.")

        # ---------------- Kayıtlı denemeler ----------------
        elif admin_section == "📝 Cevap Anahtarı Bekleyenler":
            st.markdown("#### 📝 Cevap anahtarı bekleyen kitapçıklar")
            st.markdown(
                "Bazı kitapçıkların cevap anahtarı sayfası, otomatik okunamayacak "
                "kadar farklı bir düzende olabiliyor (her yayının kendi tasarımı var). "
                "**Bu yüzden hiçbir kitapçık çöpe gitmiyor:** indirilen dosya burada "
                "saklanıyor. Anahtarı **bir kez** yapıştırdığınızda deneme eklenir — "
                "yeni bir düzen için kod değişikliğine gerek kalmaz."
            )
            try:
                _bek = json.loads(db.get_setting("bekleyen_anahtar", "") or "{}")
            except Exception:
                _bek = {}
            if not _bek:
                st.success("🎉 Cevap anahtarı bekleyen kitapçık yok.")
            else:
                _ad = st.selectbox("Kitapçık", sorted(_bek.keys()), key="_bek_sec")
                _kayit = _bek[_ad]
                _yapi_satir = [tuple(x) for x in _kayit["yapi"]]
                _toplam = sum(c for _n, c in _yapi_satir)
                st.caption(
                    f"**{_ad}** · {len(_yapi_satir)} ders · toplam {_toplam} soru\n\n"
                    f"_Otomatik okuma neden başarısız oldu: {_kayit.get('sebep', '—')}_"
                )
                if os.path.exists(_kayit["yol"]):
                    with st.expander("📄 Kitapçığın cevap anahtarı sayfasına bak", expanded=True):
                        try:
                            _sayfa_sayisi = _pdf_page_count(
                                _kayit["yol"], os.stat(_kayit["yol"]).st_mtime_ns)
                            _varsayilan = _kayit.get("sayfa", -1)
                            if _varsayilan is None or _varsayilan < 0:
                                _varsayilan = _sayfa_sayisi - 1
                            _bakilan = st.number_input(
                                f"Sayfa (1 - {_sayfa_sayisi})", min_value=1,
                                max_value=_sayfa_sayisi, value=min(_varsayilan + 1, _sayfa_sayisi),
                                key=f"_bek_sayfa_{_ad}",
                            )
                            st.image(
                                _pdf_page_image(_kayit["yol"], os.stat(_kayit["yol"]).st_mtime_ns,
                                                int(_bakilan) - 1, dpi=190),
                                use_container_width=True,
                            )
                            st.caption(
                                "Cevap anahtarı bu sayfada değilse yukarıdaki sayfa "
                                "numarasını değiştirin. **Kırpma** bu sayfadan yapılır: "
                                "öğrenci bu sayfayı ve sonrasını GÖRMEZ."
                            )
                        except Exception as _e:
                            st.warning(f"Sayfa gösterilemedi: {_e}")
                else:
                    st.error("Saklanan dosya bulunamadı; kitapçığı yeniden indirin.")

                st.markdown("##### Cevapları girin")
                st.caption(
                    "Her ders için harfleri sırayla yazın. Aralarına virgül, boşluk "
                    "koyabilir ya da bitişik yazabilirsiniz — **ABCDA**, **A,B,C,D,A** "
                    "ve **A B C D A** aynı şeydir."
                )
                _girilen, _hatali = {}, []
                for _ders, _adet in _yapi_satir:
                    _txt = st.text_area(
                        f"{_ders} ({_adet} soru)", key=f"_bek_cev_{_ad}_{_ders}", height=68,
                    )
                    _harfler = [c for c in (_txt or "").upper() if c in "ABCDE"]
                    _girilen[_ders] = _harfler
                    if _harfler:
                        if len(_harfler) == _adet:
                            st.caption(f"✅ {_ders}: {len(_harfler)} cevap okundu.")
                        else:
                            st.caption(f"⚠️ {_ders}: {len(_harfler)} harf girdiniz, {_adet} olmalı.")
                            _hatali.append(_ders)
                    else:
                        _hatali.append(_ders)

                _c1, _c2 = st.columns([2, 1])
                if _c1.button("✅ Bu kitapçığı ekle", type="primary",
                              disabled=bool(_hatali), key=f"_bek_ekle_{_ad}"):
                    try:
                        _kes = int(_bakilan) - 1
                        _guv = os.path.join(PDF_DIR, f"{slugify(_ad)}_guvenli.pdf")
                        parsing.crop_and_merge([(_kayit["yol"], _kes)], _guv)
                        _orj = os.path.join(PRIVATE_DIR, f"{slugify(_ad)}_orijinal.pdf")
                        parsing.merge_full([_kayit["yol"]], _orj)
                        db.add_category(_kayit["kategori"])
                        _eid, _uy = _deneme_ekle(
                            _ad, _kayit["kategori"], _guv,
                            build_generic_structure(_yapi_satir),
                            {"Genel": _girilen}, source="elle-cevap",
                            pdf_path_original=_orj,
                        )
                        _bek.pop(_ad, None)
                        db.set_setting("bekleyen_anahtar", json.dumps(_bek, ensure_ascii=False))
                        _msj = [("success", f"✅ **{_ad}** eklendi ({_toplam} soru).")]
                        if _uy:
                            _msj.append(("error", _uy))
                        st.session_state["_admin_flash"] = _msj
                        st.session_state["_islem_balon"] = True
                        st.rerun()
                    except Exception as _e:
                        st.error(f"Eklenemedi: {_e}")
                if _c2.button("🗑️ Listeden çıkar", key=f"_bek_sil_{_ad}"):
                    _bek.pop(_ad, None)
                    db.set_setting("bekleyen_anahtar", json.dumps(_bek, ensure_ascii=False))
                    st.session_state["_admin_flash"] = ("success", f"{_ad} listeden çıkarıldı.")
                    st.rerun()

        elif admin_section == "🩹 Eksik Kitapçıkları Onar":
            st.markdown("#### 🩹 Kitapçığı kaybolan denemeler")
            st.markdown(
                "**Sorun neydi?** Streamlit'in ücretsiz bulut sunucusu, uygulama her "
                "güncellendiğinde diski **sıfırlıyor**. Sınav kayıtları kalıcı "
                "veritabanında (Supabase) durduğu için listede görünmeye devam ediyor, "
                "ama **kitapçık dosyaları siliniyordu** — bu yüzden denemeyi açınca "
                "*\"PDF dosyası sunucuda bulunamadı\"* yazıyordu.\n\n"
                "**Artık kalıcı:** Bu güncellemeden sonra eklenen her denemenin "
                "kitapçığı veritabanına da yazılıyor. Aşağıdaki liste, ESKİ eklenmiş "
                "ve dosyası kaybolmuş denemeleri gösterir."
            )
            _tum = db.get_exams()
            try:
                _saklanan = db.pdf_saklananlar()
            except Exception:
                _saklanan = {}
            _bozuk, _saglam = [], 0
            for _e in _tum:
                _y = _e.get("pdf_path") or ""
                _diskte = bool(_y) and os.path.exists(_y) and os.path.getsize(_y) > 0
                if _diskte or _e["id"] in _saklanan or _e.get("source_url"):
                    _saglam += 1
                else:
                    _bozuk.append(_e)

            # ---- GÜVENLİK: static klasöründe kalmış CEVAP ANAHTARLI ham dosyalar ----
            # Eski sürüm, internetten inen HAM kitapçıkları (anahtar sayfası
            # hâlâ içindeyken) herkese açık "static/pdfs" klasörüne yazıyordu.
            # Dosya adları tahmin edilebilir olduğu için giriş yapmadan
            # indirilebiliyorlardı. Yeni sürüm korumalı klasöre indiriyor;
            # aşağıdaki düğme eskiden kalanları temizler.
            _acikta = []
            try:
                _acikta = [a for a in os.listdir(PDF_DIR)
                           if a.startswith("_indirilen_") or a.startswith("_iokbs_")]
            except OSError:
                pass
            if _acikta:
                st.error(
                    f"🔓 **Güvenlik uyarısı — {len(_acikta)} artık dosya**\n\n"
                    f"Programın eski sürümü, internetten indirdiği kitapçıkların "
                    f"**ham hâlini** (cevap anahtarı sayfası hâlâ içindeyken) "
                    f"herkese açık klasöre bırakıyordu. Dosya adları tahmin "
                    f"edilebilir olduğu için, adresi bilen biri giriş yapmadan "
                    f"cevap anahtarını indirebilir.\n\n"
                    f"**Bunlar çöp dosyalardır.** Denemeleriniz bu dosyaları "
                    f"kullanmıyor; öğrenciye gösterilen kırpılmış kitapçık ayrı "
                    f"bir dosyada ve ayrıca veritabanında duruyor."
                )
                st.success(
                    "✅ **Silmek hiçbir şeyi bozmaz.** Denemeler silinmez, sonuçlar "
                    "silinmez, hiçbir şeyi yeniden yüklemeniz gerekmez. Sadece bu "
                    "artık dosyalar diskten kalkar."
                )
                with st.expander(f"Silinecek dosyaları gör ({len(_acikta)})"):
                    st.code("\n".join(sorted(_acikta)))
                if st.button("🧹 Bu artık dosyaları sil", type="primary", key="_ham_temizle"):
                    _silinen = _ham_dosyalari_temizle()
                    st.session_state["_admin_flash"] = (
                        "success",
                        f"🧹 {len(_silinen)} ham kitapçık silindi; cevap anahtarları "
                        f"artık dışarıdan erişilebilir değil.",
                    )
                    st.rerun()

            _m1, _m2, _m3 = st.columns(3)
            _m1.metric("📚 Toplam deneme", len(_tum))
            _m2.metric("✅ Açılabilir", _saglam)
            _m3.metric("❌ Kitapçığı yok", len(_bozuk))
            if _saklanan:
                st.caption(
                    f"💾 Veritabanında saklanan kitapçık: **{len(_saklanan)} adet**, "
                    f"toplam **{sum(_saklanan.values()) / 1e6:.1f} MB**."
                )

            # Diskte duran ama veritabanına kaydedilmemiş olanları tamamla
            _yedeksiz = [
                _e for _e in _tum
                if _e["id"] not in _saklanan
                and (_e.get("pdf_path") or "")
                and os.path.exists(_e["pdf_path"]) and os.path.getsize(_e["pdf_path"]) > 0
            ]
            if _yedeksiz:
                st.info(
                    f"📥 **{len(_yedeksiz)} denemenin** kitapçığı şu an diskte duruyor ama "
                    f"kalıcı kopyası yok. Aşağıdaki düğme hepsini veritabanına yazar; "
                    f"böylece sunucu yeniden başladığında da açılabilirler."
                )
                if st.button(f"💾 {len(_yedeksiz)} kitapçığı kalıcı olarak sakla",
                             type="primary", key="_yedekle_btn"):
                    _bar = st.progress(0.0, text="Başlıyor...")
                    _ok_say, _hata = 0, []
                    for _i, _e in enumerate(_yedeksiz, start=1):
                        _bar.progress(_i / len(_yedeksiz), text=f"{_e['title']} ({_i}/{len(_yedeksiz)})")
                        try:
                            if os.path.getsize(_e["pdf_path"]) > db.PDF_SAKLAMA_SINIRI:
                                parsing.gorsel_kucult(_e["pdf_path"], sinir=db.PDF_SAKLAMA_SINIRI)
                            with open(_e["pdf_path"], "rb") as _fh:
                                _ok, _msj = db.pdf_kaydet(
                                    _e["id"], os.path.basename(_e["pdf_path"]), _fh.read())
                            if _ok:
                                _ok_say += 1
                            else:
                                _hata.append(f"{_e['title']}: {_msj}")
                        except Exception as _ex:
                            _hata.append(f"{_e['title']}: {_ex}")
                    _bar.empty()
                    _mesajlar = [("success", f"✅ {_ok_say} kitapçık kalıcı olarak saklandı.")]
                    _mesajlar += [("error", f"⚠️ {h}") for h in _hata[:10]]
                    st.session_state["_admin_flash"] = _mesajlar
                    st.session_state["_islem_balon"] = bool(_ok_say)
                    st.rerun()

            # ---- SİLMEDEN ONARIM: LGS/bursluluk kitapçıklarını yeniden indir ----
            # ÖNEMLİ: Denemeyi SİLMEK, o denemeye ait GEÇMİŞ SONUÇLARI da siler.
            # Otomatik indirilen kitapçıklar MEB arşivinden yeniden inebildiği
            # için, önce bunu deniyoruz: kayıt aynı kalır, sonuçlar korunur.
            _indirilebilir = [
                _e for _e in _bozuk
                if (_e.get("source") or "").startswith("otomatik")
                and re.search(r"(20\d{2})", _e["title"] or "")
            ]
            if _indirilebilir:
                st.info(
                    f"♻️ **{len(_indirilebilir)} tanesi MEB arşivinden yeniden "
                    f"indirilebilir.** Bu yol denemeyi SİLMEZ; aynı kayda yeni "
                    f"kitapçık bağlanır, yani **çocuğunuzun o sınavlardaki geçmiş "
                    f"sonuçları korunur.** Önce bunu deneyin."
                )
                if st.button(f"♻️ {len(_indirilebilir)} kitapçığı yeniden indir "
                             f"(sonuçlar korunur)", type="primary", key="_yeniden_indir"):
                    _bar = st.progress(0.0, text="Başlıyor...")
                    _ok_say, _mesajlar = 0, []
                    for _i, _e in enumerate(_indirilebilir, start=1):
                        _yil = int(re.search(r"(20\d{2})", _e["title"]).group(1))
                        _oran = _i / len(_indirilebilir)
                        _bar.progress(_oran, text=f"{_e['title']} indiriliyor... ({_i}/{len(_indirilebilir)})")
                        try:
                            if (_e.get("source") or "") == "otomatik-iokbs":
                                _mesajlar.append((
                                    "error",
                                    f"⚠️ {_e['title']}: bursluluk kitapçıkları yeniden "
                                    f"indirme ile onarılamıyor; 'Bursluluk (İOKBS) "
                                    f"Otomatik İndir' bölümünden yeniden ekleyin.",
                                ))
                                continue
                            _res = bot.fetch_lgs_year(_yil, INDIRME_DIR)
                            if not (_res.get("Sözel") and _res.get("Sayısal")):
                                _mesajlar.append((
                                    "error",
                                    f"❌ {_e['title']}: kitapçık MEB arşivinde bulunamadı "
                                    f"({'; '.join(_res.get('hatalar', [])[:2]) or 'sebep bilinmiyor'}).",
                                ))
                                continue
                            _bar.progress(_oran, text=f"{_e['title']}: cevap anahtarı sayfası bulunuyor...")
                            _sk, _sm, _si = parsing.extract_answer_key(
                                _res["Sözel"], [(n, c) for n, c, _ in LGS_SUBJECTS["Sözel"]])
                            _yk, _ym, _yi2 = parsing.extract_answer_key(
                                _res["Sayısal"], [(n, c) for n, c, _ in LGS_SUBJECTS["Sayısal"]])
                            if _si is None or _yi2 is None:
                                _mesajlar.append((
                                    "error",
                                    f"⚠️ {_e['title']}: indirildi ama cevap anahtarı sayfası "
                                    f"bulunamadı ({_sm or _ym}); kırpılamadı.",
                                ))
                                continue
                            _yeni_yol = os.path.join(PDF_DIR, f"{_yil}_LGS_guvenli.pdf")
                            _bar.progress(_oran, text=f"{_e['title']}: kitapçık hazırlanıyor...")
                            parsing.crop_and_merge(
                                [(_res["Sözel"], _si), (_res["Sayısal"], _yi2)], _yeni_yol)
                            _orij2 = os.path.join(PRIVATE_DIR, f"{_yil}_LGS_orijinal.pdf")
                            parsing.merge_full([_res["Sözel"], _res["Sayısal"]], _orij2)
                            if os.path.getsize(_yeni_yol) > db.PDF_SAKLAMA_SINIRI:
                                parsing.gorsel_kucult(_yeni_yol, sinir=db.PDF_SAKLAMA_SINIRI)
                            db.exam_pdf_guncelle(_e["id"], _yeni_yol, _orij2)
                            with open(_yeni_yol, "rb") as _fh:
                                _kok, _kmsj = db.pdf_kaydet(
                                    _e["id"], os.path.basename(_yeni_yol), _fh.read())
                            if _kok:
                                _ok_say += 1
                                _mesajlar.append(("success", f"✅ {_e['title']}: kitapçık geri geldi, sonuçlar korundu."))
                            else:
                                _mesajlar.append(("error", f"⚠️ {_e['title']}: indirildi ama kalıcı kopya saklanamadı ({_kmsj})."))
                        except Exception as _ex:
                            _mesajlar.append(("error", f"❌ {_e['title']}: {_ex}"))
                    _bar.empty()
                    _mesajlar.insert(0, (
                        "success" if _ok_say else "error",
                        f"♻️ {_ok_say} kitapçık yeniden indirildi ve kalıcı olarak saklandı.",
                    ))
                    st.session_state["_admin_flash"] = _mesajlar
                    st.session_state["_islem_balon"] = bool(_ok_say)
                    st.rerun()

            if not _bozuk:
                st.success("🎉 Kitapçığı kayıp deneme yok; hepsi açılabilir durumda.")
            else:
                st.warning(
                    f"Aşağıdaki **{len(_bozuk)} denemenin** kitapçığı hem diskte hem "
                    f"veritabanında yok; bunlar açılamaz. Yapılacak tek şey: **silip "
                    f"yeniden eklemek.** (Geçmiş yıl LGS ve bursluluk kitapçıkları "
                    f"'Otomatik İndirme' bölümünden tek tuşla geri gelir.)"
                )
                _tablo(pd.DataFrame([
                    {"Deneme": _e["title"], "Bölüm": _e["category"],
                     "Eklenme": _tarih_bicimle(_e.get("created_at")),
                     "Kaynak": _e.get("source") or "—"}
                    for _e in _bozuk
                ]))
                st.error(
                    "⚠️ **DİKKAT — silmek geri alınamaz.** Bir denemeyi silmek, o denemeye "
                    "ait **geçmiş sonuçları da siler** (çocuğunuzun o sınavdan aldığı netler, "
                    "soru dökümü, düzeltme turları). Öğrenci hesapları ve DİĞER sınavların "
                    "sonuçları etkilenmez.\n\n"
                    "Bu yüzden önce yukarıdaki **♻️ yeniden indir** yolunu deneyin; o yol "
                    "hiçbir şey silmez. Silme, ancak kitapçığı başka türlü geri "
                    "getirilemeyen denemeler için son çaredir."
                )
                _onay = st.checkbox(
                    f"Evet, kitapçığı kayıp {len(_bozuk)} denemeyi silmek istiyorum",
                    key="_bozuk_onay",
                )
                if st.button("🗑️ Kayıp denemeleri sil", disabled=not _onay, key="_bozuk_sil"):
                    for _e in _bozuk:
                        db.delete_exam(_e["id"])
                    st.session_state["_admin_flash"] = (
                        "success",
                        f"🗑️ {len(_bozuk)} kayıp deneme silindi. Şimdi 'Otomatik İndirme' "
                        f"veya 'Soru Bankasını Test Test Ayır' bölümünden yeniden ekleyin — "
                        f"bu kez kitapçıkları kalıcı olarak saklanacak.",
                    )
                    st.rerun()

        elif admin_section == "Kayıtlı Denemeler":
            # Bölüm bölüm ve test numarasına göre sıralı (1, 2, 3 ... 10, 11).
            all_exams = sorted(
                db.get_exams(), key=lambda e: (e["category"], _dogal_sira(e["title"]))
            )
            if not all_exams:
                st.info("Henüz kayıtlı deneme yok.")
            else:
                st.caption(f"Toplam **{len(all_exams)}** kayıtlı deneme/test var.")

            # ---- Boş bölüm adlarını temizle ---------------------------
            # KULLANICI GERİ BİLDİRİMİ: "sildiğim testlerin adları hâlâ
            # duruyor". Silinen testlerin BÖLÜM adı listede kalmaya devam
            # ediyordu ve kaldırmanın hiçbir yolu yoktu.
            _bos_kats = db.bos_kategoriler()
            if _bos_kats:
                with st.expander(
                        f"🧹 İçi boş bölüm adları ({len(_bos_kats)}) — temizleyin"):
                    st.caption(
                        "Bu bölümlerde hiç deneme kalmamış ama adları "
                        "listelerde görünmeye devam ediyor. Silmek "
                        "hiçbir denemeyi etkilemez."
                    )
                    for _bk in _bos_kats:
                        _c1, _c2 = st.columns([4, 1])
                        _c1.write(f"• {_bk}")
                        if _c2.button("Sil", key=f"_katsil_{slugify(_bk)}"):
                            _ok, _msg = db.delete_category(_bk)
                            st.session_state["_admin_flash"] = (
                                "success" if _ok else "error",
                                ("🧹 " if _ok else "") + _msg)
                            st.rerun()
                    if len(_bos_kats) > 1 and st.button(
                            f"🧹 {len(_bos_kats)} boş bölümün hepsini sil",
                            key="_katsil_hepsi"):
                        _n = sum(1 for _bk in _bos_kats
                                 if db.delete_category(_bk)[0])
                        st.session_state["_admin_flash"] = (
                            "success", f"🧹 {_n} boş bölüm adı silindi.")
                        st.rerun()

            # ---- Süzgeç + toplu silme --------------------------------
            # KULLANICI İSTEĞİ: "yanlış eklenenleri vb daha kolay silmek
            # için seçerek veya toplu sil butonu". Tek tek silmek, bir
            # kitaptan 17 test eklendiğinde 17 tıklama demekti.
            _sf1, _sf2 = st.columns([1, 2])
            _kats = ["Hepsi"] + sorted({e["category"] for e in all_exams})
            _kat_sec = _sf1.selectbox("Bölüm", _kats, key="_kd_kat")
            _ara = _sf2.text_input("Ada göre ara", key="_kd_ara",
                                   placeholder="örn. 2. Ünite, Bursluluk, KPSS...")
            _liste = [
                e for e in all_exams
                if (_kat_sec == "Hepsi" or e["category"] == _kat_sec)
                and (not _ara or _ara.strip().lower() in (e["title"] or "").lower())
            ]
            if len(_liste) != len(all_exams):
                st.caption(f"Süzgeçten geçen: **{len(_liste)}** deneme.")

            _hepsi_key = "_kd_hepsi"

            def _kd_hepsini_isaretle(_ids=tuple(e["id"] for e in _liste)):
                _deger = st.session_state.get(_hepsi_key, False)
                for _i in _ids:
                    st.session_state[f"_kd_sec_{_i}"] = _deger

            st.checkbox(f"☑️ Aşağıdaki {len(_liste)} denemenin hepsini seç",
                        key=_hepsi_key, on_change=_kd_hepsini_isaretle)
            _secili = [e for e in _liste if st.session_state.get(f"_kd_sec_{e['id']}")]

            # ÖNEMLİ - ÖLÇÜLDÜ: Onay adımına geçmek için st.rerun() çağırınca
            # Streamlit, o çalıştırmada ekrana çizilmemiş kutucukların
            # durumunu SİLİYOR; seçim kayboluyor ve onay penceresi hiç
            # açılmıyordu. Bu yüzden seçilenler, kutucuklardan bağımsız
            # ayrı bir listede saklanıyor.
            _bekleyen = st.session_state.get("_kd_onay") or []
            if _bekleyen:
                st.error(
                    f"**{len(_bekleyen)} deneme** ve bunlara ait **tüm çözüm "
                    f"sonuçları** silinecek. Bu işlem geri alınamaz."
                )
                st.caption("Silinecekler: " + ", ".join(t for _i, t in _bekleyen[:8])
                           + (" ..." if len(_bekleyen) > 8 else ""))
                _o1, _o2, _o3 = st.columns([1, 1, 3])
                if _o1.button("Evet, sil", type="primary", use_container_width=True):
                    _n = 0
                    for _id, _t in _bekleyen:
                        try:
                            db.delete_exam(_id)
                            _n += 1
                        except Exception:
                            pass
                        st.session_state.pop(f"_kd_sec_{_id}", None)
                    st.session_state.pop("_kd_onay", None)
                    st.session_state["_admin_flash"] = [
                        ("success", f"🗑️ {_n} deneme silindi.")]
                    st.rerun()
                if _o2.button("Vazgeç", use_container_width=True):
                    st.session_state.pop("_kd_onay", None)
                    st.rerun()
            elif _secili:
                if st.button(f"🗑️ Seçilen {len(_secili)} denemeyi sil",
                             type="primary"):
                    st.session_state["_kd_onay"] = [(e["id"], e["title"]) for e in _secili]
                    st.rerun()
            st.divider()

            for e in _liste:
                c0, c1, c2, c3, c4 = st.columns([0.5, 4, 2, 1, 2])
                c0.checkbox("Seç", key=f"_kd_sec_{e['id']}",
                            label_visibility="collapsed")
                c1.write(f"**{e['title']}**  ·  {e['category']}  ·  {e['source']}")
                c2.write(e["created_at"])
                if c3.button("Sil", key=f"del_{e['id']}"):
                    db.delete_exam(e["id"])
                    st.rerun()
                with c4:
                    # ÖNEMLİ - "ADMİNDE NEDEN ORİJİNAL PDF'LER YOK":
                    # Orijinal (cevap anahtarlı) kitapçık, bulut sunucusunun
                    # diskine yazılıyor ve uygulama her yeniden başladığında
                    # SİLİNİYOR. Onu da veritabanında saklamak depoyu ikiye
                    # katlardı; oysa yöneticinin asıl ihtiyacı cevapları
                    # görmek. Cevap anahtarı zaten veritabanında duruyor ve
                    # aşağıdaki düğme onu doğrudan gösteriyor -- dosya
                    # kaybolsa bile çalışır.
                    if st.button("🔑 Cevap anahtarı", key=f"keybtn_{e['id']}",
                                 use_container_width=True):
                        st.session_state["_anahtar_goster"] = e["id"]
                        st.rerun()
                    # Daha önce eklenmiş kitapçıklarda filigran duruyor
                    # olabilir; silip yeniden eklemeye gerek kalmasın diye
                    # tek düğmeyle temizlenebiliyor (sonuçlar korunur).
                    if st.button("🧼 Filigranı temizle", key=f"filbtn_{e['id']}",
                                 use_container_width=True,
                                 help="Kitapçığın üstündeki büyük gri ÖSYM/MEB damgasını "
                                      "kalıcı olarak siler; yazılar netleşir."):
                        _yol, _hata = _pdf_yolu_onbellekli(
                            e["id"], e.get("pdf_path"), e.get("source_url"))
                        if not _yol:
                            st.session_state["_admin_flash"] = [
                                ("error", f"❌ {e['title']}: kitapçık dosyası bulunamadı.")]
                        else:
                            try:
                                _n = parsing.filigrani_kaldir(_yol)
                            except Exception as _ex:
                                _n, _hata = 0, str(_ex)
                            if _n:
                                with open(_yol, "rb") as _f:
                                    db.pdf_kaydet(e["id"], os.path.basename(_yol), _f.read())
                                _pdf_yolu_onbellekli.clear()
                                _pdf_page_image.clear()
                                st.session_state["_admin_flash"] = [
                                    ("success", f"🧼 **{e['title']}**: {_n} sayfadaki "
                                                f"filigran silindi.")]
                            else:
                                st.session_state["_admin_flash"] = [
                                    ("error", f"ℹ️ **{e['title']}**: silinebilecek bir "
                                              f"filigran katmanı bulunamadı.")]
                        st.rerun()
                    if e.get("pdf_path_original") and os.path.exists(e["pdf_path_original"]):
                        show_key = f"show_orig_{e['id']}"
                        if not st.session_state.get(show_key):
                            if st.button("🔓 Orijinal PDF", key=f"origbtn_{e['id']}",
                                         use_container_width=True):
                                st.session_state[show_key] = True
                                st.rerun()
                        else:
                            pdf_link_button(e["pdf_path_original"])
                st.divider()

            # ---- Cevap anahtarı penceresi ----
            if st.session_state.get("_anahtar_goster"):
                _hedef = next(
                    (x for x in all_exams if x["id"] == st.session_state["_anahtar_goster"]), None)
                if _hedef is None:
                    st.session_state.pop("_anahtar_goster", None)
                else:
                    def _anahtar_govde(_e=_hedef):
                        st.markdown(f"**{_e['title']}**  ·  *{_e['category']}*")
                        _yapi = _e.get("structure") or {}
                        _anh = _e.get("answer_key") or {}
                        _hic = True
                        for _bol, _dersler in _anh.items():
                            for _ders, _liste in _dersler.items():
                                _hic = False
                                _meta = (_yapi.get(_bol) or {}).get(_ders) or {}
                                _nolar = _meta.get("numbers") or list(range(1, len(_liste) + 1))
                                st.markdown(f"##### {_ders} — {len(_liste)} soru")
                                _sat, _sut = [], 10
                                for _i in range(0, len(_liste), _sut):
                                    _sat.append({
                                        f"{_k + 1}": f"{_nolar[_i + _k]}. {_liste[_i + _k]}"
                                        for _k in range(min(_sut, len(_liste) - _i))
                                    })
                                _tablo(pd.DataFrame(_sat).fillna(""))
                        if _hic:
                            st.warning("Bu deneme için cevap anahtarı kaydedilmemiş.")
                        st.caption(
                            "Bu anahtar veritabanından okunuyor; kitapçık dosyası "
                            "kaybolsa bile burada durur. **Öğrenci bu ekranı göremez.**"
                        )
                    _pencere_ac("🔑 Cevap Anahtarı", _anahtar_govde,
                                temizlenecek=("_anahtar_goster",))

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
                    # YAZILDIĞI HÂLİ GÖSTER: Giriş anahtarı küçük harfli
                    # ("e.m.onur") ama yönetici "E.M.ONUR" yazdıysa ekranda
                    # öyle görünmeli. Çocuk hangi yazımla girerse girsin
                    # (büyük/küçük harf, İ/I/ı/i) aynı hesaba düşer.
                    _gorunen_kadi = s.get("username_yazim") or s["username"]
                    with st.expander(f"👤 {s['display_name']}  (kullanıcı adı: {_gorunen_kadi})"):
                        st.markdown("**Kullanıcı adını değiştir**")
                        st.caption(
                            "Yazdığınız gibi görünür. Girişte büyük/küçük harf "
                            "farkı aranmaz — çocuk hangi şekilde yazarsa yazsın "
                            "aynı hesaba girer."
                        )
                        new_username = st.text_input(
                            "Yeni kullanıcı adı", value=_gorunen_kadi,
                            key=f"newuser_{s['username']}"
                        )
                        if st.button("Kullanıcı Adını Güncelle", key=f"renamebtn_{s['username']}"):
                            # ÖNEMLİ - "DEĞİŞTİRDİM AMA KAYDEDİLDİĞİNE DAİR HİÇBİR
                            # ŞEY ÇIKMADI": Burada st.success() yazılıp hemen
                            # ardından st.rerun() çağrılıyordu. Ekran baştan
                            # çizildiği için o yeşil mesaj göz açıp kapayana
                            # kadar siliniyordu; kullanıcı işlemin olup
                            # olmadığını anlayamıyordu. Artık mesaj oturumda
                            # saklanıp yenilemeden SONRA gösteriliyor (panelin
                            # diğer bölümlerinde zaten böyle yapılıyordu).
                            _eski = s["username"]
                            ok, msg = db.rename_student(_eski, new_username)
                            if ok:
                                _yeni_ad = db.kullanici_adi_duzelt(new_username)
                                _yeni_yazim = (new_username or "").strip().replace(" ", "_")
                                # Değiştirilen öğrenci o an oturumda seçiliyse
                                # oturumu da yeni ada taşı, yoksa raporlar boş görünür.
                                if st.session_state.student_name == _eski:
                                    st.session_state.student_name = _yeni_ad
                                if _yeni_ad == _eski and _yeni_yazim == _gorunen_kadi:
                                    st.session_state["_admin_flash"] = (
                                        "success",
                                        f"ℹ️ Kullanıcı adı zaten **{_gorunen_kadi}** idi; "
                                        f"değişiklik yapılmadı.",
                                    )
                                elif _yeni_ad == _eski:
                                    # Sadece BÜYÜK/küçük harf yazımı değişti.
                                    st.session_state["_admin_flash"] = (
                                        "success",
                                        f"✅ Kullanıcı adı artık **{_yeni_yazim}** diye "
                                        f"görünecek ({s['display_name']}). Giriş yine "
                                        f"aynı hesaba yapılır; büyük/küçük harf farkı "
                                        f"aranmaz.",
                                    )
                                else:
                                    st.session_state["_admin_flash"] = (
                                        "success",
                                        f"✅ Kullanıcı adı güncellendi: **{_gorunen_kadi} → "
                                        f"{_yeni_yazim}** ({s['display_name']}). Geçmiş "
                                        f"sonuçları ve yarım kalan sınavı da yeni ada "
                                        f"taşındı.",
                                    )
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
                    _adm_gruplar = _gunluk_gruplar(results)
                    # Ortalama/en yüksek net SADECE tam sınavlardan hesaplanır.
                    # Düzeltme turunun neti birkaç soruluktur; ortalamaya
                    # katılınca gerçek başarıyı olduğundan düşük gösteriyordu
                    # (ekranda "Ortalama Net 34.0" görünmesinin sebebi buydu:
                    #  68 netlik sınav ile 0 netlik düzeltme turu toplanmıştı).
                    _tamlar = [r for r in results if r.get("mode") != "yanlis"]
                    _nets = [r["total_net"] for r in (_tamlar or results)
                             if r["total_net"] is not None]
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Çözülen Sınav (gün)", len(_adm_gruplar))
                    c2.metric("Ortalama Net", round(sum(_nets) / len(_nets), 2) if _nets else 0)
                    c3.metric("En Yüksek Net", max(_nets) if _nets else 0)
                    if len(_tamlar) != len(results):
                        st.caption(
                            f"Ortalama ve en yüksek net, **{len(_tamlar)} tam sınav** "
                            f"üzerinden hesaplandı; düzeltme turları "
                            f"({len(results) - len(_tamlar)} adet) katılmadı."
                        )

                    st.divider()
                    # ÖNEMLİ - KULLANICI İSTEĞİ: "Aynı tarihte çözülen aynı test
                    # tek satırda gösterilsin, içine girince o görünen ekran
                    # açılsın." Aynı sınavın tam çözümü ile düzeltme turu ayrı
                    # satırlarda duruyor, üstelik düzeltme turunun 0.0 neti
                    # asıl sonucun yanında yanıltıcı görünüyordu.
                    st.markdown("#### 📋 Sınav Geçmişi")
                    st.caption(
                        "Her satır bir sınavın bir günü. **🔍 Ayrıntı**'ya basınca "
                        "o günün bütün turları ders ders açılır."
                    )
                    with _kutu("lgs_kompakt", height=430, border=True):
                        for g in _adm_gruplar:
                            _t = g["toplam"]
                            _n = len(g["turlar"])
                            _tur = (
                                ("🎯 Düzeltme turu" if g["turlar"][0].get("mode") == "yanlis"
                                 else "📝 Tam sınav")
                                if _n == 1 else
                                f"🔁 {_n} tur" + (f" ({g['duzeltme']} düzeltme)"
                                                 if g["duzeltme"] else "")
                            )
                            st.markdown(
                                f"**{g['exam_title']}**  \n"
                                f"<span style='background:#1E3A8A;color:#fff;padding:2px 9px;"
                                f"border-radius:14px;font-size:0.82rem;font-weight:600;'>"
                                f"🗓️ {g['gun']}</span> "
                                f"<span style='color:#475569;font-size:0.85rem;'> · {_tur} · "
                                f"{g.get('category') or ''}</span>",
                                unsafe_allow_html=True,
                            )
                            st.markdown(
                                f"<div style='font-size:0.95rem;margin:2px 0 6px 0;'>"
                                f"<b>Net {g['net']}</b> &nbsp;·&nbsp; {_t['soru']} soru "
                                f"&nbsp;·&nbsp; ✅ {_t['dogru']} doğru &nbsp;·&nbsp; "
                                f"❌ {_t['yanlis']} yanlış &nbsp;·&nbsp; ⬜ {_t['bos']} boş</div>",
                                unsafe_allow_html=True,
                            )
                            # Ders kutucukları: günün ASIL (tam) sınavından.
                            _ps = g["esas"].get("per_subject") or {}
                            _bcols = st.columns((len(_ps) + 1) if _ps else 2)
                            for _bc, (_subj, _res) in zip(_bcols, _ps.items()):
                                if _bc.button(
                                    f"**{_subj}**\n\nNet {_res['net']}\n\n"
                                    f"✅{_res['dogru']} ❌{_res['yanlis']} ⬜{_res['bos']}",
                                    key=f"admdet_{g['id']}_{_subj}", use_container_width=True,
                                ):
                                    st.session_state["_adm_grup"] = g["id"]
                                    st.session_state["_adm_ders"] = _subj
                                    st.rerun()
                            if _bcols[-1].button("🔍 Ayrıntı", key=f"admall_{g['id']}",
                                                 use_container_width=True, type="primary"):
                                st.session_state["_adm_grup"] = g["id"]
                                st.session_state["_adm_ders"] = None
                                st.rerun()
                            st.markdown(
                                "<hr style='margin:6px 0 10px 0;border:none;"
                                "border-top:1px solid #E2E8F0;'>",
                                unsafe_allow_html=True,
                            )

                    # Döküm penceresi (yönetici zaten şifreyle girdi, PIN sorulmaz)
                    if st.session_state.get("_adm_grup"):
                        _hedef = next(
                            (x for x in _adm_gruplar
                             if x["id"] == st.session_state["_adm_grup"]), None)
                        if _hedef is not None:
                            _grup_penceresi(
                                _hedef, pin_gerekli=False,
                                vurgu_ders=st.session_state.get("_adm_ders"),
                                silme=True,
                                temizlenecek=("_adm_grup", "_adm_ders"),
                            )
                        else:
                            st.session_state.pop("_adm_grup", None)

        # ---------------- Hesap ayarları (admin kullanıcı adı / şifre) ----------------
        elif admin_section == "Hesap Ayarları":
            st.markdown("#### 🔐 Rapor PIN kodu")
            st.caption(
                "Öğrenci **Gelişim Raporum → 🔍 Sınav Detayı** düğmesine bastığında, soru "
                "bazlı döküm (hangi soruyu yanlış yaptı, doğrusu neydi) bu PIN kodu "
                "sorularak açılır. PIN bir kez girildikten sonra o oturum boyunca tekrar "
                "sorulmaz. **Boş bırakırsanız PIN sorulmaz**, döküm herkese açık olur."
            )
            _mevcut_pin = db.get_setting("rapor_pin", "") or ""
            # ÖNEMLİ - KULLANICI GERİ BİLDİRİMİ ("PIN girdim ama kaydedilip
            # kaydedilmediğini anlamadım"): Durum artık büyük ve net yazıyor,
            # ayrıca PIN'in kaç haneli olduğu ve ne zaman kaydedildiği görünüyor.
            if _mevcut_pin:
                st.success(
                    f"🔒 **PIN kodu ŞU AN AKTİF.** {len(_mevcut_pin)} haneli bir kod "
                    f"kayıtlı (güvenlik için kodun kendisi gösterilmez). Öğrenci "
                    f"**Gelişim Raporum → 🔍 Sınav Detayı** derken bu kod sorulacak.",
                    icon="✅",
                )
            else:
                st.warning(
                    "🔓 **PIN kodu YOK.** Şu an soru soru döküm (doğru cevaplar dâhil) "
                    "PIN sorulmadan açılıyor. Kod koymak için aşağıya yazıp "
                    "**PIN'i Kaydet**'e basın."
                )
            _yeni_pin = st.text_input(
                "Yeni PIN kodu", type="password", key="pin_yeni",
                help="Sadece rakam kullanmanız önerilir (ör. 4-6 haneli).",
            )
            _pc1, _pc2 = st.columns(2)
            if _pc1.button("PIN'i Kaydet", type="primary", key="pin_kaydet"):
                _t = _yeni_pin.strip()
                if _t and len(_t) < 4:
                    st.error("PIN en az 4 karakter olmalı.")
                else:
                    db.set_setting("rapor_pin", _t)
                    # Kaydın gerçekten oluştuğunu veritabanından OKUYARAK doğrula
                    _kontrol = db.get_setting("rapor_pin", "") or ""
                    if _t and _kontrol == _t:
                        st.session_state["_admin_flash"] = (
                            "success",
                            f"✅ **PIN kodu kaydedildi ve doğrulandı** ({len(_t)} haneli). "
                            f"Bundan sonra 'Sınav Detayı' bu kodla açılacak. "
                            f"Kodu unutmayın — burada bir daha gösterilmez.",
                        )
                        st.session_state["_pin_balon"] = True
                    elif not _t:
                        st.session_state["_admin_flash"] = (
                            "success", "✅ PIN kaldırıldı; döküm artık PIN sorulmadan açılacak.")
                    else:
                        st.session_state["_admin_flash"] = (
                            "error",
                            "❌ PIN kaydedilemedi (veritabanına yazıldıktan sonra geri "
                            "okunamadı). Lütfen tekrar deneyin.",
                        )
                    st.rerun()
            if _pc2.button("PIN'i Kaldır", key="pin_kaldir"):
                db.set_setting("rapor_pin", "")
                st.session_state["_admin_flash"] = ("success", "PIN kaldırıldı.")
                st.rerun()

            st.divider()
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
