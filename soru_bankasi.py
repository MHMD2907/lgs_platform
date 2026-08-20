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

DERS_ADLARI = [
    "Türkçe",
    "Matematik",
    "Fen Bilimleri",
    "T.C. İnkılap Tarihi ve Atatürkçülük",
    "Din Kültürü ve Ahlak Bilgisi",
    "İngilizce",
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


def testleri_bul(pdf_path):
    """Soru bankasi PDF'ini tarar.

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
