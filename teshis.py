"""
teshis.py - "Kitabı tarıyorum ama dersler çıkmıyor" durumunu teşhis eder.

NE YAPAR: Verilen PDF'i sayfa sayfa gezip her sayfa için programın NE
GÖRDÜĞÜNÜ yazar (hangi ders, ünite başlığı var mı, cevap anahtarı mı,
merkezî sınav kapağı mı, sayfadaki soru numaraları). Sonuna da gerçek
tarama sonucunu ekler.

Çıktıyı `teshis_raporu.txt` dosyasına yazar. O dosya küçüktür (birkaç yüz
KB), rahatça paylaşılabilir -- 177 MB'lık kitabı göndermeye gerek kalmaz.

KULLANIM:  TESHIS.bat dosyasına çift tıklayın.
           (ya da:  python teshis.py lgs_sozel.pdf)
"""

import os
import sys
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _kisalt(s, n=26):
    s = str(s or "-")
    return s if len(s) <= n else s[: n - 1] + "…"


def main():
    try:
        import soru_bankasi as sb
        import pypdfium2 as pdfium
    except Exception as e:
        print("Gerekli kütüphaneler yüklenemedi:", e)
        input("Devam etmek için Enter'a basın...")
        return

    klasor = os.path.dirname(os.path.abspath(__file__))
    if len(sys.argv) > 1:
        hedefler = [sys.argv[1]]
    else:
        hedefler = sorted(glob.glob(os.path.join(klasor, "*.pdf")))
        hedefler = [h for h in hedefler if os.path.getsize(h) > 5 * 1024 * 1024]
        if not hedefler:
            print("Bu klasörde taranacak büyük bir PDF bulunamadı.")
            input("Devam etmek için Enter'a basın...")
            return
        print("Bu klasördeki büyük PDF'ler:")
        for i, h in enumerate(hedefler, start=1):
            print(f"  {i}) {os.path.basename(h)}  "
                  f"({os.path.getsize(h) / 1e6:.0f} MB)")
        sec = input("Hangisini inceleyelim? (numara, boş = 1): ").strip()
        try:
            hedefler = [hedefler[int(sec) - 1]] if sec else [hedefler[0]]
        except Exception:
            hedefler = [hedefler[0]]

    yol = hedefler[0]
    rapor = os.path.join(klasor, "teshis_raporu.txt")
    satirlar = []

    def yaz(s=""):
        print(s)
        satirlar.append(s)

    yaz("=" * 78)
    yaz(f"TESHIS RAPORU  -  {os.path.basename(yol)} "
        f"({os.path.getsize(yol) / 1e6:.1f} MB)")
    yaz(f"soru_bankasi.py surumu: {getattr(sb, 'SURUM', '?')}")
    yaz("=" * 78)

    doc = pdfium.PdfDocument(yol)
    toplam = len(doc)
    yaz(f"Toplam sayfa: {toplam}")
    yaz("")
    yaz("SAYFA SAYFA NE GORULDU")
    yaz("-" * 78)
    yaz(f"{'Sf':>4} | {'Ders':<26} | {'Tur':<9} | {'Unite':<22} | Soru no")
    yaz("-" * 78)

    ders_araliklari = []      # (ders, ilk_sayfa, son_sayfa)
    numarasiz = []            # (sayfa, bulunan_numara_sayisi)
    anahtar_sayfalari = []    # (sayfa, ders, unite_sayisi)
    merkezi_sayfalari = []
    su_ders = None
    ilk = None

    for i in range(toplam):
        if i % 25 == 0:
            print(f"  ... {i}/{toplam}", end="\r")
        try:
            metin, ust, sol = sb._sayfa_okumalari(doc, i)
        except Exception as e:
            yaz(f"{i + 1:>4} | !! sayfa okunamadi: {e}")
            continue
        duz = sb._duz(metin)
        duz_k = sb._duz(sb._kaydirmayi_coz(metin))
        harfler = sb._sadece_harfler(duz)
        harfler_k = sb._sadece_harfler(duz_k)

        toc = sb._icindekiler_mi(duz, duz_k)
        merkezi = (not toc) and sb._merkezi_kapak_mi(duz, duz_k)
        yazidan = (
            "CEVAPANAHTARI" in harfler or "CEVAPANAHTARI" in harfler_k
            or "ANSWERKEY" in harfler or "ANSWERKEY" in harfler_k
        )
        try:
            icerikten = sb._anahtar_sayfasi_mi(metin)
        except Exception:
            icerikten = False
        anahtar = (not toc) and (yazidan or icerikten)
        ders = sb._sayfa_dersi_metinden(duz, ust)
        uno, uad = sb._unite_basligi_metinden(metin)
        nums = sb._sol_serit_numaralarindan(sol, uno)

        tur = "GOVDE"
        if toc:
            tur = "ICINDEK"
        if merkezi:
            tur = "MERKEZI"
            merkezi_sayfalari.append(i + 1)
        if anahtar:
            tur = "ANAHTAR" if yazidan else "ANAHTAR*"   # * = icerikten anlasildi
            try:
                bloklar = sb._anahtar_bloklari(metin)
            except Exception:
                bloklar = {}
            anahtar_sayfalari.append(
                (i + 1, sb._anahtar_dersi(metin), len(bloklar),
                 sorted(bloklar.keys())[:12])
            )

        if ders and ders != su_ders:
            if su_ders is not None:
                ders_araliklari.append((su_ders, ilk, i))
            su_ders, ilk = ders, i + 1

        numarasiz.append((i + 1, len(nums)))
        unite = f"{uno}. {_kisalt(uad, 16)}" if uno else "-"
        yaz(f"{i + 1:>4} | {_kisalt(ders):<26} | {tur:<9} | {_kisalt(unite, 22):<22} | "
            f"{nums[:8]}")

    if su_ders is not None:
        ders_araliklari.append((su_ders, ilk, toplam))
    doc.close()
    print(" " * 40, end="\r")

    yaz("")
    yaz("=" * 78)
    yaz("OZET")
    yaz("=" * 78)
    yaz("")
    yaz("Taninan ders bolumleri (sayfa araliklari):")
    if ders_araliklari:
        for d, a, b in ders_araliklari:
            yaz(f"  - {d}: sayfa {a} - {b}")
    else:
        yaz("  (hicbir ders adi taninmadi -- ASIL SORUN BU OLABILIR)")
    _hic = [sf for sf, n in numarasiz if n == 0]
    yaz("")
    yaz(f"Soru numarasi HIC bulunamayan sayfa sayisi: {len(_hic)} / {toplam}")
    if _hic:
        yaz(f"  ornek sayfalar: {_hic[:25]}{' ...' if len(_hic) > 25 else ''}")
    yaz("")
    yaz(f"Merkezi sinav kapagi sanilan sayfalar: {merkezi_sayfalari or '(yok)'}")
    yaz("")
    yaz("Cevap anahtari sayfalari:")
    if anahtar_sayfalari:
        for sf, d, n, unite_nolari in anahtar_sayfalari:
            yaz(f"  - sayfa {sf}: ders={d}  blok={n}  uniteler={unite_nolari}")
    else:
        yaz("  (hic cevap anahtari sayfasi bulunamadi -- ASIL SORUN BU OLABILIR)")

    # ---- Secili sayfalarin HAM METNI ----
    # Kalan sorunlari (ozellikle soru numarasi bulunamayan bolumler)
    # uzaktan cozebilmek icin birkac sayfanin ham metni gerekiyor.
    ilgi = [sf for sf, _d, _n, _u in anahtar_sayfalari][:6]
    if len(sys.argv) > 2:
        ilgi = []
        for x in sys.argv[2:]:
            try:
                ilgi.append(int(x))
            except ValueError:
                pass
    else:
        # Soru numarasi hic bulunamayan en uzun bolgeden ornek sayfalar
        bos_bolge = [sf for sf, n in numarasiz if n == 0]
        for k in (0, len(bos_bolge) // 2, len(bos_bolge) - 1):
            if 0 <= k < len(bos_bolge) and bos_bolge[k] not in ilgi:
                ilgi.append(bos_bolge[k])
    ilgi = sorted(set(i for i in ilgi if 1 <= i <= toplam))[:12]
    if ilgi:
        yaz("")
        yaz("=" * 78)
        yaz("SECILI SAYFALARIN HAM METNI  (ilk 1200 karakter)")
        yaz("Bu bolum, programin o sayfada TAM OLARAK ne okudugunu gosterir.")
        yaz("=" * 78)
        doc2 = pdfium.PdfDocument(yol)
        for sf in ilgi:
            try:
                m, _u, _s = sb._sayfa_okumalari(doc2, sf - 1)
            except Exception as e:
                yaz(f"--- Sayfa {sf}: okunamadi ({e})")
                continue
            yaz("")
            yaz(f"--- SAYFA {sf} " + "-" * 60)
            yaz(repr(m[:1200]))
        doc2.close()

    yaz("")
    yaz("=" * 78)
    yaz("GERCEK TARAMA SONUCU")
    yaz("=" * 78)
    try:
        testler, anahtar, uyarilar = sb.testleri_bul(yol, parca_soru=0)
    except Exception as e:
        testler, uyarilar = [], [f"Tarama hata verdi: {e}"]
    yaz(f"Bulunan test sayisi: {len(testler)}")
    _dersler = {}
    for t in testler:
        _dersler.setdefault(t["ders"], []).append(t)
    for d, liste in _dersler.items():
        yaz(f"  {d}: {len(liste)} test")
        for t in liste[:40]:
            yaz(f"      - {t['konu']}  ({len(t.get('numaralar') or [])} soru, "
                f"sayfa {(t.get('sayfalar') or ['?'])[0]}-{(t.get('sayfalar') or ['?'])[-1]})")
    yaz("")
    yaz("Uyarilar:")
    for u in uyarilar or ["(yok)"]:
        yaz(f"  - {u}")

    with open(rapor, "w", encoding="utf-8") as f:
        f.write("\n".join(satirlar))
    print()
    print("=" * 60)
    print(f"Rapor yazildi: {rapor}")
    print("Bu dosyayi Claude'a gonderin.")
    print("=" * 60)
    input("Kapatmak icin Enter'a basin...")


if __name__ == "__main__":
    main()
