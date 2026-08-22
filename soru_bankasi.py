"""
soru_bankasi.py - Cok dersli SORU BANKASI PDF'lerini TEST BAZINDA parcalara ayirir.

Amac: "8. Sinif Tum Dersler Soru Bankasi" gibi tek bir buyuk PDF'i, icindeki
her test ayri bir "deneme" olacak sekilde sisteme aktarabilmek. Boylece
ogrenci 50 soruluk koca bir LGS denemesi yerine "Turkce - Sozcukte Anlam
Test 3" gibi 6-8 soruluk kisa testler cozebilir.

Kitabin yapisi (gercek dosya uzerinde olculdu, IQ Yayinlari 8. Sinif):

  * Her dersin basinda SADECE ders adinin yazdigi bir ayrac sayfasi vardir
    ("8. SINIF TÜRKÇE IQ YAYINLARI" gibi, cok az metin icerir).
  * Her test sayfasinin sag ust kosesinde DIKEY olarak "T E S T" yazar
    (x ~ 447-465, sayfanin en ustu) ve hemen yaninda test numarasi bulunur
    (x >= 470). Iki basamakli numaralar iki ayri karakter olarak gelir
    ("1" ve "0" -> 10), bu yuzden yukaridan asagiya birlestirilir.
  * Konu adi ayni satirda, ortada yazar ("Sözcükte Anlam" gibi).
  * Sorular iki sutun halinde dizilir; soru numaralari sol sutunda x ~ 45,
    sag sutunda x ~ 299 civarinda "4." bicimindedir.
  * Kitabin sonunda "CEVAP ANAHTARI" sayfalari vardir; tek sutundur ve
    "TEST - 1 1-B 2-C 3-D ..." bicimindedir. Ders basliklari (TÜRKÇE,
    MATEMATİK, ...) araya girer.

ONEMLI: Cevap anahtari sayfalarindaki satirlar, PDF'in ic metin siralamasina
gore okundugunda KARISIK gelir (once 11. test, sonra 7., sonra 1. ...). Bu
yuzden satirlar mutlaka kelimelerin (x, top) KOORDINATLARINA gore yeniden
kurulur; yoksa cevaplar yanlis testlere atanir.
"""

import re

import pdfplumber

# Dosya surumu -- app.py bunu okuyup "hepsi ayni surumde mi" diye bakar.
SURUM = "2026-08-22.5"

DERS_ADLARI = [
    "Türkçe",
    "Matematik",
    "Fen Bilimleri",
    "T.C. İnkılap Tarihi ve Atatürkçülük",
    "Din Kültürü ve Ahlak Bilgisi",
    "İngilizce",
    "Sosyal Bilgiler",
]

# Cevap anahtarindaki BUYUK HARFLI ders basliklari -> normal ders adi
_KEY_BASLIKLARI = {
    "TÜRKÇE": "Türkçe",
    "MATEMATİK": "Matematik",
    "FEN BİLİMLERİ": "Fen Bilimleri",
    "T.C. İNKILAP TARİHİ VE ATATÜRKÇÜLÜK": "T.C. İnkılap Tarihi ve Atatürkçülük",
    "DİN KÜLTÜRÜ VE AHLAK BİLGİSİ": "Din Kültürü ve Ahlak Bilgisi",
    "İNGİLİZCE": "İngilizce",
}


def _satirlar(page, x_min=None, x_max=None):
    """Sayfadaki kelimeleri GERCEK gorsel siraya gore satirlara dizer
    (once yukaridan asagiya, sonra soldan saga). PDF'in kendi metin
    siralamasina guvenilmez -- bkz. modul basindaki not."""
    words = page.extract_words()
    if x_min is not None:
        words = [w for w in words if x_min <= w["x0"] <= (x_max if x_max is not None else 1e9)]
    gruplar = {}
    for w in words:
        gruplar.setdefault(round(w["top"] / 3), []).append(w)
    out = []
    for anahtar in sorted(gruplar):
        ws = sorted(gruplar[anahtar], key=lambda w: w["x0"])
        out.append(" ".join(w["text"] for w in ws))
    return out


def _ust_serit(pdfium_page, yukseklik=90):
    """Sayfanin en ust seridindeki metni dondurur.

    HIZ NOTU: Bu is once pdfplumber ile yapiliyordu ve 121 sayfalik bir
    kitapta 114 SANIYE suruyordu (pdfplumber, sadece ust serit istense bile
    sayfadaki tum nesneleri ayristiriyor). pdfium'un bolgeli metin okumasi
    ayni isi 5,5 saniyede yapiyor -- yaklasik 20 kat hizli."""
    h = pdfium_page.get_height()
    w = pdfium_page.get_width()
    tp = pdfium_page.get_textpage()
    try:
        return tp.get_text_bounded(left=0, bottom=h - yukseklik, right=w, top=h) or ""
    finally:
        tp.close()


def _serit_coz(serit):
    """Ust seritten (test_no, konu) cikarir; test sayfasi degilse (None, None).

    Kitapta ust serit su bicimlerden birinde geliyor (hepsi gercek dosyada
    goruldu):
        "Sözcükte Anlam\\nTEST\\n1"      "Genel Tekrar Testi\\nTEST 27"
        "TEST\\nNoktalama İşaretleri 26"
    Ortak kural: serit "TEST" iceriyorsa test sayfasidir ve test numarasi
    serideki SON sayidir."""
    if not serit or "TEST" not in serit.upper():
        return None, None
    duz = re.sub(r"\s+", " ", serit).strip()
    if duz.upper().startswith("CEVAP ANAHTARI"):
        return None, None
    sayilar = re.findall(r"\d+", duz)
    if not sayilar:
        return None, None
    test_no = int(sayilar[-1])
    konu = re.sub(r"\bTEST\b", " ", duz, flags=re.IGNORECASE)
    konu = re.sub(r"\s*" + re.escape(sayilar[-1]) + r"\s*$", "", konu.strip())
    konu = re.sub(r"\s+", " ", konu).strip(" -–—")
    return test_no, (konu or "Test")


# ONEMLI: Python'da "Matematik".upper() -> "MATEMATIK" (noktasiz I) verir,
# ama PDF'te "MATEMATİK" (noktali İ) yazar; bu yuzden duz upper() ile
# karsilastirma sessizce basarisiz oluyordu. Once tum Turkce harfleri
# ASCII karsiliklarina cevirip oyle karsilastiriyoruz.
_TR_KATLA = str.maketrans(
    "ÇçĞğİıÖöŞşÜüÂâÎîÛû",
    "CcGgIiOoSsUuAaIiUu",
)


def _sadece_harfler(s):
    return re.sub(r"[^0-9A-Za-z]", "", (s or "").translate(_TR_KATLA)).upper()


def _altdizi_mi(kucuk, buyuk):
    """kucuk'un harfleri, buyuk'un icinde SIRAYLA geciyor mu?"""
    it = iter(buyuk)
    return all(ch in it for ch in kucuk)


def _ayrac_dersi(duz_metin):
    """Ders ayrac sayfasindaki ders adini bulur.

    ONEMLI: Bu sayfalarda kenarda dikey duran "IQ YAYINLARI" susu, ders
    adinin HARFLERININ ARASINA karisiyor ("MATEMATİK" -> "MIATEMATİK",
    "İNGİLİZCE" -> "İINGİLİZCE", "FEN BİLİMLERİ" -> "FEN Q I BİLİMLERİ").
    Bu yuzden duz karsilastirma calismaz; ders adinin harflerinin sirayla
    gecip gecmedigine bakilir."""
    hedef = _sadece_harfler(duz_metin)
    for d in DERS_ADLARI:
        if _altdizi_mi(_sadece_harfler(d), hedef):
            return d
    return None


def _soru_numaralari(page):
    """Sayfadaki soru numaralarini (iki sutunu da tarayarak) bulur."""
    ws = page.extract_words()
    nums = set()
    for w in ws:
        m = re.fullmatch(r"(\d{1,2})\.", w["text"])
        if not m:
            continue
        # sadece sutun basi konumlarindakiler (soldaki ve ortadaki sutun)
        if w["x0"] <= 60 or 285 <= w["x0"] <= 320:
            n = int(m.group(1))
            if 1 <= n <= 25:
                nums.add(n)
    return sorted(nums)


def cevap_anahtarini_oku(pdf):
    """Kitabin sonundaki 'CEVAP ANAHTARI' sayfalarini okur.
    Donus: {ders: {test_no: {soru_no: harf}}}"""
    key = {}
    aktif_ders = None
    for idx in range(len(pdf.pages)):
        page = pdf.pages[idx]
        # Sadece son sayfalardaki gercek anahtari al (basta "İÇİNDEKİLER"
        # sayfasinda da bu kelime gecebiliyor).
        if idx < len(pdf.pages) - 8:
            continue
        satirlar = _satirlar(page)
        if not any("CEVAP ANAHTARI" in s.upper() for s in satirlar):
            continue
        for satir in satirlar:
            L = re.sub(r"\s+", " ", satir).strip()
            if not L or L.upper().startswith("CEVAP ANAHTARI"):
                continue
            up = _sadece_harfler(L)
            bulunan = [
                v for k, v in _KEY_BASLIKLARI.items() if up.startswith(_sadece_harfler(k))
            ]
            if bulunan:
                aktif_ders = bulunan[0]
                continue
            m = re.match(r"TEST\s*-\s*(\d+)\s+(.*)$", L)
            if m and aktif_ders:
                cevaplar = {
                    int(a): b for a, b in re.findall(r"(\d{1,2})\s*-\s*([A-E])", m.group(2))
                }
                if cevaplar:
                    key.setdefault(aktif_ders, {})[int(m.group(1))] = cevaplar
    return key


def _iq_testlerini_bul(pdf_path):
    """IQ Yayinlari duzenindeki soru bankasi PDF'ini tarar.

    Donus: (testler, cevap_anahtari, uyarilar)
      testler: [{"ders","test_no","konu","sayfalar":[1-tabanli],"sorular":[...]}]
    """
    import pypdfium2 as pdfium

    testler = []
    uyarilar = []

    # 1) Cevap anahtari: KOORDINAT bazli okunmali (bkz. modul basindaki not),
    #    bu yuzden sadece o birkac sayfa icin pdfplumber kullaniliyor.
    with pdfplumber.open(pdf_path) as pdf:
        anahtar = cevap_anahtarini_oku(pdf)

    # 2) Test sayfalarini bulma: hizli olmasi icin tamamen pdfium ile.
    doc = pdfium.PdfDocument(pdf_path)
    try:
        aktif_ders = None
        for i in range(len(doc)):
            page = doc[i]
            test_no, konu = _serit_coz(_ust_serit(page))
            if test_no is None:
                # Test sayfasi degil -- ders ayraci olabilir. Ayrac sayfalari
                # cok az metin icerir; tam metni okumak sadece bu sayfalar
                # icin gerekiyor.
                tp = page.get_textpage()
                try:
                    duz = re.sub(r"\s+", " ", tp.get_text_range() or "").strip()
                finally:
                    tp.close()
                if len(duz) < 140:
                    d = _ayrac_dersi(duz)
                    if d:
                        aktif_ders = d
                continue
            if aktif_ders is None:
                continue
            # Ayni testin birden fazla sayfasi varsa (tam kitaplarda test
            # genelde 2 sayfadir) onceki kayda ekle.
            if testler and testler[-1]["ders"] == aktif_ders and testler[-1]["test_no"] == test_no:
                testler[-1]["sayfalar"].append(i + 1)
            else:
                testler.append({
                    "ders": aktif_ders,
                    "test_no": test_no,
                    "konu": konu,
                    "sayfalar": [i + 1],
                    "sorular": [],
                    "tur": "test",
                })
    finally:
        doc.close()
    # Cevap anahtarini testlere bagla + sayfada gercekten basili olan soru
    # numaralarini bul (kitabin ilk sayfasi eksikse test 4. sorudan baslar).
    for t in testler:
        t["cevaplar"] = anahtar.get(t["ders"], {}).get(t["test_no"])
        if t["cevaplar"]:
            t["numaralar"] = gorunen_sorular(pdf_path, t["sayfalar"], list(t["cevaplar"].keys()))
        else:
            t["numaralar"] = []
        # Ogrencinin gercekten cozebilecegi soru sayisi = sayfada basili olan
        t["soru_sayisi"] = len(t["numaralar"])
        t["anahtar_soru_sayisi"] = len(t["cevaplar"]) if t["cevaplar"] else 0
    eksik = [t for t in testler if not t["cevaplar"]]
    if eksik:
        dersler = sorted({t["ders"] for t in eksik})
        uyarilar.append(
            f"{len(eksik)} testin cevap anahtarı bu PDF'te bulunamadı "
            f"({', '.join(dersler)}). Bu testler eklenemez."
        )
    return testler, anahtar, uyarilar


def gorunen_sorular(kaynak_pdf, sayfalar, anahtar_numaralari):
    """Bir testin sayfalarinda GERCEKTEN basili olan soru numaralarini bulur.

    NEDEN GEREKLI: Elimizdeki soru bankasi bir TANITIM surumu; her testin
    sadece son sayfasi var. Ornegin Turkce Test-1'in cevap anahtarinda 7 soru
    var ama sayfada sadece 4, 5, 6, 7. sorular basili. Bunu bilmezsek optik
    formu 1'den baslatiriz; cocuk PDF'te 4. soruyu okurken formda 1. soruyu
    isaretler ve TUM cevaplar kayar (hepsi yanlis sayilir). Bu fonksiyon
    sayfadaki gercek numaralari bulup formun onlarla ayni olmasini saglar.

    YONTEM: Soru numaralari sayfanin iki sutununun SOL KENARINDA (x ~ 45 ve
    x ~ 299) "4." bicimindedir; govde metni ise x ~ 65'ten baslar. Bu yuzden
    sadece o iki dar seride bakilir. Ust serit (baslik/test numarasi) haric
    tutulur, yoksa test numarasi soru sanilir.

    Donus: kitaptaki gercek soru numaralari listesi (ornegin [4, 5, 6, 7]).
    Emin olunamazsa bos liste doner (cagiran taraf testi atlar)."""
    import pypdfium2 as pdfium

    anahtar_set = set(int(n) for n in anahtar_numaralari)
    if not anahtar_set:
        return []
    bulunan = set()
    doc = pdfium.PdfDocument(kaynak_pdf)
    try:
        for s in sayfalar:
            if not (1 <= s <= len(doc)):
                continue
            page = doc[s - 1]
            h = page.get_height()
            tp = page.get_textpage()
            try:
                metin = ""
                for sol, sag in ((36, 66), (290, 320)):
                    metin += " " + (
                        tp.get_text_bounded(left=sol, bottom=0, right=sag, top=h - 95) or ""
                    )
            finally:
                tp.close()
            for m in re.findall(r"(?<![\d,.])(\d{1,2})\.(?!\d)", metin):
                n = int(m)
                if 1 <= n <= 25:
                    bulunan.add(n)
    finally:
        doc.close()

    ortak = bulunan & anahtar_set
    if not ortak:
        return []
    ust = max(anahtar_set)
    if ust not in ortak:
        # Beklenmedik durum: son soru sayfada gorunmuyor. Yanlis hizalama
        # riskine girmemek icin sadece kesin bulunanlari kullan.
        return sorted(ortak)
    # Son sorudan geriye dogru KESINTISIZ giden araligi al; ortadaki bir
    # numara okunamadiysa daha kisa bir aralik doner -- eksik soru sormak,
    # yanlis hizalanmis soru sormaktan iyidir.
    alt = ust
    while (alt - 1) in ortak:
        alt -= 1
    return list(range(alt, ust + 1))


def test_pdf_olustur(kaynak_pdf, sayfalar, hedef_yol):
    """Secilen sayfalardan tek bir kucuk PDF uretir (1 tabanli sayfa no)."""
    import os

    from PyPDF2 import PdfReader, PdfWriter

    reader = PdfReader(kaynak_pdf)
    writer = PdfWriter()
    for s in sayfalar:
        if 1 <= s <= len(reader.pages):
            writer.add_page(reader.pages[s - 1])
    os.makedirs(os.path.dirname(hedef_yol), exist_ok=True)
    with open(hedef_yol, "wb") as f:
        writer.write(f)
    return hedef_yol


# =====================================================================
#  İKİNCİ BİÇİM: "ÜNİTE / TEMA" KİTAPLARI  (MEB LGS Çalışma Kitabı vb.)
# =====================================================================
# Yukarıdaki okuyucu tek bir yayınevinin (IQ) düzenine göre yazılmıştı:
# sayfanın sağ üstünde dikey "TEST" yazısı, sonda "TEST - 1  1-B 2-C..."
# biçiminde cevap anahtarı. Kullanıcının eklediği MEB çalışma kitapları
# (lgs_fen.pdf, lgs_turkce.pdf, lgs_sozel.pdf, lgs_matematik.pdf) ise
# TAMAMEN farklı: testler yok, "1. Ünite" / "1. Tema" bölümleri var,
# sorular ünite içinde 1'den başlayıp kesintisiz devam ediyor ve cevap
# anahtarı şu düzende:
#
#     FEN BİLİMLERİ
#     CEVAP ANAHTARI
#     1. Ünite
#     1. C   2. A   3. A   4. C  ...
#     2. Ünite
#     1. B   2. D  ...
#
# Bu yüzden eski okuyucu bu kitaplarda "5 test buldu, 0'ı eklenebilir"
# diyordu: aradığı kalıpların hiçbiri yoktu.
#
# Bu bölüm ünite/tema düzenini okur. Ayrıca kitabın sonundaki
# "Merkezî Sınav Soruları" (geçmiş yıl LGS soruları) bölümünü BİLEREK
# ATLAR -- kullanıcı sadece ünite sorularını istiyor, geçmiş yıl
# sınavları zaten "Otomatik İndirme" bölümünden ekleniyor.

# "1. Ünite", "3. Tema", "2. ÜNİTE" ... başlıklarını yakalar.
_UNITE_BASLIK = re.compile(
    r"(?<!\d)(\d{1,2})\s*[\.\-]?\s*(ÜNİTE|Ünite|ünite|TEMA|Tema|tema|BÖLÜM|Bölüm)(?![A-Za-zÇĞİÖŞÜçğıöşü])"
)
# "12. C" biçimindeki tek cevap
_CEVAP = re.compile(r"(?<!\d)(\d{1,3})\s*[\.\-\)]\s*([A-E])(?![A-Za-zÇĞİÖŞÜçğıöşü0-9])")
# NOT: Cevap anahtarı bir TABLO içindeyse hücreler bitişik gelebiliyor
# ("1. C2. A3. A"); bu kalıp o durumu da yakalar.
_CEVAP_BITISIK = re.compile(
    r"(?<!\d)(\d{1,3})\s*[\.\-\)]\s*([A-E])(?![A-Za-zÇĞİÖŞÜçğıöşü])"
)
# "Unit 1", "UNIT 3" -- İngilizce bölümlerinde ünite başlığı böyle yazılır
# (sayı, Türkçe'nin tersine, kelimeden SONRA gelir).
_UNITE_BASLIK_EN = re.compile(r"(?:UNIT|Unit|unit)\s*[:\.\-]?\s*(\d{1,2})(?!\d)")
# Kitabın sonundaki geçmiş yıl sınavı bölümünün kapak sayfası
_MERKEZI = re.compile(r"MERKEZ[İIÎV]?\s*SINAV\s*SORULAR", re.IGNORECASE)
# İçindekiler sayfası: başlığından ya da "...... 185" biçimindeki
# nokta dizisi + sayfa numarası satırlarından tanınır.
_ICINDEKILER = re.compile(r"İÇİNDEKİLER|ICINDEKILER|CONTENTS", re.IGNORECASE)
_TOC_NOKTALI = re.compile(r"\.{4,}\s*\d{1,3}\b")
# Bozuk yazı tipinde nokta dizisi "= K = K = K =" gibi görünür
_TOC_NOKTALI_K = re.compile(r"(?:[=\s]*K){6,}")


def _icindekiler_mi(*metinler):
    """Sayfa bir İÇİNDEKİLER sayfası mı?

    ÖNEMLİ - KULLANICININ SÖZEL KİTABI HİÇ AKTARILAMIYORDU: O kitabın
    içindekiler sayfasında "2018-2019 Merkezi Sınav Soruları ..... 345"
    gibi SEKİZ satır var. Program bu satırları görünce daha kitabın 3.
    sayfasındayken "geçmiş yıl bölümü başladı" sanıp kitabın TAMAMINI
    atlıyordu; sonuçta 3 dersin (İnkılap, Din Kültürü, İngilizce) hiçbiri
    eklenemiyordu. Artık içindekiler sayfası baştan tanınıp es geçiliyor."""
    for m in metinler:
        if m and _ICINDEKILER.search(m):
            return True
    for m in metinler:
        if m and (len(_TOC_NOKTALI.findall(m)) >= 4
                  or len(_TOC_NOKTALI_K.findall(m)) >= 4):
            return True
    return False


def _merkezi_kapak_mi(duz, duz_k):
    """Sayfa, geçmiş yıl merkezî sınav bölümünün KAPAĞI mı?

    Sadece ifadeyi aramak yetmiyordu (bkz. _icindekiler_mi). Kapak
    sayfaları kısadır; uzun bir metin sayfasında geçen aynı ifade
    bölümün başladığı anlamına gelmez."""
    if not (_MERKEZI.search(duz or "") or _MERKEZI.search(duz_k or "")):
        return False
    return len((duz or "").split()) <= 150


def _duz(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _sayfa_metni(doc, i):
    tp = doc[i].get_textpage()
    try:
        return tp.get_text_range() or ""
    finally:
        tp.close()


def _sol_serit_numaralari(doc, i, sol=14, sag=54):
    """Sayfanın SOL KENARINDAKİ dar şeritte basılı soru numaralarını verir.

    Neden dar şerit: gövde metninde de "1990. yılda" gibi sayılar geçiyor;
    soru numarası ise her zaman sol kenarda, kendi başına duruyor. Ölçüldü:
    bu kitaplarda soru numaraları x≈28'de başlıyor, gövde metni x≈40+."""
    page = doc[i]
    h = page.get_height()
    tp = page.get_textpage()
    try:
        s = tp.get_text_bounded(left=sol, bottom=0, right=sag, top=h) or ""
    finally:
        tp.close()
    return [int(m) for m in re.findall(r"(?<!\d)(\d{1,3})\s*\.(?!\d)", s)]


def _anahtar_bloklari(metin):
    """Bir cevap anahtarı sayfasındaki '1. Ünite -> cevaplar' bloklarını okur.

    Döner: {unite_no: {"tur": "Ünite"/"Tema", "cevaplar": {soru_no: harf}}}
    (ünite başlığı hiç yoksa boş sözlük -> bu bir merkezî sınav anahtarıdır)"""
    bloklar = {}
    ham = metin
    yerler = [(m.start(), int(m.group(1)), m.group(2)) for m in _UNITE_BASLIK.finditer(metin)]
    if not yerler:
        # İngilizce bölümü: "Unit 1", "Unit 2" ... (sayı kelimeden SONRA).
        # ÖNEMLİ: Orijinal metinde aranır -- kaydırma onarımı düzgün
        # yazılmış İngilizce başlıkları bozar.
        yerler = [(m.start(), int(m.group(1)), "Ünite")
                  for m in _UNITE_BASLIK_EN.finditer(ham)]
    if not yerler:
        # Başlıklar kaydırılmış yazı tipiyle basılmış olabilir
        metin = _kaydirmayi_coz(metin)
        yerler = [(m.start(), int(m.group(1)), m.group(2))
                  for m in _UNITE_BASLIK.finditer(metin)]
    if not yerler:
        yerler = [(m.start(), int(m.group(1)), "Ünite")
                  for m in _UNITE_BASLIK_EN.finditer(metin)]
    if not yerler:
        return {}
    # ÖNEMLİ - TABLO DÜZENİ: Bazı kitaplarda (kullanıcının Fen kitabı gibi)
    # cevap anahtarı bir TABLO; "1. Ünite" yazısı tablonun SOL sütununda,
    # ortada duruyor. PDF'ten metin okunduğunda bu yazı, cevapların arasında
    # herhangi bir yere düşebiliyor. Sayfada TEK ünite başlığı varsa,
    # sayfadaki bütün cevapları ona veriyoruz -- konumdan bağımsız, güvenli.
    if len(yerler) == 1:
        _bas, no, tur = yerler[0]
        cevaplar = _cevaplari_ayikla(metin)
        if cevaplar:
            bloklar[no] = {"tur": tur.title(), "cevaplar": cevaplar}
        return bloklar
    for idx, (bas, no, tur) in enumerate(yerler):
        son = yerler[idx + 1][0] if idx + 1 < len(yerler) else len(metin)
        cevaplar = _cevaplari_ayikla(metin[bas:son])
        if cevaplar:
            # Aynı ünite birden fazla sayfaya taşmış olabilir -> birleştir
            k = bloklar.setdefault(no, {"tur": tur.title(), "cevaplar": {}})
            k["cevaplar"].update(cevaplar)
    return bloklar


def _cevaplari_ayikla(parca):
    """Bir metin parçasındaki '12. C' biçimindeki cevapları toplar."""
    cevaplar = {}
    for m in _CEVAP_BITISIK.finditer(parca):
        no = m.group(1) or m.group(3)
        harf = m.group(2) or m.group(4)
        if no and harf:
            cevaplar[int(no)] = harf
    return cevaplar


def _anahtar_dersi(metin):
    """Cevap anahtarı sayfasının başlığındaki ders adını bulur."""
    for satir in metin.splitlines()[:6]:
        L = _duz(satir)
        if not L or "CEVAP" in L.upper():
            continue
        d = _ayrac_dersi(L)
        if d:
            return d
    for d in DERS_ADLARI:
        if _altdizi_mi(_sadece_harfler(d), _sadece_harfler(metin[:400])):
            return d
    return None


# --- MEB PDF'lerindeki bozuk yazı tipi kodlamasını onarma ------------------
# ÖNEMLİ: Bu kitaplarda başlıklar ve kalın yazılar, PDF'ten okunduğunda
# ANLAMSIZ görünüyor:
#     "0DWHPDWLN"                 -> "Matematik"
#     "\x14\x11hQLWH\x1ddDUSDQODU"  -> "1. Ünite: Çarpanlar..."
# Sebep: yazı tipi gömülürken her harfin kodu 29 kaydırılmış, PDF de bu ham
# kodları veriyor. Aşağıdaki iki fonksiyon metni okunur hâle getiriyor.
# (Soru numaraları ve cevap harfleri normal yazı tipinde olduğu için
#  bunlardan etkilenmez; bu onarım sadece BAŞLIKLAR için gerekli.)

# Kaydırma OLMADAN, doğrudan yanlış eşlenen Türkçe harfler
_MEB_HARF = {
    "Õ": "ı", "÷": "ğ", "ú": "ş", "ø": "İ", "ù": "Ş", "ö": "ö",
    "³": '"', "´": '"', "¶": "'", "\ufffe": "",
}
# 29 kaydırıldıktan sonra 0x80-0x9F aralığına düşen Türkçe harfler
_KAYDIRMA_OZEL = {
    0x81: "Ç", 0x84: "Ö", 0x85: "Ü", 0x8C: "ç", 0x99: "ö", 0x9E: "ü",
    0x8A: "Ş", 0x9A: "ş", 0x8D: "İ", 0x9D: "ı", 0x8E: "Ğ", 0x9F: "ğ",
}


def _meb_duzelt(s):
    """Kaydırma OLMAYAN metindeki yanlış Türkçe harfleri düzeltir."""
    if not s:
        return s
    for a, b in _MEB_HARF.items():
        s = s.replace(a, b)
    return s


def _kaydirmayi_coz(s):
    """Tamamı 29 kaydırılmış bir başlığı okunur hâle getirir."""
    if not s:
        return s
    out = []
    for ch in s:
        if ch in _MEB_HARF:          # bu harfler kaydırılmamış
            out.append(_MEB_HARF[ch])
            continue
        if ch in "İıŞşĞğÜüÖöÇç":     # zaten doğru okunmuş Türkçe harfler
            out.append(ch)
            continue
        n = ord(ch) + 29
        if n in _KAYDIRMA_OZEL:
            out.append(_KAYDIRMA_OZEL[n])
        elif 0x20 <= n < 0x250:
            out.append(chr(n))
        else:
            out.append(ch)
    return "".join(out)


def _bozuk_onar(metin):
    """Sadece OKUNAMAYAN karakterleri (kontrol karakterleri) onarır,
    geri kalanına dokunmaz. '\\x15. hQLWH' -> '2. hQLWH' gibi."""
    return "".join(
        _kaydirmayi_coz(c) if (ord(c) < 0x20 and c not in "\r\n\t") or 0x80 <= ord(c) < 0xA0 else c
        for c in _meb_duzelt(metin or "")
    )


def _turkce_puani(t):
    """Bir kelimenin ne kadar 'okunur Türkçe' göründüğünü ölçer."""
    harf = sum(1 for c in t if c.isalpha())
    bozuk = sum(1 for c in t if ord(c) < 0x20 or 0x80 <= ord(c) < 0xA0 or ord(c) in (0xFFFD,))
    return harf - 3 * bozuk


def _baslik_onar(metin):
    """Aynı satırda hem normal hem kaydırılmış yazı olabiliyor.

    ÖLÇÜLDÜ: İçindekiler sayfasında "1. Ünite" kaydırılmış, hemen yanındaki
    noktalar ve sayfa numarası normal; başka bir kitapta ise sadece kelimenin
    İLK harfi kaydırılmış ("'oğa ve Evren" -> "Doğa ve Evren"). Bu yüzden
    onarım kelime kelime yapılıyor ve her kelime için hangi okunuş daha
    'Türkçe' görünüyorsa o seçiliyor."""
    out = []
    for tok in re.split(r"(\s+)", metin or ""):
        if not tok.strip():
            out.append(tok)
            continue
        duz = _meb_duzelt(tok)
        kay = _kaydirmayi_coz(tok)
        if _turkce_puani(kay) > _turkce_puani(duz):
            out.append(kay)
            continue
        # Kelime doğru okunuyor ama İLK harfi kaydırılmış olabilir
        if len(duz) > 2 and not duz[0].isalpha() and duz[1:2].isalpha():
            ilk = _kaydirmayi_coz(duz[0])
            if ilk.isalpha():
                duz = ilk + duz[1:]
        out.append(duz)
    return "".join(out)


def _okunuslar(metin):
    """Bir sayfa metninin OKUNABİLİR sürümlerini verir.

    Aynı sayfada hem normal hem kaydırılmış yazı olabildiği için hepsini
    deneyip hangisinde aradığımızı bulursak onu kullanıyoruz."""
    return (_meb_duzelt(metin), _kaydirmayi_coz(metin), _baslik_onar(metin))


# ÖNEMLİ - ÇOK DERSLİ KİTAPLARDAKİ HATA:
# Kitaplar ders adını her yerde TAM yazmıyor. Cevap anahtarı sayfasının
# başlığında "T.C. İNKILAP TARİHİ VE ATATÜRKÇÜLÜK" yerine sadece
# "İNKILAP TARİHİ", "DİN KÜLTÜRÜ VE AHLAK BİLGİSİ" yerine "DİN KÜLTÜRÜ"
# yazabiliyor. Eski kod tam adı aradığı için bu dersleri TANIYAMIYOR,
# hepsini kitabın kapağındaki derse (Türkçe) yazıyordu. Sonuçta İnkılap'ın
# "1. Ünite"si, Türkçe'nin "1. Ünite"siyle aynı ada sahip oluyor ve
# "zaten var" denip atlanıyordu -- kullanıcının "Din, İngilizce, İnkılap
# hiç çıkmıyor" dediği durum tam olarak buydu.
# Çözüm: her ders için kısa yazımların listesi.
DERS_TAKMA_ADLARI = {
    "Türkçe": ["Türkçe"],
    "Matematik": ["Matematik"],
    "Fen Bilimleri": ["Fen Bilimleri", "Fen ve Teknoloji", "Fen Bilgisi"],
    "T.C. İnkılap Tarihi ve Atatürkçülük": [
        "T.C. İnkılap Tarihi ve Atatürkçülük", "İnkılap Tarihi ve Atatürkçülük",
        "TC İnkılap Tarihi", "İnkılap Tarihi", "İnkılâp Tarihi", "İnkılap", "İnkılâp",
    ],
    "Din Kültürü ve Ahlak Bilgisi": [
        "Din Kültürü ve Ahlak Bilgisi", "Din Kültürü ve Ahlâk Bilgisi",
        "Din Kültürü", "Din Kültürü ve Ahlak",
    ],
    "İngilizce": ["İngilizce", "Yabancı Dil (İngilizce)", "Yabancı Dil"],
    "Sosyal Bilgiler": ["Sosyal Bilgiler"],
}

# Uzun yazımlar önce denensin ki "İnkılap Tarihi" varken "İnkılap" ile
# yetinilmesin.
_DERS_ARAMA = sorted(
    ((takma, ders) for ders, takmalar in DERS_TAKMA_ADLARI.items() for takma in takmalar),
    key=lambda x: len(x[0]), reverse=True,
)


def _ders_bul_kesin(metin):
    """Metinde ders adını KESİN olarak (harf harf, bitişik) arar.

    ÖNEMLİ: Burada eskiden 'harfler sırayla geçiyor mu' testi kullanılıyordu.
    O test ders AYRAÇ sayfaları için yazılmıştı. Ama bir cevap anahtarı
    sayfasının 400 karakterinde 'T-ü-r-k-ç-e' harfleri tesadüfen sırayla
    bulunabiliyor: ölçüldü, Matematik kitabı 'Türkçe' sanıldı. Artık
    bitişik aranıyor ve kısa yazımlar da tanınıyor."""
    okunuslar = _okunuslar(metin)
    for okunus in okunuslar:
        hedef = _sadece_harfler(okunus)
        for takma, ders in _DERS_ARAMA:
            if _sadece_harfler(takma) in hedef:
                return ders
    # İKİNCİ DENEME - BOZUK "İ" HARFİ: Bu PDF'lerde en sık bozulan karakter
    # Türkçe'nin noktalı/noktasız i'sidir; bazen hiç çıkmaz
    # ("T.C. İNKILAP TARİHİ" -> "T.C. NKILAP TARH"). Bu yüzden ikinci turda
    # i/ı/İ/I harfleri İKİ TARAFTAN DA atılıp karşılaştırılıyor.
    def _isiz(x):
        return re.sub(r"[Iİıi]", "", _sadece_harfler(x))
    for okunus in okunuslar:
        hedef = _isiz(okunus)
        for takma, ders in _DERS_ARAMA:
            t = _isiz(takma)
            if len(t) >= 5 and t in hedef:
                return ders
    return None


def _anahtar_dersi(metin):
    """Cevap anahtarı sayfasının BAŞLIĞINDAKİ ders adını bulur."""
    bas = "\n".join(metin.splitlines()[:6])
    return _ders_bul_kesin(bas) or _ders_bul_kesin(metin[:600])


# ÖNEMLİ: Aynı satırda bazı harfler kaydırılmış, bazıları normal olabiliyor.
# Bu yüzden numarayla "Ünite" arasındaki ayırıcı bazen "." bazen "K" (nokta
# karakterinin kaydırılmış hâli), boşluk yerine de "=" görünebiliyor.
# Ayrıca kaydırılmış hâliyle "Ünite" -> "hQLWH", "Tema" -> "7HPD" olur.
_TOC_SATIRI = re.compile(
    r"^[\s=]*(\d{1,2})[\s=]*[\.\)K]?[\s=]*"
    r"(?:Ünite|ÜNİTE|ünite|Tema|TEMA|tema|Bölüm|BÖLÜM|hQLWH|7HPD|%|OP)"
    r"[\s=]*[:\.\-]?[\s=]*(.*)$"
)
# İngilizce içindekiler satırı: "Unit 3: In the Kitchen ....... 231"
_TOC_SATIRI_EN = re.compile(
    r"^\s*(?:Unit|UNIT|unit)\s*[:\.\-]?\s*(\d{1,2})\s*[:\.\-]?\s*(.*)$"
)


def _ad_temizle(ad):
    """İçindekiler satırından ünite adını çıkarır.

    Satırın sonundaki nokta dizisi ve sayfa numarası atılır. Kaydırılmış
    yazı tipinde noktalar 'K', boşluklar '=' gibi görünebildiği için bunlar
    da temizlenir. Sonuç şüpheli görünüyorsa (kelimeler birbirine yapışmış,
    okunamayan karakter var) ad KULLANILMAZ -- yanlış bir başlık, hiç
    başlık olmamasından kötüdür."""
    ad = ad.replace("=", " ")
    ad = re.split(r"[\.\s]{4,}|(?:\s[K]){4,}", ad)[0]
    ad = re.sub(r"(?:[\sK\.]){3,}$", "", ad).strip(" .:-–—")
    ad = _duz(ad)
    if len(ad) < 3 or len(ad) > 70:
        return None
    if any(ord(c) < 0x20 or 0x80 <= ord(c) < 0xA0 or c == "\ufffd" for c in ad):
        return None
    if " " not in ad and len(ad) > 24:
        return None       # kelimeler birbirine yapışmış
    if sum(1 for c in ad if c.isalpha()) < len(ad) * 0.6:
        return None
    # "1. C 2. A 3. B" gibi cevap dizilerini ad sanma
    if len(re.findall(r"\d{1,3}\s*[\.\)]\s*[A-E]\b", ad)) >= 2:
        return None
    _kelimeler = [w for w in ad.split() if w]
    if _kelimeler and sum(1 for w in _kelimeler if len(w) <= 2) > len(_kelimeler) * 0.6:
        return None
    return ad


def _ad_puani(ad):
    """Bir başlığın ne kadar 'düzgün Türkçe' göründüğünü ölçer.

    Kaydırılmış okunuşlar harf gibi görünür ama kelime ortasında BÜYÜK
    harf ve '|', ']', '&' gibi karakterler barındırır
    ("DUHN|NOü İIDGHOHU" <-> "Kareköklü İfadeler"). Bu ölçü doğru okunuşu
    seçmemizi sağlıyor."""
    if not ad:
        return -999
    puan = sum(1 for c in ad if c.isalpha())
    puan -= 4 * sum(1 for c in ad if not (c.isalpha() or c in " -'’()/,."))
    for onceki, simdiki in zip(ad, ad[1:]):
        if onceki.islower() and simdiki.isupper():
            puan -= 3
    return puan


def _unite_adlari(doc, tarama=20):
    """İÇİNDEKİLER sayfasından '1. Ünite: Çarpanlar ve Katlar' gibi
    ünite adlarını okur. Bulunamazsa boş döner (test yine eklenir,
    sadece adında konu yazmaz).

    ÖLÇÜLDÜ: Bazı kitaplarda '1. Ünite' ile konu adı AYRI SATIRLARDA
    yazılıyor; bu yüzden başlık bulunduğunda konu adı aynı satırda yoksa
    bir SONRAKİ satıra bakılıyor.

    ÇOK DERSLİ KİTAPLAR (sözel kitabı gibi): İçindekilerde her dersin
    üniteleri 1'den yeniden başlar -- İnkılap'ın 1. ünitesi de var, Din
    Kültürü'nün 1. ünitesi de. Adlar sadece numaraya göre saklanırsa
    Din Kültürü'nün 1. ünitesi "Bir Kahraman Doğuyor" diye görünürdü.
    Bu yüzden içindekiler taranırken hangi dersin altında olunduğu da
    takip edilip adlar (ders, numara) çiftine göre saklanıyor.

    Döner: {"ders": {(ders, no): ad}, "genel": {no: ad}, "_ders": son_ders}"""
    adlar = {"ders": {}, "genel": {}, "_ders": None}
    for i in range(min(tarama, len(doc))):
        _unite_adlari_sayfa(adlar, _sayfa_metni(doc, i))
    return adlar


def _unite_adlari_sayfa(adlar, _sayfa):
    """Tek bir içindekiler sayfasını okuyup `adlar` sözlüğünü büyütür.

    Çok dersli kitaplarda her dersin kendi içindekiler sayfası kitabın
    ortasında olabiliyor; bu yüzden ana tarama sırasında karşılaşılan
    içindekiler sayfaları da buraya veriliyor."""
    ders_adlari, genel = adlar["ders"], adlar["genel"]
    su_ders = adlar.get("_ders")
    bu_sayfa, sayfa_dersleri = [], []
    if True:
        # ÖNEMLİ: Cevap anahtarı sayfası da "1. Ünite" başlığı taşır ve hemen
        # altında cevaplar gelir. İçindekiler sanılırsa ünite adı
        # "1. Ünite · 1. C 2. A 3. B" gibi saçma çıkıyordu. Bu sayfalar atlanır.
        if "CEVAPANAHTARI" in _sadece_harfler(_sayfa):
            adlar["_ders"] = su_ders
            return adlar
        ham = _sayfa.splitlines()
        # Her satırı ayrı ayrı, en okunur hâline getir
        satirlar = []
        for satir in ham:
            adaylar = [_meb_duzelt(satir), _bozuk_onar(satir),
                       _baslik_onar(satir), _kaydirmayi_coz(satir)]
            satirlar.append(adaylar)
        for k, adaylar in enumerate(satirlar):
            no, ad_adaylari = None, []
            for okunus in adaylar:
                m = _TOC_SATIRI.match(okunus) or _TOC_SATIRI_EN.match(okunus)
                if not m:
                    continue
                no = int(m.group(1))
                ad_adaylari.append(_ad_temizle(m.group(2) or ""))
            if no is None:
                # Ünite satırı değil: ders başlığı olabilir mi?
                for okunus in adaylar:
                    _d = _ders_bul_kesin(okunus)
                    if _d and len(_duz(okunus)) <= 60:
                        su_ders = _d
                        if _d not in sayfa_dersleri:
                            sayfa_dersleri.append(_d)
                        break
                continue
            if not (1 <= no <= 30):
                continue
            if no in genel and (su_ders, no) in ders_adlari:
                continue
            if not any(ad_adaylari) and k + 1 < len(satirlar):
                ad_adaylari = [_ad_temizle(x) for x in satirlar[k + 1]]
            ad_adaylari = [a for a in ad_adaylari if a]
            if not ad_adaylari:
                continue
            en_iyi = max(ad_adaylari, key=_ad_puani)
            if _ad_puani(en_iyi) >= 4:
                _ad = _kelime_onar(en_iyi)
                genel.setdefault(no, _ad)
                bu_sayfa.append((no, _ad))
                if su_ders:
                    ders_adlari.setdefault((su_ders, no), _ad)
    # DERS BAŞLIĞI SAYFANIN İÇİNDE GEÇ GÖRÜNMÜŞ OLABİLİR: Bazı kitaplarda
    # "MATEMATİK" başlığı, ilk ünite satırıyla AYNI satırda basılı; o
    # yüzden ünite adları okunurken hangi derste olduğumuz henüz belli
    # değil. Sayfada TEK ders adı geçiyorsa adlar geriye dönük o derse
    # yazılır. Birden fazla ders varsa (iki sütunlu içindekiler) karışma
    # riski olduğu için dokunulmaz -- yanlış ad, adsızlıktan kötüdür.
    if len(sayfa_dersleri) == 1:
        for no, _ad in bu_sayfa:
            ders_adlari.setdefault((sayfa_dersleri[0], no), _ad)
    adlar["_ders"] = su_ders
    return adlar


def _unite_adi(adlar, ders, no, tek_ders):
    """Ünite adını seçer. Çok dersli kitapta YANLIŞ ad göstermektense
    hiç ad göstermemeyi tercih ediyoruz."""
    if not adlar:
        return None
    _d = (adlar.get("ders") or {}).get((ders, no))
    if _d:
        return _d
    if tek_ders:
        return (adlar.get("genel") or {}).get(no)
    return None


_SESLI = set("aeıioöuüAEIİOÖUÜ")


def _kelime_onar(ad):
    """Başlık içinde tek tek kalmış bozuk kelimeleri düzeltir.

    İki durum var (ikisi de gerçek dosyalarda görüldü):
      1. Kelimenin sadece İLK harfi kaydırılmış:  "'oğa"  -> "Doğa"
      2. Kelimenin TAMAMI kaydırılmış:  "İIDGHOHU YH" -> "İfadeler ve"
         (Bu kelimeler baştan sona BÜYÜK harf görünür; başlığın geri kalanı
          normal yazıldığı için ayırt edilebiliyorlar.)"""
    karisik = any(c.islower() for c in ad)
    out = []
    for tok in re.split(r"(\s+)", ad):
        if len(tok) > 2 and not tok[0].isalpha() and tok[1].isalpha():
            ilk = _kaydirmayi_coz(tok[0])
            if ilk.isalpha():
                tok = ilk + tok[1:]
        harfler = [c for c in tok if c.isalpha()]
        if karisik and len(harfler) >= 2 and all(c.isupper() for c in harfler):
            aday = _kaydirmayi_coz(tok)
            if (any(c in _SESLI for c in aday)
                    and any(c.islower() for c in aday)
                    and all(c.isalpha() or c in " -'" for c in aday)):
                tok = aday
        out.append(tok)
    return "".join(out)


def _bolumlere_ayir(govde):
    """Gövde sayfalarını üniteye böler.

    govde: [(sayfa_no_1tabanli, [sayfadaki soru numaraları]), ...]
    Kural: soru numaralandırması 1'e döndüğünde YENİ ünite başlar. Soru
    numarası olmayan sayfalar (okuma metni, görsel sayfası) açık olan
    ünitenin içinde sayılır; ünitenin SONUNDAKİ numarasız sayfalar
    (bir sonraki ünitenin kapağı) atılır."""
    bolumler = []
    su = None
    for sayfa, nums in govde:
        if not nums:
            if su is not None:
                su["sayfalar"].append(sayfa)
            continue
        if su is None or nums[0] == 1:
            su = {"sayfalar": [], "numaralar": [], "sayfa_numaralari": []}
            bolumler.append(su)
        su["sayfalar"].append(sayfa)
        su["numaralar"].extend(nums)
        su["sayfa_numaralari"].append((sayfa, nums))
    for b in bolumler:
        gecerli = {s for s, _ in b["sayfa_numaralari"]}
        while b["sayfalar"] and b["sayfalar"][-1] not in gecerli:
            b["sayfalar"].pop()
    # Aynı soru numarası iki sayfada birden görünebiliyor (soru sayfa
    # sonunda bölünmüşse). Tekrarları ayıklıyoruz -- yoksa optik formda
    # aynı soru iki kez çıkardı.
    for b in bolumler:
        gorulen = set()
        for k, (sayfa, nums) in enumerate(b["sayfa_numaralari"]):
            yeni_nums = [n for n in nums if n not in gorulen]
            gorulen.update(yeni_nums)
            b["sayfa_numaralari"][k] = (sayfa, yeni_nums)
        b["numaralar"] = [n for _s, ns in b["sayfa_numaralari"] for n in ns]

    # ÖNEMLİ - "İÇİNDEKİLER" TUZAĞI: İçindekiler sayfasında da sol kenarda
    # "1.", "2.", "3." diye numaralar var; bu sayfa sahte bir bölüm üretip
    # bütün eşleşmeyi bir kaydırıyordu (ölçüldü: 8 ünite yerine 9 bölüm).
    # Gerçek bir ünite bölümü 1'den başlar ve hiçbir numarayı atlamaz.
    temiz = []
    for b in bolumler:
        n = b["numaralar"]
        if not n or n[0] != 1:
            continue
        if sorted(n) != list(range(1, max(n) + 1)):
            continue
        temiz.append(b)
    return temiz


def _esle(bolumler, anahtar):
    """Gövde bölümlerini cevap anahtarındaki ünitelerle eşler.

    Sadece sırayla eşlemek kırılgan: kitabın başındaki/sonundaki fazladan
    bir bölüm her şeyi kaydırır. Bu yüzden önce SORU SAYILARINA bakılır --
    her ünitenin soru sayısı cevap anahtarında zaten yazılı olduğu için
    bu, doğru eşleşmenin en güvenilir kanıtıdır."""
    nolar = sorted(anahtar)
    hedef = [len(anahtar[n]["cevaplar"]) for n in nolar]
    sayilar = [len(b["numaralar"]) for b in bolumler]
    if sayilar == hedef:
        return list(zip(nolar, bolumler)), None
    # Soru sayılarını sırayla tutturmaya çalış (fazlalık bölümleri atlar)
    eslesme, j, atlanan = [], 0, 0
    for no, adet in zip(nolar, hedef):
        k = j
        while k < len(bolumler) and len(bolumler[k]["numaralar"]) != adet:
            k += 1
        if k < len(bolumler):
            atlanan += k - j
            eslesme.append((no, bolumler[k]))
            j = k + 1
    if len(eslesme) == len(nolar):
        return eslesme, None
    # Son çare: sırayla eşle
    return (
        list(zip(nolar, bolumler)),
        f"kitapta {len(bolumler)} bölüm bulundu, cevap anahtarında {len(nolar)} ünite var",
    )


def _parcala(bolum, cevaplar, parca_soru):
    """Bir üniteyi, sayfa sınırlarını bozmadan, yaklaşık `parca_soru`
    soruluk parçalara böler. parca_soru<=0 ise tek parça döner.

    ÖNEMLİ - KULLANICI İSTEĞİ ("son 30 kalanı ekleyerek bölsün"): Bölme
    sonunda 5-7 soruluk minik bir artık parça kalabiliyordu ("5. Ünite
    (2/2) - 7 soru"). Böyle bir kırıntı test olarak anlamsız; artık son
    parça yarımdan azsa BİR ÖNCEKİ parçaya ekleniyor. Yani 32 soruluk bir
    ünite 30'a bölünürse iki parça değil, 32 soruluk TEK test olur."""
    if parca_soru and parca_soru > 0:
        parcalar, su = [], None
        for sayfa, nums in bolum["sayfa_numaralari"]:
            if su is None or (len(su["numaralar"]) >= parca_soru and nums):
                su = {"sayfalar": [], "numaralar": []}
                parcalar.append(su)
            su["sayfalar"].append(sayfa)
            su["numaralar"].extend(nums)
        _atanan = {s for p in parcalar for s in p["sayfalar"]}
        for sayfa in bolum["sayfalar"]:
            if sayfa in _atanan:
                continue
            for p in parcalar:
                if p["sayfalar"] and p["sayfalar"][-1] == sayfa - 1:
                    p["sayfalar"].append(sayfa)
                    break
        # Sondaki kırıntı parçayı bir öncekine ekle
        while len(parcalar) > 1 and len(parcalar[-1]["numaralar"]) < max(2, parca_soru // 2):
            son = parcalar.pop()
            parcalar[-1]["sayfalar"] += son["sayfalar"]
            parcalar[-1]["numaralar"] += son["numaralar"]
        for p in parcalar:
            p["sayfalar"] = sorted(set(p["sayfalar"]))
            p["numaralar"] = sorted(set(p["numaralar"]))
    else:
        parcalar = [{"sayfalar": list(bolum["sayfalar"]),
                     "numaralar": list(bolum["numaralar"])}]
    for p in parcalar:
        p["numaralar"] = [n for n in p["numaralar"] if n in cevaplar]
    return [p for p in parcalar if p["numaralar"]]


def unite_kitabini_coz(pdf_path, parca_soru=0):
    """'Ünite / Tema' düzenindeki bir çalışma kitabını ayrıştırır.

    parca_soru: 0 -> her ünite tek test; 20 -> üniteler ~20 soruluk
                parçalara bölünür (uzun ünitelerde çocuk boğulmasın diye).

    Döner: (testler, uyarilar) -- testler eski biçimle aynı alanlara sahip,
    böylece uygulamanın geri kalanı değişmeden çalışır."""
    import pypdfium2 as pdfium

    testler, uyarilar = [], []
    doc = pdfium.PdfDocument(pdf_path)
    try:
        adlar = _unite_adlari(doc)
        kitap_dersi = _kitap_dersi(doc)
        govde = []
        acik = None
        merkezi_basladi = False

        kullanilan_dersler = []

        def _kapat(acik_kayit):
            if not acik_kayit or not acik_kayit["bloklar"]:
                return
            ders = acik_kayit["ders"]
            if not ders:
                # ÖNEMLİ: Ders adı okunamadıysa kitabın kapağındaki derse
                # yazmak TEHLİKELİ -- çok dersli kitaplarda bütün dersler
                # aynı ada düşer, üniteler "zaten var" diye atlanır ve
                # kullanıcı "Din, İngilizce hiç çıkmıyor" der. Bu yüzden
                # tanınmayan bölüme AYRI bir ad veriliyor.
                ders = kitap_dersi if not kullanilan_dersler else None
                if not ders or ders in kullanilan_dersler:
                    ders = f"Bölüm {len(kullanilan_dersler) + 1}"
                    uyarilar.append(
                        f"Bir bölümün ders adı okunamadı; '{ders}' olarak eklendi. "
                        f"İsterseniz 'Kayıtlı Denemeler'den silip 'Diğer Kategori' "
                        f"bölümünden doğru adla ekleyebilirsiniz."
                    )
            if ders not in kullanilan_dersler:
                kullanilan_dersler.append(ders)
            bolumler = _bolumlere_ayir(acik_kayit["govde"])
            if not bolumler:
                uyarilar.append(f"{ders}: cevap anahtarı bulundu ama soru sayfaları eşleşmedi.")
                return
            eslesme, uyari = _esle(bolumler, acik_kayit["bloklar"])
            if uyari:
                uyarilar.append(f"{ders}: {uyari}. En yakın eşleşme kullanıldı.")
            for unite_no, bolum in eslesme:
                blok = acik_kayit["bloklar"][unite_no]
                cevaplar, tur = blok["cevaplar"], blok.get("tur") or "Ünite"
                ad = _unite_adi(adlar, ders, unite_no,
                                len((adlar.get("ders") or {})) == 0)
                kok = f"{unite_no}. {tur}" + (f" · {ad}" if ad else "")
                parcalar = _parcala(bolum, cevaplar, parca_soru)
                for pno, parca in enumerate(parcalar, start=1):
                    konu = kok if len(parcalar) == 1 else f"{kok} ({pno}/{len(parcalar)})"
                    testler.append({
                        "ders": ders,
                        "test_no": unite_no * 100 + pno if len(parcalar) > 1 else unite_no,
                        "konu": konu,
                        "sayfalar": parca["sayfalar"],
                        "numaralar": parca["numaralar"],
                        "cevaplar": {n: cevaplar[n] for n in parca["numaralar"]},
                        "soru_sayisi": len(parca["numaralar"]),
                        "anahtar_soru_sayisi": len(parca["numaralar"]),
                        "unite_no": unite_no,
                        "tur": "unite",
                    })

        for i in range(len(doc)):
            metin = _sayfa_metni(doc, i)
            duz = _duz(metin)
            duz_k = _duz(_kaydirmayi_coz(metin))
            # ÖNEMLİ: İçindekiler sayfası bölüm kapağı ya da cevap anahtarı
            # SAYILMAZ. Ama sayfayı komple atmıyoruz -- ölçüldü: Türkçe
            # kitabında "içindekiler" konulu bir SORU sayfası da bu tarife
            # uyuyor ve atıldığında o ünitenin soru numaraları kopup ünite
            # komple kayboluyordu. Sayfa gövdede kalır; sahte bölümleri
            # zaten "1'den başlamalı ve numara atlamamalı" kuralı eliyor.
            _toc = _icindekiler_mi(duz, duz_k)
            if _toc and i >= 20:
                # Çok dersli kitaplarda ikinci/üçüncü dersin içindekiler
                # sayfası kitabın ortasındadır; ünite adları oradan gelir.
                try:
                    _unite_adlari_sayfa(adlar, metin)
                except Exception:
                    pass
            if not _toc and _merkezi_kapak_mi(duz, duz_k):
                # Geçmiş yıl LGS soruları bölümü -> kullanıcı istemiyor, atla
                if not merkezi_basladi:
                    uyarilar.append(
                        "ℹ️ Kitabın sonundaki **geçmiş yıl merkezî sınav soruları** "
                        "bölümü bilerek alınmadı. O sınavların tamamını, resmî ve "
                        "eksiksiz hâliyle **Otomatik İndirme (Resmî EBA Arşivi)** "
                        "bölümünden tek tuşla ekleyebilirsiniz."
                    )
                merkezi_basladi = True
                _kapat(acik)
                acik, govde = None, []
                continue
            _harfler = _sadece_harfler(duz)
            _harfler_k = _sadece_harfler(duz_k)
            if not _toc and ("CEVAPANAHTARI" in _harfler or "CEVAPANAHTARI" in _harfler_k
                             # İngilizce bölümlerinde başlık "Answer Key" olur
                             or "ANSWERKEY" in _harfler or "ANSWERKEY" in _harfler_k):
                bloklar = _anahtar_bloklari(metin)
                if not bloklar:
                    _kapat(acik)
                    acik, govde = None, []
                    continue
                if acik is None:
                    acik = {"ders": _anahtar_dersi(metin), "bloklar": {}, "govde": govde}
                    govde = []
                for no, blok in bloklar.items():
                    hedef = acik["bloklar"].setdefault(
                        no, {"tur": blok["tur"], "cevaplar": {}}
                    )
                    hedef["cevaplar"].update(blok["cevaplar"])
                continue
            if acik is not None:
                _kapat(acik)
                acik, govde = None, []
            if merkezi_basladi:
                continue
            govde.append((i + 1, _sol_serit_numaralari(doc, i)))
        _kapat(acik)
    finally:
        doc.close()
    return testler, uyarilar


def _kitap_dersi(doc):
    """Kitabın kapağından/ilk sayfalarından ders adını tahmin eder."""
    for i in range(min(4, len(doc))):
        d = _ders_bul_kesin(_sayfa_metni(doc, i))
        if d:
            return d
    return None


def testleri_bul(pdf_path, parca_soru=0):
    """Yüklenen PDF'i tarar -- BİÇİMİ KENDİ ANLAR.

    Elimizde iki farklı kitap düzeni var ve kullanıcı hangisini yüklediğini
    bilmek zorunda kalmasın istiyoruz:

      1. Klasik soru bankası (IQ Yayınları gibi): sayfanın üstünde "TEST 3",
         kitabın sonunda "TEST - 3  1-B 2-C ..." biçiminde anahtar.
      2. MEB LGS Çalışma Kitabı: "1. Ünite" bölümleri, ünite içinde 1'den
         devam eden soru numaraları, sonda "1. Ünite / 1. C 2. A ..." anahtarı.

    İkisi de denenir, HANGİSİ DAHA ÇOK ÇÖZÜLEBİLİR TEST buluyorsa o kullanılır.

    Döner: (testler, cevap_anahtari, uyarilar)"""
    hatalar = []
    try:
        unite_testler, unite_uyari = unite_kitabini_coz(pdf_path, parca_soru=parca_soru)
    except Exception as e:
        unite_testler, unite_uyari = [], []
        hatalar.append(f"Ünite düzeni okunamadı: {e}")
    # Ünite düzeni tuttuysa ikinci okuyucuyu hiç çalıştırma (zaman kazancı)
    if [t for t in unite_testler if t.get("cevaplar") and t.get("numaralar")]:
        anahtar = {}
        for t in unite_testler:
            anahtar.setdefault(t["ders"], {})[t["test_no"]] = t["cevaplar"]
        return unite_testler, anahtar, unite_uyari + hatalar[:1]
    try:
        iq_testler, iq_anahtar, iq_uyari = _iq_testlerini_bul(pdf_path)
    except Exception as e:
        iq_testler, iq_anahtar, iq_uyari = [], {}, []
        hatalar.append(f"Test düzeni okunamadı: {e}")

    def _kullanilabilir(liste):
        return len([t for t in liste if t.get("cevaplar") and t.get("numaralar")])

    if _kullanilabilir(unite_testler) >= max(1, _kullanilabilir(iq_testler)):
        anahtar = {}
        for t in unite_testler:
            anahtar.setdefault(t["ders"], {})[t["test_no"]] = t["cevaplar"]
        return unite_testler, anahtar, unite_uyari + hatalar[:1]
    return iq_testler, iq_anahtar, iq_uyari + hatalar[:1]
