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

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LGS-Egitim-Platformu/1.0"
TIMEOUT = 20

# Yil basina denenecek resmi EBA CDN kaliplari (yeni kaliplar buraya eklenebilir)
EBA_URL_TEMPLATES = [
    "https://cdn.eba.gov.tr/yardimcikaynaklar/{yil}/06/trlgs/a_{yil}_{bolum}.pdf",
]

BOLUM_ADLARI = {"Sözel": "sozel", "Sayısal": "sayisal"}


def _download(url, dest_path):
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, stream=True)
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


def fetch_lgs_year(yil, pdf_dir):
    """Belirtilen yil icin Sozel+Sayisal LGS kitapciklarini resmi EBA
    adresinden indirmeyi dener. Donus: {"Sözel": path/None, "Sayısal": path/None, "hatalar": [...]}
    """
    results = {}
    errors = []
    for bolum, slug in BOLUM_ADLARI.items():
        found = None
        for template in EBA_URL_TEMPLATES:
            url = template.format(yil=yil, bolum=slug)
            dest = os.path.join(pdf_dir, f"_indirilen_{yil}_{slug}.pdf")
            ok, msg = _download(url, dest)
            if ok:
                found = dest
                break
            else:
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
