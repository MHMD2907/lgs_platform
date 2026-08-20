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
    """Tek bir ders icin (liste, liste) -> dogru, yanlis, bos, net."""
    dogru = yanlis = bos = 0
    for u, k in zip(user_answers, key_answers):
        if u in BOS_ISARETLERI:
            bos += 1
        elif u == k:
            dogru += 1
        else:
            yanlis += 1
    net = round(dogru - yanlis / 3, 2)
    return {"dogru": dogru, "yanlis": yanlis, "bos": bos, "net": net}


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
            for i, (u, k) in zip(numaralar, zip(u_list, k_list)):
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
