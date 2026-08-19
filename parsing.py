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
    """Verilen PDF'in son sayfalarindan birinde cevap anahtarini arar
    (bazi kitapciklarda son sayfa bos/kapak olabilir).

    section_subjects: [("Türkçe", 20), ...] siralı liste.
    Donus: (answers_dict veya None, mesaj, cevap_anahtari_sayfa_indeksi veya None)
    """
    with pdfplumber.open(pdf_file_or_path) as pdf:
        n = len(pdf.pages)
        last_error = "PDF'te sayfa bulunamadı."
        for offset in range(search_last_n_pages):
            idx = n - 1 - offset
            if idx < 0:
                break
            result, msg = parse_answer_page(pdf.pages[idx], section_subjects)
            if result is not None:
                return result, "OK", idx
            last_error = msg
        return None, last_error, None


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
