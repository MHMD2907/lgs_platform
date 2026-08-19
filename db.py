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
    for cat in DEFAULT_CATEGORIES:
        c.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (cat,))
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
    salt, pw_hash = _hash_password(password)
    conn.execute(
        """INSERT INTO students (username, display_name, salt, password_hash, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (username, display_name, salt, pw_hash, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return True, "Hesap oluşturuldu."


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

def add_exam(title, category, pdf_path, structure, answer_key, source="manuel"):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO exams (title, category, source, pdf_path, structure, answer_key, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            title,
            category,
            source,
            pdf_path,
            json.dumps(structure, ensure_ascii=False),
            json.dumps(answer_key, ensure_ascii=False),
            datetime.now().isoformat(timespec="seconds"),
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
    conn = get_conn()
    conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
    conn.commit()
    conn.close()


# ---------- results ----------

def add_result(exam_id, student_name, per_subject, total_net, weighted_score):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO results (exam_id, student_name, per_subject, total_net, weighted_score, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            exam_id,
            student_name,
            json.dumps(per_subject, ensure_ascii=False),
            total_net,
            weighted_score,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    result_id = c.lastrowid
    conn.close()
    return result_id


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
