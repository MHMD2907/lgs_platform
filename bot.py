"""
bot.py - Otomatik LGS/soru bankasi indirme modulu.

Sohbette konusulan "farkli sitelerden otomatik indirme" fikri, kirilgan ve
her an degisebilecek HTML sayfalarini kazimak (scraping) yerine, MEB'in
resmi EBA icerik sunucusundaki ONGORULEBILIR dosya adlandirma kalibina
dayanir. Arastirma sirasinda dogrulandi:

    https://cdn.eba.gov.tr/yardimcikaynaklar/{yil}/06/trlgs/a_{yil}_sozel.pdf
    https://cdn.eba.gov.tr/yardimcikaynaklar/{yil}/06/trlgs/a_{yil}_sayisal.pdf

(Masaustunuzdeki a_2026_sozel.pdf / a_2026_sayisal.pdf tam olarak bu
adresten inmis dosyalardi.) Bu kalip her yil icin GARANTI degildir (MEB
yapisini degistirebilir, eski yillarda farkli barindirma kullanilmis
olabilir) -- bu yuzden bot once bu deseni dener, basarisiz olursa admin'e
"manuel indir / manuel yukle" secenegini sunar. Ayrica herhangi bir siteden
bulunan DOGRUDAN pdf linkini indirebilen genel bir fonksiyon da vardir;
boylece "farkli sitelerden de" istegi, kirilgan bir HTML kazici yazmadan,
guvenli ve bakimi kolay bir sekilde karsilanir.
"""

import os
import re
import requests

# NOT: Bazi MEB okul siteleri (meb.k12.tr) alisilmadik tarayici kimligi
# gonderen istekleri reddediyor. Bu yuzden sirandan bir Chrome kimligi
# kullaniliyor.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_BASLIKLAR = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}
TIMEOUT = 20

# Yil basina denenecek resmi EBA CDN kaliplari (yeni kaliplar buraya eklenebilir)
EBA_URL_TEMPLATES = [
    "https://cdn.eba.gov.tr/yardimcikaynaklar/{yil}/06/trlgs/a_{yil}_{bolum}.pdf",
]

BOLUM_ADLARI = {"Sözel": "sozel", "Sayısal": "sayisal"}

# Gecmis yillarin kitapciklarini TEK BIR SAYFADA toplayan liste sayfasi;
# linkler MEB'in kendi sunucularina gidiyor. Yukaridaki EBA kalibi pratikte
# sadece en son yil icin calisiyor, eski yillarda 404 donuyordu ("2018-2026
# seçtim indirmedi" sorunu); asagidaki dogrulanmis adresler bunu kapatiyor.
LGS_SOURCE_PAGE = "https://www.sinav.com.tr/HaberDetay/genel/lgs-sorulari/27620"

# O sayfadan okunup dogrulanmis DOGRUDAN pdf adresleri. Sayfa degisse veya
# kapansa bile bu liste calismaya devam eder.
KNOWN_LGS_PDFS = {
    2025: {
        "Sözel": "https://karabukodm.meb.gov.tr/meb_iys_dosyalar/2025_07/01134332_2025sozelbolum.pdf",
        "Sayısal": "https://karabukodm.meb.gov.tr/meb_iys_dosyalar/2025_07/01134858_2025sayisalbolum.pdf",
    },
    2024: {
        "Sözel": "https://msyo.meb.k12.tr/meb_iys_dosyalar/61/02/727852/dosyalar/2024_06/03121642_2024sozelakitapcik.pdf",
        "Sayısal": "https://msyo.meb.k12.tr/meb_iys_dosyalar/61/02/727852/dosyalar/2024_06/03121642_2024sayisalakitapcik.pdf",
    },
    2023: {
        "Sözel": "https://msyo.meb.k12.tr/meb_iys_dosyalar/61/02/727852/dosyalar/2023_06/05095005_2023-LGS-SOZEL-KITAPCIGI.pdf",
        "Sayısal": "https://msyo.meb.k12.tr/meb_iys_dosyalar/61/02/727852/dosyalar/2023_06/05095020_2023-LGS-SAYISAL-KITAPCIGI.pdf",
    },
    2022: {
        "Sözel": "https://cdn.eba.gov.tr/icerik/lgs/2022_sozel_bolum_a_kitapcigi_ve_cevap_anahtari.pdf",
        "Sayısal": "https://cdn.eba.gov.tr/icerik/lgs/2022_sayisal_bolum_a_kitapcigi_ve_cevap_anahtari.pdf",
    },
    # 2021: MEB'in kendi haber sayfasindan alindi (liste sayfasinda 2021 yoktu,
    # bu yuzden "2018-2025" secildiginde sadece o yil atlanip ekranda hata
    # veriyordu). Kaynak: meb.gov.tr 06/06/2021 tarihli duyuru.
    2021: {
        "Sözel": "https://cdn.eba.gov.tr/icerik/lgs/2021_SOZEL_BOLUM_A_.pdf",
        "Sayısal": "https://cdn.eba.gov.tr/icerik/lgs/2021_SAYISAL_BOLUM_A_.pdf",
    },
    2020: {
        "Sözel": "https://www.meb.gov.tr/meb_iys_dosyalar/2020_06/21195531_2020_sozel_bolum_a.pdf",
        "Sayısal": "https://www.meb.gov.tr/meb_iys_dosyalar/2020_06/21195513_2020_sayisal_bolum_a.pdf",
    },
    2019: {
        "Sözel": "https://www.meb.gov.tr/meb_iys_dosyalar/2019_06/02125953_2019_SOZEL_BOLUM.pdf",
        "Sayısal": "https://www.meb.gov.tr/meb_iys_dosyalar/2019_06/02130019_2019_SAYISAL_BOLUM.pdf",
    },
    2018: {
        "Sözel": "https://odsgm.meb.gov.tr/meb_iys_dosyalar/2018_06/03153730_SYZEL_BYLYM_A_kitapYY.pdf",
        "Sayısal": "https://odsgm.meb.gov.tr/meb_iys_dosyalar/2018_06/03153730_SAYISAL_BYLYM_A_kitapYY.pdf",
    },
}

_TR_LOWER = str.maketrans("ĞÜŞİÖÇI", "gusioci")


def _normalize(text):
    return (text or "").translate(_TR_LOWER).lower()


def _classify_pdf_link(url, anchor_text=""):
    """Bir pdf linkinden (yil, bolum) cikarir; cikaramazsa (yil, None).

    Adres kaliplari yillara gore cok farkli oldugu icin (2025sozelbolum.pdf,
    2023-LGS-SOZEL-KITAPCIGI.pdf, .../2018_06/..._SYZEL_BYLYM_A_kitapYY.pdf
    gibi -- sonuncusunda Turkce karakterler bozulmus ve dosya adinda yil
    hic yok, yil sadece klasor adinda) hem adresin tamami hem de baglantinin
    yazisi birlikte taranir."""
    blob = _normalize(url + " " + anchor_text)
    m = re.search(r"(20[0-9]{2})", blob)
    if not m:
        return None, None
    year = int(m.group(1))
    if "sayisal" in blob:
        return year, "Sayısal"
    # "SÖZEL" bozulmus hallerini de yakala (SYZEL gibi)
    if "sozel" in blob or "syzel" in blob:
        return year, "Sözel"
    return year, None


def scrape_source_page(page_url=LGS_SOURCE_PAGE):
    """Liste sayfasini tarayip {yil: {"Sözel": url, "Sayısal": url}} dondurur.
    Sayfaya ulasilamazsa bos sozluk doner (asla hata firlatmaz)."""
    try:
        resp = requests.get(page_url, headers=_BASLIKLAR, timeout=TIMEOUT)
        if resp.status_code != 200:
            return {}
        html = resp.text
    except requests.exceptions.RequestException:
        return {}
    found = {}
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+\.pdf)["\'][^>]*>(.*?)</a>',
                         html, re.IGNORECASE | re.DOTALL):
        url = m.group(1)
        text = re.sub(r"<[^>]+>", " ", m.group(2))
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://www.sinav.com.tr" + url
        year, bolum = _classify_pdf_link(url, text)
        if year and bolum:
            found.setdefault(year, {}).setdefault(bolum, url)
    return found


def available_years():
    """Adresi kesin olarak elimizde olan yillar (menude gostermek icin)."""
    return sorted(KNOWN_LGS_PDFS.keys(), reverse=True)


# ------------------------------------------------------------------ BURSLULUK
# MEB'in "Bursluluk Sinavi Cikmis Sorular" sayfasi: her yil icin bir baslik
# ("2016 yili bursluluk sinav kitapciklari ve cevap anahtari") ve altinda
# sinif sinif baglantilar ("5. Sinif icin Tiklayiniz") bulunur.
BURSLULUK_SOURCE_PAGE = (
    "https://745183.meb.k12.tr/icerikler/bursluluksinavicikmissorular20162025_16928996.html"
)

# İOKBS'de her sinifta 4 ders x 25 soru = 100 soru vardir. 8. sinifta
# "Sosyal Bilgiler" yerine "T.C. Inkilap Tarihi ve Ataturkculuk" sorulur.
IOKBS_YAPISI = {
    5: [("Türkçe", 25), ("Matematik", 25), ("Fen Bilimleri", 25), ("Sosyal Bilgiler", 25)],
    6: [("Türkçe", 25), ("Matematik", 25), ("Fen Bilimleri", 25), ("Sosyal Bilgiler", 25)],
    7: [("Türkçe", 25), ("Matematik", 25), ("Fen Bilimleri", 25), ("Sosyal Bilgiler", 25)],
    8: [("Türkçe", 25), ("Matematik", 25), ("Fen Bilimleri", 25), ("İnkılap", 25)],
}


def scrape_bursluluk(page_url=BURSLULUK_SOURCE_PAGE, ayrinti=None):
    """Bursluluk sayfasindaki baglantilari {(yil, sinif): url} olarak dondurur.

    YONTEM: Sayfa "2016 yili ... kitapciklari", ardindan "5. Sinif icin
    Tiklayiniz", "6. Sinif icin Tiklayiniz" ... seklinde ilerliyor. Yani bir
    baglantinin HANGI YILA ait oldugu, kendisinden ONCE gelen en son yil
    basligindan anlasiliyor. Bu yuzden sayfa bastan sona SIRAYLA taraniyor.

    ayrinti: (varsa) icine hata/teshis bilgisi yazilan bir liste. Bos sonuc
    donunce sebebinin ne oldugunu ekranda gosterebilmek icin kullanilir --
    "ulasilamadi" demek yerine gercek sebebi soylemek gerekiyor.

    Hata olursa bos sozluk doner (asla istisna firlatmaz)."""
    def _not(m):
        if ayrinti is not None:
            ayrinti.append(m)

    try:
        resp = requests.get(page_url, headers=_BASLIKLAR, timeout=TIMEOUT,
                            allow_redirects=True)
        if resp.status_code != 200:
            _not(f"Sunucu HTTP {resp.status_code} yanıtı verdi ({page_url}).")
            return {}
        resp.encoding = resp.apparent_encoding or resp.encoding
        html = resp.text
        _not(f"Sayfa alındı ({len(html)} karakter).")
    except requests.exceptions.SSLError as e:
        _not(f"Güvenli bağlantı (SSL) kurulamadı: {e}")
        return {}
    except requests.exceptions.Timeout:
        _not(f"Sunucu {TIMEOUT} saniyede yanıt vermedi.")
        return {}
    except requests.exceptions.RequestException as e:
        _not(f"Bağlantı hatası: {e}")
        return {}

    taban = re.match(r"^(https?://[^/]+)", page_url)
    taban = taban.group(1) if taban else ""
    dizin = page_url.rsplit("/", 1)[0]

    def _tam_url(u):
        u = (u or "").strip()
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("/"):
            return taban + u
        if u.startswith("http"):
            return u
        return dizin + "/" + u

    # Yil basliklari ve baglantilari BELGE SIRASINDA birlikte tara
    desen = re.compile(
        r'(?P<link><a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<metin>.*?)</a>)'
        r"|(?P<yil>20[0-9]{2})\s*y[ıi]l[ıi]",
        re.IGNORECASE | re.DOTALL,
    )
    bulunan = {}
    aktif_yil = None
    for m in desen.finditer(html):
        if m.group("yil"):
            aktif_yil = int(m.group("yil"))
            continue
        if aktif_yil is None:
            continue
        metin = re.sub(r"<[^>]+>", " ", m.group("metin") or "")
        metin = re.sub(r"\s+", " ", metin).strip()
        sm = re.search(r"([5-9]|1[0-2])\s*\.?\s*s[ıi]n[ıi]f", _normalize(metin))
        if not sm:
            continue
        sinif = int(sm.group(1))
        bulunan.setdefault((aktif_yil, sinif), _tam_url(m.group("href")))
    if not bulunan:
        _toplam_link = len(re.findall(r"<a[^>]+href=", html, re.IGNORECASE))
        _yil_sayisi = len(re.findall(r"20[0-9]{2}\s*y[ıi]l[ıi]", html, re.IGNORECASE))
        _not(
            f"Sayfada {_toplam_link} bağlantı ve {_yil_sayisi} yıl başlığı görüldü, "
            "ama hiçbiri 'X. Sınıf' kalıbıyla eşleşmedi. Sayfanın yapısı değişmiş olabilir."
        )
    return bulunan


def sayfa_ham_getir(page_url=BURSLULUK_SOURCE_PAGE, limit=1500):
    """Teshis icin: sayfanin ham metninin bir kismini dondurur."""
    try:
        resp = requests.get(page_url, headers=_BASLIKLAR, timeout=TIMEOUT)
        return f"HTTP {resp.status_code}\n" + (resp.text or "")[:limit]
    except Exception as e:
        return f"HATA: {e}"


def bursluluk_indir(url, dest_path):
    """Bursluluk kitapciklarindan birini indirir (PDF oldugu dogrulanir)."""
    return _download(url, dest_path)


def _download(url, dest_path):
    try:
        resp = requests.get(url, headers=_BASLIKLAR, timeout=TIMEOUT, stream=True)
    except requests.exceptions.RequestException as e:
        return False, f"Bağlantı hatası: {e}"
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    content = resp.content
    if content[:4] != b"%PDF":
        return False, "İndirilen dosya PDF değil (site yapısı değişmiş olabilir)."
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(content)
    return True, "OK"


def candidate_urls(yil, bolum, scraped=None):
    """Bir yil+bolum icin denenecek adresleri, en guvenilirden en zayifa
    dogru siralar: (1) dogrulanmis sabit liste, (2) liste sayfasindan canli
    okunanlar, (3) EBA adres kalibi."""
    urls = []
    known = KNOWN_LGS_PDFS.get(yil, {}).get(bolum)
    if known:
        urls.append(known)
    if scraped:
        live = scraped.get(yil, {}).get(bolum)
        if live and live not in urls:
            urls.append(live)
    slug = BOLUM_ADLARI[bolum]
    for template in EBA_URL_TEMPLATES:
        u = template.format(yil=yil, bolum=slug)
        if u not in urls:
            urls.append(u)
    return urls


def fetch_lgs_year(yil, pdf_dir, scraped=None):
    """Belirtilen yil icin Sozel+Sayisal LGS kitapciklarini indirir.

    Once dogrulanmis sabit adres listesi, sonra liste sayfasindan canli
    okunan adresler, en son EBA adres kalibi denenir -- boylece hem eski
    yillar calisir, hem de ilerde yeni bir yil eklendiginde sayfa taramasi
    sayesinde kod degistirmeden bulunabilir.

    Donus: {"Sözel": path/None, "Sayısal": path/None, "hatalar": [...]}"""
    results = {}
    errors = []
    for bolum, slug in BOLUM_ADLARI.items():
        found = None
        for url in candidate_urls(yil, bolum, scraped):
            dest = os.path.join(pdf_dir, f"_indirilen_{yil}_{slug}.pdf")
            ok, msg = _download(url, dest)
            if ok:
                found = dest
                break
            errors.append(f"{bolum} ({url}): {msg}")
        results[bolum] = found
    results["hatalar"] = errors
    return results


def fetch_from_url(url, dest_path):
    """Admin panelinden yapistirilan herhangi bir DOGRUDAN pdf linkini indirir.
    (MEB'in farkli okul/il sayfalari, sorubak/tonguc gibi kaynak siteler vb.
    -- her siteyi otomatik taramak yerine, admin bulup linki yapistirir; bot
    indirip sisteme organize eder.)
    """
    if not re.match(r"^https?://", url):
        return False, "Geçerli bir http(s) linki değil."
    return _download(url, dest_path)
