"""
scoring.py - LGS tarzi puanlama motoru.

Kurallar:
  - Her dogru cevap +1, her 3 yanlis 1 dogruyu goturur: net = dogru - yanlis/3
  - Bos birakilan sorular ne dogru ne yanlis sayilir.
  - "structure" icinde ders bazli katsayi (coef) varsa agirlikli net hesaplanir.

ONEMLI DURUSTLUK NOTU: MEB'in resmi LGS puanı (100-500 arası), o yılki
tüm Türkiye'nin ortalama ve standart sapmasına dayanan istatistiksel bir
donusumle hesaplanir; bu veri tek bir ogrencinin elinde olmaz. Bu yuzden
bu modul resmi bir "LGS puanı" UYDURMAZ; bunun yerine ders bazli net ve
katsayiyla agirliklandirilmis bir "gösterge puan" (tahmini, karsilastirma
amacli) verir. Arayuzde bu acikca "tahmini" olarak etiketlenmelidir.
"""

BOS_ISARETLERI = {"Boş", "boş", "BOŞ", "", None}


def score_subject(user_answers, key_answers):
    """Tek bir ders icin (liste, liste) -> dogru, yanlis, bos, net.

    ONEMLI - SESSIZ SORU KAYBI: Burada eskiden duz zip() kullaniliyordu.
    zip(), listelerden KISA olani bitince durur ve hic ses cikarmaz. Yani
    cevap anahtari 25 soru, ogrencinin formu 20 soru ise, kalan 5 soru
    PUANLAMAYA HIC GIRMIYOR -- net "dogru" gorunuyor ama yanlis oluyordu.
    Artik anahtar kac soruysa o kadar soru puanlanir; ogrencinin
    isaretlemedigi (eksik kalan) sorular BOS sayilir ve durum "uyari"
    alaninda bildirilir."""
    user_answers = list(user_answers or [])
    key_answers = list(key_answers or [])
    dogru = yanlis = bos = 0
    for i, k in enumerate(key_answers):
        u = user_answers[i] if i < len(user_answers) else "Boş"
        if u in BOS_ISARETLERI:
            bos += 1
        elif u == k:
            dogru += 1
        else:
            yanlis += 1
    net = round(dogru - yanlis / 3, 2)
    sonuc = {"dogru": dogru, "yanlis": yanlis, "bos": bos, "net": net}
    if len(user_answers) != len(key_answers):
        sonuc["uyari"] = (
            f"Cevap sayısı uyuşmuyor: optik formda {len(user_answers)}, "
            f"cevap anahtarında {len(key_answers)} soru var."
        )
    return sonuc


def score_exam(user_answers, answer_key, structure):
    """user_answers / answer_key: {"Sözel": {"Türkçe": [...], ...}, "Sayısal": {...}}
       structure: {"Sözel": {"Türkçe": {"count":20,"coef":4}, ...}, ...}

    Donus: per_subject (duz sozluk, ders adi -> sonuc), total_net, weighted_score
    """
    per_subject = {}
    total_net = 0.0
    weighted = 0.0
    has_coef = False

    for section, subjects in structure.items():
        for subject, meta in subjects.items():
            u = user_answers.get(section, {}).get(subject, [])
            k = answer_key.get(section, {}).get(subject, [])
            res = score_subject(u, k)
            per_subject[subject] = res
            total_net += res["net"]
            coef = meta.get("coef", 1)
            if coef and coef != 1:
                has_coef = True
            weighted += res["net"] * coef

    weighted_score = round(weighted, 2) if has_coef else None
    return per_subject, round(total_net, 2), weighted_score


def build_answer_detail(user_answers, answer_key, structure):
    """Soru bazli detay uretir -- admin panelinde 'hangi soruyu bildi/bilemedi'
    gorunumu icin. Donus: {"Sözel": {"Türkçe": [{"soru":1,"verilen":"A",
    "dogru_cevap":"A","durum":"dogru"}, ...]}, ...}"""
    detail = {}
    for section, subjects in structure.items():
        detail[section] = {}
        for subject, meta in subjects.items():
            u_list = user_answers.get(section, {}).get(subject, [])
            k_list = answer_key.get(section, {}).get(subject, [])
            # Soru bankasindan alinan testlerde sorular 1'den baslamayabilir
            # (kitapta o testin ilk sayfasi yoksa, sayfa 4. sorudan baslar).
            # Bu durumda "numbers" alaninda KITAPTAKI GERCEK soru numaralari
            # tutulur ve dokumde de o numaralar gosterilir; yoksa 1, 2, 3...
            numaralar = (meta or {}).get("numbers") or list(range(1, len(k_list) + 1))
            rows = []
            # score_subject ile AYNI kural: anahtar kac soruysa o kadar satir
            # uretilir; ogrencinin isaretlemedigi soru "Boş" sayilir. (Eskiden
            # ic ice zip kullaniliyordu ve eksik cevap dokumden de dusuyordu.)
            for _i, k in enumerate(k_list):
                i = numaralar[_i] if _i < len(numaralar) else _i + 1
                u = u_list[_i] if _i < len(u_list) else "Boş"
                if u in BOS_ISARETLERI:
                    durum = "bos"
                elif u == k:
                    durum = "dogru"
                else:
                    durum = "yanlis"
                rows.append({"soru": i, "verilen": u, "dogru_cevap": k, "durum": durum})
            detail[section][subject] = rows
    return detail


def empty_user_answers(structure):
    """Optik formun ilk hali icin bos cevap sozlugu uretir (hepsi 'Boş')."""
    out = {}
    for section, subjects in structure.items():
        out[section] = {}
        for subject, meta in subjects.items():
            out[section][subject] = ["Boş"] * meta["count"]
    return out
