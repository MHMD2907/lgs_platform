"""
parsing.py - PDF cevap anahtari cikarma + guvenli (cevapsiz) PDF uretme.

ONEMLI - gecmis surumdeki hata: MEB'in gercek LGS cevap anahtari sayfalari
("1. A 1. B 1. D 1. D" seklinde) dersleri YAN YANA SUTUNLAR halinde basar.
Eski kod tum sayfayi tek bir metin bloğu olarak regex'liyordu; bu da her
ders icin yanlislikla hep ILK sutunun (Turkce / Matematik) cevaplarinin
kopyalanmasina yol aciyordu -> yanlis puanlama.

Bu modul, kelimelerin sayfadaki (x0, top) koordinatlarini kullanarak
sutunlari birbirinden ayirir (Turkce/Inkilap/Din/Ingilizce veya
Matematik/Fen gibi). a_2026_sozel.pdf ve a_2026_sayisal.pdf (gercek MEB
LGS cevap anahtarlari) ile test edilip dogrulanmistir.
"""

import io
import os
import re
import shutil
import subprocess
import tempfile

import pdfplumber
from PyPDF2 import PdfReader, PdfWriter

try:
    from PIL import Image
except Exception:  # Pillow kurulu değilse sıkıştırma sessizce atlanır
    Image = None


def _compress_pdf_for_display(path, dpi=130, quality=62):
    """MEB'in resmi LGS kitapçıkları genelde 10-15 MB civarında oluyor
    (bazı sayfalardaki gradyan/gölgeli vektör çizimler yüzünden -- bu,
    normal PDF sıkıştırma araçlarının (Ghostscript'in görsel-küçültme
    ayarları dahil) pek işe yaramadığı, "resim değil vektör" bir sorun).
    En güvenilir çözüm: her sayfayı SABİT bir çözünürlükte resme çevirip
    (öğrenci zaten sadece OKUYACAK, metni seçip kopyalamayacak), o
    resimlerden yeni, çok daha küçük bir PDF oluşturmak. Bu dosyayı
    tarayıcıya göndermek (base64 ile gömülü olsa bile) çok daha hızlı
    ve güvenilir olur.

    Ghostscript (gs) sistemde kurulu değilse (ör. henüz packages.txt
    Streamlit Cloud'a yüklenmediyse) ya da herhangi bir adımda hata
    olursa, ORİJİNAL (sıkıştırılmamış) dosya olduğu gibi bırakılır --
    yani bu adım asla denemeyi bozmaz, sadece atlanır.
    """
    if Image is None or shutil.which("gs") is None:
        return False
    tmpdir = tempfile.mkdtemp(prefix="lgs_compress_")
    try:
        pattern = os.path.join(tmpdir, "p_%04d.jpg")
        result = subprocess.run(
            [
                "gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER",
                "-sDEVICE=jpeg", f"-r{dpi}", f"-dJPEGQ={quality}",
                f"-o{pattern}", path,
            ],
            capture_output=True, timeout=180,
        )
        if result.returncode != 0:
            return False
        page_files = sorted(
            f for f in os.listdir(tmpdir) if f.startswith("p_") and f.endswith(".jpg")
        )
        if not page_files:
            return False
        images = [Image.open(os.path.join(tmpdir, f)).convert("RGB") for f in page_files]
        out_path = os.path.join(tmpdir, "out.pdf")
        images[0].save(out_path, save_all=True, append_images=images[1:])
        for im in images:
            im.close()
        # Sadece gerçekten küçüldüyse orijinalin üzerine yaz (garanti altına al).
        if os.path.getsize(out_path) < os.path.getsize(path):
            shutil.copyfile(out_path, path)
        return True
    except Exception:
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def drop_blank_pages(path):
    """MEB kitapçıklarında (ör. 'Bu bölüm boş bırakılmıştır' arka yüzleri)
    tamamen BOŞ sayfalar bulunur; öğrenci sayfa çevirirken bunlara denk
    gelip 'PDF açılmadı' sanıyor. Bu fonksiyon o sayfaları dosyadan
    KALICI olarak siler.

    Yöntem: her sayfa çok düşük çözünürlükte (20 dpi) hızlıca resme çevrilip
    en koyu pikseline bakılır. Sayfa tamamen beyazsa bu değer 255 olur.
    Gerçek kitapçık üzerinde ölçüldü: boş sayfalar tam 255, en açık DOLU
    sayfa 89 -- yani aradaki fark çok büyük, yanlışlıkla dolu bir sayfayı
    silme riski yok (eşik 250 seçildi). Sayfadaki metni okumaya çalışan
    yönteme göre ~15 kat daha hızlı (0.9 sn / 33 sayfa) ve taranmış
    (resme çevrilmiş) sayfalarda da çalışır.

    Herhangi bir hata olursa dosyaya DOKUNULMAZ; yani bu adım asla
    denemeyi bozmaz, sadece atlanır.
    """
    try:
        import pypdfium2 as pdfium
    except Exception:
        return 0
    try:
        doc = pdfium.PdfDocument(path)
        total = len(doc)
        blank_idx = set()
        for i in range(total):
            gray = doc[i].render(scale=20 / 72).to_pil().convert("L")
            if gray.getextrema()[0] >= 250:
                blank_idx.add(i)
        doc.close()
        # Tüm sayfalar boşsa (beklenmez) dosyayı boşaltmayalım.
        if not blank_idx or len(blank_idx) >= total:
            return 0
        # Dosyayi tamamen bellege alip oyle okuyoruz: aksi halde PdfReader
        # dosyayi acik tutar ve (ozellikle Windows'ta) hemen ardindan ayni
        # dosyanin uzerine yazmak basarisiz olur.
        with open(path, "rb") as f_in:
            buf = io.BytesIO(f_in.read())
        reader = PdfReader(buf)
        writer = PdfWriter()
        for i, page in enumerate(reader.pages):
            if i not in blank_idx:
                writer.add_page(page)
        with open(path, "wb") as f_out:
            writer.write(f_out)
        return len(blank_idx)
    except Exception:
        return 0


def _cluster_columns(numtoks, gap=35):
    """x0'a gore siralanmis sayi token'larini soldan saga sutunlara ayirir."""
    numtoks = sorted(numtoks, key=lambda t: t["x0"])
    cols = [[numtoks[0]]]
    for t in numtoks[1:]:
        if t["x0"] - cols[-1][-1]["x0"] > gap:
            cols.append([t])
        else:
            cols[-1].append(t)
    return cols


def parse_answer_page(page, subjects):
    """Bir pdfplumber page nesnesinden, verilen (ders_adi, soru_sayisi)
    siralı listesine gore cevaplari sutun sutun cikartir.

    subjects: [("Türkçe", 20), ("İnkılap", 10), ...]  -- PDF'teki soldan
        saga sutun sirasiyla AYNI sirada olmali.

    Donus: (dict veya None, mesaj)
    """
    words = page.extract_words()
    numtoks, lettoks = [], []
    for w in words:
        txt = w["text"]
        m = re.match(r"^(\d{1,2})\.$", txt)
        if m:
            # "1.", "12." gibi salt soru numarasi token'lari
            numtoks.append({"x0": w["x0"], "top": w["top"], "num": int(m.group(1))})
        elif re.match(r"^[A-D]$", txt):
            lettoks.append({"x0": w["x0"], "top": w["top"], "letter": txt})

    if not numtoks:
        return None, "Sayfada soru numarası (1., 2., ...) bulunamadı. Bu PDF'in son sayfası cevap anahtarı olmayabilir."

    cols = _cluster_columns(numtoks)
    if len(cols) != len(subjects):
        return None, (
            f"{len(subjects)} ders sütunu bekleniyordu, sayfada {len(cols)} sütun bulundu. "
            "PDF formatı beklenenden farklı olabilir; manuel giriş yapabilirsiniz."
        )

    result = {}
    for col, (subj, count) in zip(cols, subjects):
        col_sorted = sorted(col, key=lambda t: t["top"])
        answers = []
        for nt in col_sorted:
            cand = [
                lt for lt in lettoks
                if abs(lt["top"] - nt["top"]) < 6
                and 0 < (lt["x0"] - nt["x0"]) < 40
            ]
            if not cand:
                answers.append(None)
            else:
                cand.sort(key=lambda lt: lt["x0"] - nt["x0"])
                answers.append(cand[0]["letter"])
        if len(answers) != count or any(a is None for a in answers):
            return None, (
                f"'{subj}' dersi için {count} cevap bekleniyordu, {len(answers)} bulundu "
                f"({[a or '?' for a in answers]}). Manuel giriş yapabilirsiniz."
            )
        result[subj] = answers
    return result, "OK"


def extract_answer_key(pdf_file_or_path, section_subjects, search_last_n_pages=2):
    """Verilen PDF'te cevap anahtarini arar.

    ONEMLI - IKI ASAMALI ARAMA: Once ESKI kati okuyucu denenir (son
    sayfada, her ders bir sutun). O tutmazsa, duzenden bagimsiz calisan
    ESNEK okuyucu devreye girer (bkz. cevap_anahtari_bul). Bursluluk
    (IOKBS) kitapciklari eski okuyucuya uymadigi icin hicbiri
    eklenemiyordu; artik ikisi birlikte deneniyor.

    section_subjects: [("Türkçe", 20), ...] siralı liste.
    Donus: (answers_dict veya None, mesaj, cevap_anahtari_sayfa_indeksi veya None)
    """
    son_hata = "PDF'te sayfa bulunamadı."
    try:
        with pdfplumber.open(pdf_file_or_path) as pdf:
            n = len(pdf.pages)
            for offset in range(search_last_n_pages):
                idx = n - 1 - offset
                if idx < 0:
                    break
                result, msg = parse_answer_page(pdf.pages[idx], section_subjects)
                if result is not None:
                    return result, "OK", idx
                son_hata = msg
    except Exception as e:
        son_hata = f"PDF okunamadı: {e}"

    # --- Esnek okuyucu: dosya nesnesi verilmisse gecici dosyaya yaz ---
    yol = pdf_file_or_path
    gecici = None
    if hasattr(pdf_file_or_path, "read"):
        try:
            pdf_file_or_path.seek(0)
            gecici = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            gecici.write(pdf_file_or_path.read())
            gecici.close()
            yol = gecici.name
            pdf_file_or_path.seek(0)
        except Exception:
            return None, son_hata, None
    try:
        sonuc, mesaj, idx = cevap_anahtari_bul(yol, section_subjects)
        if sonuc is not None:
            return sonuc, "OK", idx
        return None, f"{son_hata} (Esnek okuma da denendi: {mesaj})", idx
    except Exception as e:
        return None, f"{son_hata} (Esnek okuma hatası: {e})", None
    finally:
        if gecici:
            try:
                os.unlink(gecici.name)
            except Exception:
                pass


def crop_and_merge(file_specs, output_path):
    """file_specs: [(dosya_yolu_veya_buffer, cevap_anahtari_sayfa_indeksi), ...]
    Her dosyadan cevap anahtarı sayfası dahil, o sayfadan itibaren tüm sayfaları
    keser (bazı kitapçıklarda cevap anahtarı 1'den fazla sayfa olabilir),
    kalanları tek bir PDF'te birleştirir. Öğrenciye SADECE bu temiz PDF gösterilir.
    """
    writer = PdfWriter()
    for src, key_page_idx in file_specs:
        if hasattr(src, "seek"):
            src.seek(0)
        reader = PdfReader(src)
        last_page_to_keep = key_page_idx if key_page_idx is not None else len(reader.pages) - 1
        for i in range(min(last_page_to_keep, len(reader.pages))):
            writer.add_page(reader.pages[i])
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f_out:
        writer.write(f_out)
    # Öğrenciye gösterilen kitapçıktan tamamen boş sayfaları at (sayfa
    # çevirirken boş ekrana denk gelmesin). Sadece bu "temiz" sürümde
    # yapılır; admin'in gördüğü orijinal dosyaya dokunulmaz.
    drop_blank_pages(output_path)
    # MEB kitapçıkları bazen tek başına 10-15 MB olabiliyor; öğrenci
    # tarayıcıda hızlı ve güvenilir görebilsin diye dosyayı küçültmeyi
    # dene (Ghostscript yoksa/hata olursa orijinal dosya öylece kalır).
    _compress_pdf_for_display(output_path)
    return output_path


def merge_full(file_specs, output_path):
    """file_specs: [dosya_yolu_veya_buffer, ...] -- HİÇBİR SAYFAYI KIRPMADAN
    dosyaları tek bir PDF'te birleştirir (cevap anahtarı sayfaları dahil).
    Bu, sadece ADMIN'in daha sonra orijinali görüntüleyebilmesi için kullanılır;
    öğrenciye gösterilen PDF her zaman crop_and_merge() ile üretilen temiz
    (kırpılmış) sürümdür."""
    writer = PdfWriter()
    for src in file_specs:
        if hasattr(src, "seek"):
            src.seek(0)
        reader = PdfReader(src)
        for page in reader.pages:
            writer.add_page(page)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f_out:
        writer.write(f_out)
    _compress_pdf_for_display(output_path)
    return output_path


def pdf_page_count(file_obj_or_path):
    if hasattr(file_obj_or_path, "seek"):
        file_obj_or_path.seek(0)
    return len(PdfReader(file_obj_or_path).pages)


def gorsel_kucult(yol, dpi=110, kalite=58, sinir=None):
    """Bir PDF'i, her sayfasini RESME cevirerek kucultur.

    NEDEN GEREKLI: Denemelerin PDF'i artik veritabaninda da saklaniyor
    (Streamlit bulut sunucusu diski her yeniden baslatmada sildigi icin).
    Veritabaninda saklanabilecek boyut sinirli oldugundan, cok buyuk
    dosyalari once kucultuyoruz. Uygulama PDF'i zaten SAYFA SAYFA RESIM
    olarak gosterdigi icin gorsel kalite farki ekranda fark edilmez.

    Olculdu: 20 sayfalik bir bolum 4,7 MB -> 1,8 MB.

    sinir verilirse (bayt), dosya zaten kucukse hic dokunulmaz.
    Basarisiz olursa dosya oldugu gibi kalir (hic hata firlatmaz)."""
    try:
        if not yol or not os.path.exists(yol):
            return False
        if sinir and os.path.getsize(yol) <= sinir:
            return False
        import pypdfium2 as pdfium
        from PIL import Image  # noqa: F401

        doc = pdfium.PdfDocument(yol)
        try:
            sayfalar = []
            for i in range(len(doc)):
                sayfalar.append(doc[i].render(scale=dpi / 72).to_pil().convert("RGB"))
        finally:
            doc.close()
        if not sayfalar:
            return False
        buf = io.BytesIO()
        sayfalar[0].save(
            buf, "PDF", save_all=True, append_images=sayfalar[1:],
            resolution=dpi, quality=kalite,
        )
        veri = buf.getvalue()
        if veri and len(veri) < os.path.getsize(yol):
            with open(yol, "wb") as f:
                f.write(veri)
            return True
    except Exception:
        pass
    return False


# =====================================================================
#  ESNEK CEVAP ANAHTARI OKUYUCU
# =====================================================================
# ONEMLI - NEDEN GEREKLI: Onceki okuyucu TEK bir duzeni taniyordu:
# "son sayfada, her ders icin bir sutun". Bursluluk (IOKBS) kitapciklarinda
# duzen farkli oldugu icin "4 ders sutunu bekleniyordu, 1 sutun bulundu"
# diyip hicbir kitapcigi ekleyemiyordu.
#
# Bu okuyucu duzene degil, SAYILARA bakar:
#   1. Cevap anahtari sayfalarini bulur (metninde "CEVAP ANAHTARI" gecen
#      ya da bol miktarda "12. C" ikilisi bulunan sayfalar).
#   2. Sayfadaki tum (soru_no, harf) ikililerini KOORDINATLARIYLA toplar.
#   3. Sutun sutun mu satir satir mi dizildigini, elde edilen sayi dizisinin
#      duzgunlugune bakarak kendi anlar.
#   4. Ders bloklarini iki sekilde ayirir:
#        a) numaralandirma her derste 1'den basliyorsa -> her 1'de yeni blok
#        b) 1'den toplam soru sayisina kadar kesintisiz gidiyorsa -> ders
#           soru sayilarina gore sirayla boler
# Boylece hem eski LGS kitapciklari hem bursluluk kitapciklari okunur.

_IKILI = re.compile(r"^(\d{1,3})[\.\)]?$")


def _sayfa_ikilileri(page):
    """Sayfadaki (soru_no, harf) ikililerini koordinatlariyla dondurur."""
    words = page.extract_words()
    numtoks, lettoks = [], []
    for w in words:
        txt = (w["text"] or "").strip()
        m = _IKILI.match(txt)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 200:
                numtoks.append({"x0": w["x0"], "x1": w["x1"], "top": w["top"], "num": n})
            continue
        if re.fullmatch(r"[A-Da-d]", txt):
            lettoks.append({"x0": w["x0"], "top": w["top"], "letter": txt.upper()})
            continue
        # "12.C" gibi bitisik yazimlar
        m2 = re.fullmatch(r"(\d{1,3})[\.\)\-]\s*([A-Da-d])", txt)
        if m2:
            n = int(m2.group(1))
            if 1 <= n <= 200:
                numtoks.append({"x0": w["x0"], "x1": w["x1"], "top": w["top"], "num": n})
                lettoks.append({"x0": w["x1"], "top": w["top"], "letter": m2.group(2).upper()})
    ikililer = []
    for nt in numtoks:
        aday = [
            lt for lt in lettoks
            if abs(lt["top"] - nt["top"]) < 7 and -2 < (lt["x0"] - nt["x1"]) < 45
        ]
        if not aday:
            continue
        aday.sort(key=lambda lt: abs(lt["x0"] - nt["x1"]))
        ikililer.append({"num": nt["num"], "harf": aday[0]["letter"],
                         "x": nt["x0"], "top": nt["top"]})
    return ikililer


def _duzgunluk(diziler):
    """Bir sayi dizisinin ne kadar 'duzgun sirali' oldugunu olcer."""
    if not diziler:
        return -1
    artan = sum(1 for a, b in zip(diziler, diziler[1:]) if b == a + 1 or b == 1)
    return artan / max(1, len(diziler) - 1)


def _okuma_sirasi(ikililer):
    """Ikilileri gorsel okuma sirasina dizer.

    Sayfa hem SUTUN SUTUN (yukaridan asagi, sonra saga) hem de SATIR SATIR
    (soldan saga, sonra asagi) dizilmis olabilir. Ikisini de deneyip sayi
    dizisi hangisinde daha duzgun cikiyorsa onu kullaniyoruz."""
    if not ikililer:
        return []

    # a) Sutun sutun
    sutunlu = sorted(ikililer, key=lambda t: t["x"])
    sutunlar, esik = [[sutunlu[0]]], 30
    for t in sutunlu[1:]:
        if t["x"] - sutunlar[-1][-1]["x"] > esik:
            sutunlar.append([t])
        else:
            sutunlar[-1].append(t)
    a_sira = [t for s in sutunlar for t in sorted(s, key=lambda z: z["top"])]

    # b) Satir satir
    b_sira = sorted(ikililer, key=lambda t: (round(t["top"] / 6), t["x"]))

    return a_sira if _duzgunluk([t["num"] for t in a_sira]) >= \
        _duzgunluk([t["num"] for t in b_sira]) else b_sira


def _bloklara_ayir(sirali, subjects):
    """Okuma sirasindaki ikilileri ders bloklarina ayirir."""
    sayilar = [t["num"] for t in sirali]
    harfler = [t["harf"] for t in sirali]
    adetler = [c for _s, c in subjects]
    toplam = sum(adetler)

    # a) Her derste numaralandirma 1'den basliyor mu?
    bloklar, su = [], []
    for i, n in enumerate(sayilar):
        if n == 1 and su:
            bloklar.append(su)
            su = []
        su.append(i)
    if su:
        bloklar.append(su)
    if len(bloklar) == len(subjects) and \
            all(len(b) == c for b, c in zip(bloklar, adetler)):
        return {s: [harfler[i] for i in b] for (s, _c), b in zip(subjects, bloklar)}

    # b) 1'den toplam soru sayisina kadar kesintisiz mi?
    if len(sayilar) == toplam and sayilar == list(range(1, toplam + 1)):
        out, bas = {}, 0
        for s, c in subjects:
            out[s] = harfler[bas:bas + c]
            bas += c
        return out

    # c) Sadece adet tutuyorsa sirayla bol (son care)
    if len(harfler) == toplam:
        out, bas = {}, 0
        for s, c in subjects:
            out[s] = harfler[bas:bas + c]
            bas += c
        return out
    return None


def cevap_anahtari_bul(pdf_yolu, subjects, tara=14):
    """PDF'te cevap anahtarini esnek bicimde arar.

    Donus: (cevaplar_dict veya None, mesaj, ilk_anahtar_sayfa_indeksi veya None)"""
    import pypdfium2 as pdfium

    adaylar = []
    try:
        doc = pdfium.PdfDocument(pdf_yolu)
        try:
            n = len(doc)
            for i in range(max(0, n - tara), n):
                tp = doc[i].get_textpage()
                try:
                    metin = (tp.get_text_range() or "").upper()
                finally:
                    tp.close()
                sade = re.sub(r"[^A-Z]", "", metin.replace("İ", "I").replace("Ç", "C")
                              .replace("Ş", "S").replace("Ğ", "G").replace("Ü", "U")
                              .replace("Ö", "O"))
                if "CEVAPANAHTARI" in sade or len(re.findall(r"\d{1,3}\s*[\.\)]\s*[A-D]\b", metin)) >= 15:
                    adaylar.append(i)
        finally:
            doc.close()
    except Exception:
        adaylar = []
    if not adaylar:
        try:
            with pdfplumber.open(pdf_yolu) as pdf:
                n = len(pdf.pages)
            adaylar = list(range(max(0, n - 4), n))
        except Exception:
            return None, "PDF açılamadı.", None

    toplam_soru = sum(c for _s, c in subjects)
    with pdfplumber.open(pdf_yolu) as pdf:
        # Anahtar birden fazla sayfaya yayilmis olabilir: ardisik aday
        # sayfalari birlestirerek dene.
        for bas in range(len(adaylar)):
            birikmis, ilk = [], adaylar[bas]
            for k in range(bas, min(bas + 4, len(adaylar))):
                idx = adaylar[k]
                if idx >= len(pdf.pages):
                    continue
                birikmis += _okuma_sirasi(_sayfa_ikilileri(pdf.pages[idx]))
                if len(birikmis) < toplam_soru:
                    continue
                sonuc = _bloklara_ayir(birikmis, subjects)
                if sonuc and all(len(v) == c for (s, c), v in zip(subjects, [sonuc[s] for s, _ in subjects])):
                    return sonuc, "OK", ilk
    return None, (
        f"Cevap anahtarı sayfası bulundu ama {toplam_soru} cevap "
        f"eşleştirilemedi. PDF'in düzeni beklenenden farklı."
    ), (adaylar[0] if adaylar else None)
