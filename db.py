"""
db.py - LGS Tablet Platformu veritabani katmani.

SQLite kullanir (dosya: lgs_platform.db). Uc tablo:
  - exams   : sisteme yuklenen her deneme/test (PDF yolu + gizli cevap anahtari JSON)
  - results : bir ogrencinin bir denemeyi cozup bitirmesinden dogan sonuc kaydi
  - categories: kategori tanimlari (8. Sinif LGS, 7. Sinif, 6. Sinif, IOKBS, Genel Soru Bankasi ...)

Cevap anahtari asla dogrudan arayuze/tablete gonderilmez; sadece bu modul
uzerinden okunup sunucu tarafinda (bu bilgisayarda) karsilastirilir.
"""

import sqlite3
import json
import os
import hashlib
import secrets
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lgs_platform.db")

DEFAULT_CATEGORIES = [
    "8. Sınıf (LGS)",
    "7. Sınıf",
    "6. Sınıf",
    "İOKBS (Bursluluk)",
    "Genel Soru Bankası",
]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS exams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            source TEXT DEFAULT 'manuel',
            pdf_path TEXT NOT NULL,
            structure TEXT NOT NULL,   -- JSON: {"Sözel": {"Türkçe": {"count":20,"coef":4}, ...}, "Sayısal": {...}}
            answer_key TEXT NOT NULL,  -- JSON: {"Sözel": {"Türkçe": ["A","B",...]}, "Sayısal": {...}}  (gizli)
            created_at TEXT NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL,
            student_name TEXT,
            per_subject TEXT NOT NULL,  -- JSON: {"Türkçe": {"dogru":18,"yanlis":1,"bos":1,"net":17.67}, ...}
            total_net REAL NOT NULL,
            weighted_score REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS categories (
            name TEXT PRIMARY KEY
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS students (
            username TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS admins (
            username TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    # Bir öğrenci bir sınavı bitirmeden ekrandan/uygulamadan ayrılırsa
    # (internet kesilmesi, tablet kapanması, sayfa yenilenmesi vb.) o ana
    # kadar işaretlediği cevapları burada tutuyoruz; öğrenci geri döndüğünde
    # "kaldığı yerden" devam edebiliyor. Sınav bitirilip puanlanınca bu satır silinir.
    c.execute(
        """CREATE TABLE IF NOT EXISTS in_progress (
            exam_id INTEGER NOT NULL,
            student_name TEXT NOT NULL,
            attempt_no INTEGER NOT NULL,
            answers_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (exam_id, student_name, attempt_no)
        )"""
    )
    for cat in DEFAULT_CATEGORIES:
        c.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (cat,))

    # Eski veritabanlarında olmayabilecek sütunları güvenli şekilde ekle
    # (var olan bir kuruluma dokunmadan yükseltme yapabilmek için).
    try:
        c.execute("ALTER TABLE results ADD COLUMN answers_detail TEXT")
    except sqlite3.OperationalError:
        pass  # sütun zaten var
    try:
        c.execute("ALTER TABLE exams ADD COLUMN pdf_path_original TEXT")
    except sqlite3.OperationalError:
        pass  # sütun zaten var
    try:
        c.execute("ALTER TABLE results ADD COLUMN attempt_no INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # sütun zaten var

    conn.commit()
    conn.close()


# ---------- students (şifreli öğrenci hesapları) ----------

def _hash_password(password, salt=None):
    """Şifreyi salt + sha256 ile hash'ler. Düz metin asla saklanmaz."""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return salt, digest


def create_student(username, display_name, password):
    username = (username or "").strip().lower().replace(" ", "_")
    display_name = (display_name or "").strip()
    if not username or not display_name or not password:
        return False, "Kullanıcı adı, ad soyad ve şifre boş olamaz."
    conn = get_conn()
    exists = conn.execute(
        "SELECT 1 FROM students WHERE username = ?", (username,)
    ).fetchone()
    if exists:
        conn.close()
        return False, "Bu kullanıcı adı zaten alınmış, başka bir tane deneyin."
    name_exists = conn.execute(
        "SELECT 1 FROM students WHERE LOWER(display_name) = LOWER(?)", (display_name,)
    ).fetchone()
    if name_exists:
        conn.close()
        return False, (
            f"'{display_name}' adında zaten kayıtlı bir hesap var. Aynı kişiyseniz "
            "mevcut hesapla giriş yapın; farklı kişiyseniz adınıza soyadınızın "
            "bir kısmını ya da bir rakam ekleyerek ayırt edici hale getirin."
        )
    salt, pw_hash = _hash_password(password)
    conn.execute(
        """INSERT INTO students (username, display_name, salt, password_hash, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (username, display_name, salt, pw_hash, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return True, "Hesap oluşturuldu."


def ensure_default_admin(username, display_name, password):
    """Admins tablosu boşsa, config.py'deki başlangıç bilgileriyle ilk yönetici
    hesabını oluşturur. Var olan bir yönetici hesabına ASLA dokunmaz -- yani
    admin panelinden şifre değiştirildikten sonra bu fonksiyon onu geri almaz."""
    username = (username or "admin").strip().lower().replace(" ", "_")
    conn = get_conn()
    exists = conn.execute("SELECT 1 FROM admins WHERE username = ?", (username,)).fetchone()
    if not exists:
        salt, pw_hash = _hash_password(password)
        conn.execute(
            """INSERT INTO admins (username, display_name, salt, password_hash, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (username, display_name or "Yönetici", salt, pw_hash, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    conn.close()


def verify_admin(username, password):
    username = (username or "").strip().lower().replace(" ", "_")
    if not username or not password:
        return None
    conn = get_conn()
    row = conn.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
    conn.close()
    if not row:
        return None
    _, pw_hash = _hash_password(password, row["salt"])
    if pw_hash == row["password_hash"]:
        return dict(row)
    return None


def change_admin_password(username, current_password, new_password):
    """Yöneticinin kendi şifresini değiştirmesi için -- mevcut şifreyi doğrular."""
    admin = verify_admin(username, current_password)
    if not admin:
        return False, "Mevcut şifre yanlış."
    if not new_password or len(new_password) < 4:
        return False, "Yeni şifre en az 4 karakter olmalı."
    salt, pw_hash = _hash_password(new_password)
    conn = get_conn()
    conn.execute(
        "UPDATE admins SET salt = ?, password_hash = ? WHERE username = ?",
        (salt, pw_hash, admin["username"]),
    )
    conn.commit()
    conn.close()
    return True, "Şifre güncellendi."


def rename_student(old_username, new_username):
    """Bir öğrencinin kullanıcı adını değiştirir (görünen ad aynı kalır).
    Sonuç geçmişi de (results.student_name) otomatik olarak yeni ada taşınır."""
    old_username = (old_username or "").strip().lower().replace(" ", "_")
    new_username = (new_username or "").strip().lower().replace(" ", "_")
    if not new_username:
        return False, "Yeni kullanıcı adı boş olamaz."
    conn = get_conn()
    exists_old = conn.execute("SELECT 1 FROM students WHERE username = ?", (old_username,)).fetchone()
    if not exists_old:
        conn.close()
        return False, "Bu öğrenci bulunamadı."
    if new_username != old_username:
        exists_new = conn.execute("SELECT 1 FROM students WHERE username = ?", (new_username,)).fetchone()
        if exists_new:
            conn.close()
            return False, "Bu kullanıcı adı zaten başka bir öğrenci tarafından kullanılıyor."
    conn.execute("UPDATE students SET username = ? WHERE username = ?", (new_username, old_username))
    conn.execute("UPDATE results SET student_name = ? WHERE student_name = ?", (new_username, old_username))
    conn.commit()
    conn.close()
    return True, "Kullanıcı adı güncellendi."


def change_admin_username(current_username, current_password, new_username):
    """Yöneticinin kullanıcı adını değiştirir -- mevcut şifreyle doğrular."""
    admin = verify_admin(current_username, current_password)
    if not admin:
        return False, "Mevcut şifre yanlış."
    new_username = (new_username or "").strip().lower().replace(" ", "_")
    if not new_username:
        return False, "Yeni kullanıcı adı boş olamaz."
    conn = get_conn()
    if new_username != admin["username"]:
        exists = conn.execute("SELECT 1 FROM admins WHERE username = ?", (new_username,)).fetchone()
        if exists:
            conn.close()
            return False, "Bu kullanıcı adı zaten kullanılıyor."
    conn.execute(
        "UPDATE admins SET username = ? WHERE username = ?", (new_username, admin["username"])
    )
    conn.commit()
    conn.close()
    return True, "Kullanıcı adı güncellendi."


def get_students():
    """Kayıtlı tüm öğrencileri (kullanıcı adı + görünen ad) döner -- admin panelinde listelemek için."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT username, display_name, created_at FROM students ORDER BY display_name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reset_student_password(username, new_password):
    """Admin tarafından bir öğrencinin şifresini sıfırlar (öğrenci unutursa)."""
    username = (username or "").strip().lower().replace(" ", "_")
    if not username or not new_password:
        return False, "Kullanıcı adı ve yeni şifre boş olamaz."
    if len(new_password) < 4:
        return False, "Şifre en az 4 karakter olmalı."
    conn = get_conn()
    exists = conn.execute(
        "SELECT 1 FROM students WHERE username = ?", (username,)
    ).fetchone()
    if not exists:
        conn.close()
        return False, "Bu kullanıcı adında bir öğrenci bulunamadı."
    salt, pw_hash = _hash_password(new_password)
    conn.execute(
        "UPDATE students SET salt = ?, password_hash = ? WHERE username = ?",
        (salt, pw_hash, username),
    )
    conn.commit()
    conn.close()
    return True, "Şifre güncellendi."


def verify_student(username, password):
    """Kullanıcı adı + şifre doğruysa öğrenci kaydını (dict) döner, değilse None."""
    username = (username or "").strip().lower().replace(" ", "_")
    if not username or not password:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM students WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    _, pw_hash = _hash_password(password, row["salt"])
    if pw_hash == row["password_hash"]:
        return dict(row)
    return None


# ---------- categories ----------

def get_categories():
    conn = get_conn()
    rows = conn.execute("SELECT name FROM categories ORDER BY rowid").fetchall()
    conn.close()
    return [r["name"] for r in rows]


def add_category(name):
    if not name:
        return
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()


# ---------- exams ----------

def add_exam(title, category, pdf_path, structure, answer_key, source="manuel", pdf_path_original=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO exams (title, category, source, pdf_path, structure, answer_key, created_at, pdf_path_original)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            title,
            category,
            source,
            pdf_path,
            json.dumps(structure, ensure_ascii=False),
            json.dumps(answer_key, ensure_ascii=False),
            datetime.now().isoformat(timespec="seconds"),
            pdf_path_original,
        ),
    )
    conn.commit()
    exam_id = c.lastrowid
    conn.close()
    return exam_id


def exam_exists(title, category):
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM exams WHERE title = ? AND category = ?", (title, category)
    ).fetchone()
    conn.close()
    return row is not None


def get_exams(category=None):
    conn = get_conn()
    if category:
        rows = conn.execute(
            "SELECT * FROM exams WHERE category = ? ORDER BY created_at DESC", (category,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM exams ORDER BY created_at DESC").fetchall()
    conn.close()
    exams = []
    for r in rows:
        d = dict(r)
        d["structure"] = json.loads(d["structure"])
        d["answer_key"] = json.loads(d["answer_key"])
        exams.append(d)
    return exams


def get_exam(exam_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["structure"] = json.loads(d["structure"])
    d["answer_key"] = json.loads(d["answer_key"])
    return d


def delete_exam(exam_id):
    """Sınavı veritabanından siler VE diskte kalan PDF dosyalarını da
    temizler (silmezsek dosyalar sunucuda gereksiz yer kaplamaya devam eder)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT pdf_path, pdf_path_original FROM exams WHERE id = ?", (exam_id,)
    ).fetchone()
    conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
    conn.commit()
    conn.close()
    if row:
        for key in ("pdf_path", "pdf_path_original"):
            p = row[key] if key in row.keys() else None
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


# ---------- results ----------

def add_result(exam_id, student_name, per_subject, total_net, weighted_score, answers_detail=None, attempt_no=0):
    """Her çağrı YENİ bir satır ekler (üzerine yazmaz) -- böylece aynı öğrenci
    aynı denemeyi 10 kere çözse bile 10 ayrı sonuç kaydı tutulur."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO results (exam_id, student_name, per_subject, total_net, weighted_score, created_at, answers_detail, attempt_no)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            exam_id,
            student_name,
            json.dumps(per_subject, ensure_ascii=False),
            total_net,
            weighted_score,
            datetime.now().isoformat(timespec="seconds"),
            json.dumps(answers_detail, ensure_ascii=False) if answers_detail is not None else None,
            attempt_no,
        ),
    )
    conn.commit()
    result_id = c.lastrowid
    conn.close()
    return result_id


def delete_result(result_id):
    """Bir sonuç kaydını siler. SADECE admin panelinden çağrılmalı --
    öğrenci arayüzünde bu işlev için hiçbir düğme yoktur."""
    conn = get_conn()
    conn.execute("DELETE FROM results WHERE id = ?", (result_id,))
    conn.commit()
    conn.close()


def get_result_for_attempt(exam_id, student_name, attempt_no):
    """Belirli bir deneme + öğrenci + deneme-numarası için tamamlanmış
    (puanlanmış) bir sonuç var mı diye bakar. Varsa döndürür, yoksa None."""
    if not student_name:
        return None
    conn = get_conn()
    row = conn.execute(
        """SELECT results.*, exams.title AS exam_title, exams.category AS category
           FROM results JOIN exams ON results.exam_id = exams.id
           WHERE results.exam_id = ? AND results.student_name = ? AND results.attempt_no = ?
           ORDER BY results.created_at DESC LIMIT 1""",
        (exam_id, student_name, attempt_no),
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["per_subject"] = json.loads(d["per_subject"])
    raw_detail = d.get("answers_detail")
    d["answers_detail"] = json.loads(raw_detail) if raw_detail else None
    return d


def get_current_attempt_no(exam_id, student_name):
    """Bir öğrenci bir denemeyi ilk kez mi açıyor, yoksa daha önce (bu
    tarayıcı oturumu kapansa/İnternet kesilse bile) kaldığı/bıraktığı bir
    yer mi var diye bakmak için kullanılır. En yüksek deneme-numarasını
    (tamamlanmış sonuçlar VEYA yarım kalmış "in_progress" kayıtları
    arasından) döndürür; hiçbiri yoksa 0 döner (ilk deneme)."""
    if not student_name:
        return 0
    conn = get_conn()
    r1 = conn.execute(
        "SELECT MAX(attempt_no) AS m FROM results WHERE exam_id = ? AND student_name = ?",
        (exam_id, student_name),
    ).fetchone()
    r2 = conn.execute(
        "SELECT MAX(attempt_no) AS m FROM in_progress WHERE exam_id = ? AND student_name = ?",
        (exam_id, student_name),
    ).fetchone()
    conn.close()
    m1 = r1["m"] if r1 and r1["m"] is not None else 0
    m2 = r2["m"] if r2 and r2["m"] is not None else 0
    return max(m1, m2)


def save_progress(exam_id, student_name, attempt_no, answers):
    """Öğrencinin o ana kadar işaretlediği cevapları kaydeder (üzerine
    yazarak); sayfa yenilense/internet kesilse bile 'kaldığı yerden'
    devam edebilmesi için kullanılır."""
    if not student_name:
        return
    conn = get_conn()
    conn.execute(
        """INSERT INTO in_progress (exam_id, student_name, attempt_no, answers_json, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(exam_id, student_name, attempt_no)
           DO UPDATE SET answers_json = excluded.answers_json, updated_at = excluded.updated_at""",
        (exam_id, student_name, attempt_no, json.dumps(answers, ensure_ascii=False),
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()


def load_progress(exam_id, student_name, attempt_no):
    """Daha önce kaydedilmiş yarım kalmış cevapları döndürür (yoksa None)."""
    if not student_name:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT answers_json FROM in_progress WHERE exam_id = ? AND student_name = ? AND attempt_no = ?",
        (exam_id, student_name, attempt_no),
    ).fetchone()
    conn.close()
    return json.loads(row["answers_json"]) if row else None


def clear_progress(exam_id, student_name, attempt_no):
    """Sınav bitirilip puanlandığında, artık gereksiz kalan 'yarım kalmış
    cevaplar' kaydını siler."""
    if not student_name:
        return
    conn = get_conn()
    conn.execute(
        "DELETE FROM in_progress WHERE exam_id = ? AND student_name = ? AND attempt_no = ?",
        (exam_id, student_name, attempt_no),
    )
    conn.commit()
    conn.close()


def get_results(student_name=None, exam_id=None):
    conn = get_conn()
    query = """SELECT results.*, exams.title AS exam_title, exams.category AS category
               FROM results JOIN exams ON results.exam_id = exams.id WHERE 1=1"""
    params = []
    if student_name:
        query += " AND results.student_name = ?"
        params.append(student_name)
    if exam_id:
        query += " AND results.exam_id = ?"
        params.append(exam_id)
    query += " ORDER BY results.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["per_subject"] = json.loads(d["per_subject"])
        raw_detail = d.get("answers_detail")
        d["answers_detail"] = json.loads(raw_detail) if raw_detail else None
        out.append(d)
    return out


def clear_results_for_exam(exam_id, student_name=None):
    """'Testi Sıfırla' -- bir denemenin gecmis sonuclarini siler ki ogrenci sifirdan cozebilsin."""
    conn = get_conn()
    if student_name:
        conn.execute(
            "DELETE FROM results WHERE exam_id = ? AND student_name = ?",
            (exam_id, student_name),
        )
    else:
        conn.execute("DELETE FROM results WHERE exam_id = ?", (exam_id,))
    conn.commit()
    conn.close()
