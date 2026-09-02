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
import threading
import re
import os
import hashlib
import secrets
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lgs_platform.db")

# Dosya surumu -- app.py bunu okuyup "hepsi ayni surumde mi" diye bakar.
SURUM = "2026-09-02.5"

DEFAULT_CATEGORIES = [
    "8. Sınıf (LGS)",
    "7. Sınıf",
    "6. Sınıf",
    "İOKBS (Bursluluk)",
    "Genel Soru Bankası",
]


# =====================================================================
#  VERİTABANI BAĞLANTISI - İKİ FARKLI YERDE ÇALIŞABİLİR
#
#  NEDEN: Streamlit'in ücretsiz bulut sunucusunda, uygulamanın kendi
#  klasörüne yazılan dosyalar KALICI DEĞİLDİR. Uygulama her güncellemede
#  veya bir süre kullanılmayıp uykuya daldıktan sonra yeniden başlarken
#  sıfırdan kurulur ve o klasördeki her şey silinir. Yani veritabanı
#  dosyası (öğrenci hesapları, sınav sonuçları) kaybolur.
#
#  ÇÖZÜM: Veriler, uygulamanın dışındaki kalıcı bir veritabanında tutulur
#  (Supabase - ücretsiz PostgreSQL). Bağlantı adresi Streamlit'in gizli
#  "Secrets" kutusuna DB_URL adıyla yazılır; kod onu oradan okur, adres
#  hiçbir zaman GitHub'a girmez.
#
#  DB_URL tanımlıysa   -> Supabase (PostgreSQL), veriler kalıcı
#  DB_URL tanımlı değilse -> bilgisayardaki lgs_platform.db (eskisi gibi)
#
#  Böylece kendi bilgisayarınızda hiçbir kurulum yapmadan çalışmaya
#  devam eder; bulutta ise veriler artık silinmez.
# =====================================================================

def _db_url():
    """Streamlit'in Secrets kutusundan (ya da ortam değişkeninden) kalıcı
    veritabanı adresini okur. Yoksa None döner -> yerel dosya kullanılır."""
    url = os.environ.get("DB_URL")
    if url:
        return url.strip()
    try:
        import streamlit as st

        return (st.secrets.get("DB_URL") or "").strip() or None
    except Exception:
        return None


class _PgCursor:
    """psycopg imlecini, kodun geri kalanının beklediği sqlite3 arayüzüne
    benzetir: '?' yer tutucularını '%s' yapar ve satırları sözlük döndürür."""

    def __init__(self, owner):
        self._owner = owner
        # ÖNEMLİ - ARADA BİR ÇIKAN HATA: Supabase, uzun süre kullanılmayan
        # bağlantıyı kendi tarafından kapatıyor. Bizim tarafımızda bağlantı
        # hâlâ "açık" göründüğü için hata execute'ta değil, DAHA ÖNCE,
        # imleç açılırken patlıyordu -- ve oradaki yeniden bağlanma
        # koruması devreye girmiyordu. Bu yüzden burada da bir kez yeniden
        # bağlanıp deniyoruz. (Kullanıcıya yansıması: program bir süre
        # kullanılmadan bekletilip tekrar dokunulduğunda kırmızı hata.)
        try:
            self._cur = owner._raw().cursor()
        except Exception as e:
            if not _baglanti_kopmus(e):
                raise
            owner._reset()
            self._cur = owner._raw().cursor()

    def execute(self, sql, params=()):
        try:
            self._cur.execute(_to_pg(sql), tuple(params))
        except Exception as e:
            # Uzun süre kullanılmayan bağlantıyı sunucu kapatmış olabilir.
            # Böyle bir durumda sessizce yeni bağlantı kurup bir kez daha dene.
            if _baglanti_kopmus(e):
                self._owner._reset()
                self._cur = self._owner._raw().cursor()
                self._cur.execute(_to_pg(sql), tuple(params))
                return self
            # ÖNEMLİ - "InFailedSqlTransaction" HATASININ KÖK NEDENİ:
            # PostgreSQL'de bir komut hata verdiğinde, O BAĞLANTIDAKİ TÜM
            # İŞLEM iptal olur ve arkasından gelen HER komut
            # "current transaction is aborted" hatası verir. Yani tek bir
            # başarısız sorgu, bağlantıyı ZEHİRLİYOR: kullanıcı bambaşka bir
            # sayfaya gitse bile uygulama çöküyordu. (Kullanıcının gördüğü
            # ekran tam olarak buydu: hata "get_students" satırında patladı
            # ama asıl bozulan komut çok daha önce çalışmıştı.)
            #
            # Çözüm: hatalı komuttan hemen sonra işlemi geri al. Böylece
            # bağlantı temiz kalır, hata sadece o komutu etkiler.
            try:
                self._owner._raw().rollback()
            except Exception:
                self._owner._reset()
            raise
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def lastrowid(self):
        # PostgreSQL'de lastrowid yoktur; INSERT'lere "RETURNING id"
        # eklendiği için son dönen satırdan okunur.
        try:
            row = self._cur.fetchone()
            return row["id"] if row else None
        except Exception:
            return None

    def close(self):
        self._cur.close()


def _baglanti_kopmus(e):
    """Hata, bağlantının kopmasından mı kaynaklanıyor?"""
    ad = type(e).__name__
    return ad in ("OperationalError", "InterfaceError", "AdminShutdown",
                  "ConnectionTimeout", "ConnectionException")


# ÖNEMLİ - HIZ: Uygulamanın her ekran yenilemesinde veritabanına ONLARCA kez
# başvuruluyor (kategoriler, denemeler, sonuçlar, sayaçlar...). Kendi
# bilgisayarındaki dosyaya bağlanmak bedavaydı; ama Frankfurt'taki sunucuya
# HER SEFERİNDE yeni bir bağlantı açmak, her biri için ayrı ayrı el sıkışma
# (TCP + TLS) demek ve tek bir tuşa basmayı saniyelerce uzatıyordu.
# Çözüm: bağlantı bir kez açılıp saklanıyor, sonraki tüm sorgular AYNI
# bağlantıyı kullanıyor. Her iş parçacığı (kullanıcı oturumu) için ayrı bir
# bağlantı tutulur; psycopg bağlantıları aynı anda paylaşılmaya uygun değildir.
_yerel = threading.local()


class _PgConn:
    """Havuzlanmış PostgreSQL bağlantısı. close() çağrıldığında bağlantı
    GERÇEKTEN kapatılmaz; sadece açık kalmış okuma işlemi geri alınıp
    bağlantı bir sonraki sorgu için hazır bırakılır."""

    def _raw(self):
        conn = getattr(_yerel, "conn", None)
        if conn is not None:
            try:
                if conn.closed:
                    conn = None
            except Exception:
                conn = None
        if conn is None:
            conn = _yeni_pg_baglantisi()
            _yerel.conn = conn
        return conn

    def _reset(self):
        eski = getattr(_yerel, "conn", None)
        _yerel.conn = None
        if eski is not None:
            try:
                eski.close()
            except Exception:
                pass

    def cursor(self):
        return _PgCursor(self)

    def execute(self, sql, params=()):
        return self.cursor().execute(sql, params)

    def commit(self):
        try:
            self._raw().commit()
        except Exception as e:
            if not _baglanti_kopmus(e):
                raise
            self._reset()

    def close(self):
        # Bağlantıyı kapatmıyoruz; sadece açıkta kalan okuma işlemini
        # sonlandırıyoruz ki sunucuda "boşta işlem" birikmesin.
        try:
            self._raw().rollback()
        except Exception:
            self._reset()


def _to_pg(sql):
    """SQLite yazımını PostgreSQL yazımına çevirir."""
    sql = sql.replace("?", "%s")
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    # "INSERT OR IGNORE INTO t (...) VALUES (...)" -> ON CONFLICT DO NOTHING
    if sql.strip().upper().startswith("INSERT OR IGNORE"):
        sql = sql.replace("INSERT OR IGNORE", "INSERT", 1).rstrip().rstrip(";")
        sql += " ON CONFLICT DO NOTHING"
    # ÖNEMLİ: Sürüm yükseltmelerinde "ALTER TABLE ... ADD COLUMN" komutları
    # sütun zaten varsa hata verir. SQLite'ta bu hata yakalanıp geçiliyordu;
    # ama PostgreSQL'de HATA VEREN BİR KOMUT TÜM İŞLEMİ İPTAL ETTİĞİ için
    # arkasından gelen komutlar da çalışmıyordu. "IF NOT EXISTS" ekleyerek
    # hata hiç oluşmuyor.
    if re.match(r"^\s*ALTER\s+TABLE\s+\S+\s+ADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS)",
                sql, re.IGNORECASE):
        sql = re.sub(r"(ADD\s+COLUMN)\s+", r"\1 IF NOT EXISTS ", sql, count=1,
                     flags=re.IGNORECASE)
    # PostgreSQL'de ikili (binary) veri sütununun adı BYTEA'dir.
    sql = re.sub(r"\bBLOB\b", "BYTEA", sql)
    # PostgreSQL'de REAL/TEXT zaten var; PRAGMA yok sayılır.
    return sql


def _yeni_pg_baglantisi():
    url = _db_url()
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as e:
        raise RuntimeError(
            "Kalıcı veritabanı için gerekli 'psycopg' kütüphanesi kurulu değil. "
            "requirements.txt dosyasını da GitHub'a yükleyip uygulamayı yeniden "
            "başlatın."
        ) from e
    # ONEMLI - prepare_threshold=None: bkz. asagidaki aciklama.
    try:
        return psycopg.connect(
            url, row_factory=dict_row, autocommit=False,
            prepare_threshold=None, connect_timeout=15,
        )
    except Exception as e:
        raise RuntimeError(
            "Kalıcı veritabanına bağlanılamadı. Streamlit'in Secrets bölümündeki "
            f"DB_URL adresini kontrol edin (şifre kısmı doğru mu?). Hata: {e}"
        ) from e


def get_conn():
    """Veritabanı bağlantısı döndürür.

    DB_URL varsa PostgreSQL (Supabase) -- bağlantı tekrar kullanılır, her
    çağrıda yeniden kurulmaz (bkz. _PgConn üstündeki HIZ notu).
    DB_URL yoksa bilgisayardaki SQLite dosyası."""
    if _db_url():
        conn = _PgConn()
        conn._raw()  # bağlantıyı şimdi kur ki hata varsa hemen görülsün
        return conn
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def is_kalici():
    """Veriler kalıcı bir veritabanında mı tutuluyor? (arayüzde göstermek için)"""
    return _db_url() is not None


# =====================================================================
#  OKUMA ÖNBELLEĞİ
#
#  NEDEN: Streamlit her tıklamada sayfanın TAMAMINI baştan çalıştırır.
#  Ölçüldü: tek bir tıklamada ~17 ayrı sorgu Frankfurt'a gidip geliyordu
#  (kategoriler, denemeler, sayaçlar, sonuçlar, ayarlar...). Her sorgu
#  yaklaşık 50 ms gidiş-dönüş demek; toplamda her tuşa basış ~1 saniye
#  boşuna bekleme. Kendi bilgisayarındaki dosyada bu süre sıfırdı, o yüzden
#  Supabase'e geçince yavaşlık ortaya çıktı.
#
#  ÇÖZÜM: Sık okunan ve nadiren değişen bilgiler kısa süreliğine bellekte
#  tutulur. Veriyi DEĞİŞTİREN her işlem (ekleme/silme/güncelleme) önbelleği
#  komple temizler; böylece ekranda asla eski bilgi kalmaz.
# =====================================================================
_ONBELLEK = {}
# ÖNEMLİ - HIZ: Önbellek süresi 20 saniyeydi; 60 saniyeye çıkarıldı.
# Veriyi DEĞİŞTİREN her işlem (deneme ekleme, sonuç kaydetme, öğrenci
# ekleme...) önbelleği zaten anında temizlediği için ekranda eski bilgi
# kalma riski yoktur; bu süre sadece "hiçbir şey değişmediyse aynı soruyu
# Frankfurt'taki sunucuya tekrar tekrar sorma" anlamına gelir.
_ONBELLEK_SURESI = 60  # saniye
_onbellek_kilit = threading.Lock()


def _onbellek_temizle():
    with _onbellek_kilit:
        _ONBELLEK.clear()


def _onbellekli(fn):
    """Okuma fonksiyonlarını kısa süreli önbelleğe alır."""
    import functools
    import time as _t

    @functools.wraps(fn)
    def sarmal(*args, **kwargs):
        anahtar = (fn.__name__, args, tuple(sorted(kwargs.items())))
        simdi = _t.time()
        with _onbellek_kilit:
            kayit = _ONBELLEK.get(anahtar)
            if kayit and (simdi - kayit[0]) < _ONBELLEK_SURESI:
                return kayit[1]
        sonuc = fn(*args, **kwargs)
        with _onbellek_kilit:
            _ONBELLEK[anahtar] = (simdi, sonuc)
        return sonuc

    return sarmal


def _yazma(fn):
    """Veriyi DEĞİŞTİREN fonksiyonlar için: iş bitince okuma önbelleğini
    komple temizler, böylece ekranda asla eski bilgi kalmaz."""
    import functools

    @functools.wraps(fn)
    def sarmal(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            _onbellek_temizle()

    return sarmal


def _insert_id(c, sql, params):
    """Bir INSERT yapıp yeni satırın id'sini döndürür.

    SQLite'ta bu bilgi cursor.lastrowid'den gelir; PostgreSQL'de böyle bir
    şey olmadığı için sorguya "RETURNING id" eklenip sonuç okunur. Bu
    fonksiyon iki durumu da tek yerde hallediyor ki çağıran kodun hangi
    veritabanında olduğunu bilmesine gerek kalmasın."""
    if isinstance(c, _PgCursor):
        c.execute(sql.rstrip().rstrip(";") + " RETURNING id", params)
        row = c.fetchone()
        return row["id"] if row else None
    c.execute(sql, params)
    return c.lastrowid


@_yazma
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

    # ÖNEMLİ: Şemayı adım adım kaydediyoruz. PostgreSQL'de tek bir komutun
    # hata vermesi TÜM işlemi iptal ettiği için, hepsini tek bir commit'e
    # bırakmak "bir tablo oluşamadı diye hiçbiri oluşmadı" demek olurdu.
    conn.commit()

    # Eski veritabanlarında olmayabilecek sütunları güvenli şekilde ekle
    # (var olan bir kuruluma dokunmadan yükseltme yapabilmek için).
    try:
        c.execute("ALTER TABLE results ADD COLUMN answers_detail TEXT")
    except Exception:
        pass  # sütun zaten var (SQLite hata verir, PostgreSQL'de IF NOT EXISTS ile hiç oluşmaz)
    try:
        c.execute("ALTER TABLE exams ADD COLUMN pdf_path_original TEXT")
    except Exception:
        pass  # sütun zaten var (SQLite hata verir, PostgreSQL'de IF NOT EXISTS ile hiç oluşmaz)
    try:
        c.execute("ALTER TABLE results ADD COLUMN attempt_no INTEGER DEFAULT 0")
    except Exception:
        pass  # sütun zaten var (SQLite hata verir, PostgreSQL'de IF NOT EXISTS ile hiç oluşmaz)
    # "tam" = sinavin tamami cozuldu, "yanlis" = sadece onceki yanlis/bos
    # birakilan sorular tekrar cozuldu ("ikinci sans" modu).
    try:
        c.execute("ALTER TABLE results ADD COLUMN mode TEXT DEFAULT 'tam'")
    except Exception:
        pass  # sütun zaten var (SQLite hata verir, PostgreSQL'de IF NOT EXISTS ile hiç oluşmaz)

    # ÖNEMLİ - "PDF DOSYASI SUNUCUDA BULUNAMADI" HATASININ ÇÖZÜMÜ:
    # Streamlit'in ücretsiz bulut sunucusunda uygulama klasörüne yazılan
    # DOSYALAR her yeniden başlatmada/güncellemede silinir. Sınav kayıtları
    # veritabanında (Supabase) durduğu için listede görünüyor, ama PDF'i
    # diskten silindiği için açılamıyordu. Artık her denemenin PDF'i de
    # veritabanında saklanıyor; dosya bulunamazsa buradan geri yazılıyor.
    c.execute(
        """CREATE TABLE IF NOT EXISTS exam_files (
            exam_id INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            data BLOB NOT NULL,
            boyut INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    # Otomatik indirilen kitapçıklarda dosyayı veritabanında saklamak yerine
    # kaynak adresi saklamak yeterli: dosya kaybolursa yeniden indirilir.
    try:
        c.execute("ALTER TABLE exams ADD COLUMN source_url TEXT")
    except Exception:
        pass  # sütun zaten var
    # KULLANICI ADININ YAZILDIĞI HÂLİ: "username" giriş anahtarıdır ve
    # küçük harfe indirgenir (bkz. kullanici_adi_duzelt). Ama yönetici
    # "E.M.ONUR" yazınca ekranda "e.m.onur" görmek istemiyor. Yazıldığı
    # hâl burada ayrıca saklanıp SADECE gösterimde kullanılıyor; giriş,
    # sonuçlar ve tüm sorgular eskisi gibi küçük harfli anahtarla çalışır.
    for _t in ("students", "admins"):
        try:
            c.execute(f"ALTER TABLE {_t} ADD COLUMN username_yazim TEXT")
        except Exception:
            pass  # sütun zaten var

    conn.commit()

    # Uygulama ayarlari (ornegin rapor PIN kodu) icin basit anahtar-deger tablosu
    c.execute(
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )"""
    )

    conn.commit()
    conn.close()

    # Eski kurulumlarda bozuk yazımla ("EMİR" -> "emi̇r") kaydedilmiş
    # kullanıcı adlarını yeni standarda taşı. Düzeltilecek bir şey yoksa
    # hiçbir maliyeti yok.
    try:
        kullanici_adlarini_duzelt()
    except Exception:
        pass


# ---------- students (şifreli öğrenci hesapları) ----------

# Kullanıcı adında İ/I/ı/i harflerinin hepsi "i" sayılır.
_KADI_KATLA = str.maketrans({"İ": "i", "I": "i", "ı": "i", "i": "i"})


def kullanici_adi_duzelt(s):
    """Kullanıcı adını TEK BİR standart yazıma indirger.

    NEDEN: Kullanıcı adı GİRİŞ ANAHTARIDIR; çocuk tablette büyük/küçük
    harfe dikkat etmek zorunda kalmasın diye küçük harfe çevriliyor.

    ÖNEMLİ - TÜRKÇE "İ" TUZAĞI: Python'da "EMİR".lower() sonucu "emir"
    DEĞİLDİR; noktalı İ küçültülünce "i" + görünmez bir "nokta" işareti
    olur (5 harflik bir metin). Yani yönetici "EMİR" yazıp kaydediyor,
    çocuk "emir" yazıp giriş yapmaya çalışıyor ve "kullanıcı bulunamadı"
    diyordu. Artık İ, I, ı, i harflerinin dördü de düz "i" sayılıyor;
    hangisini yazarsanız yazın aynı hesaba düşüyor.

    NOT: Ekranda görünen ad (Ad Soyad) bundan ETKİLENMEZ; o, yazdığınız
    gibi büyük harfleriyle saklanır."""
    s = (s or "").strip().translate(_KADI_KATLA).lower().replace(" ", "_")
    # "İ".lower() geriye görünmez bir "üstte nokta" işareti (U+0307)
    # bırakır. Ekranda hiçbir şey görünmez ama metin eşleşmez; temizlenir.
    return s.replace("̇", "")


@_yazma
def kullanici_adlarini_duzelt():
    """Eski kurulumlarda bozuk yazımla kaydedilmiş kullanıcı adlarını
    yeni standarda taşır (bkz. kullanici_adi_duzelt). Bir kez çalışır,
    düzeltilecek bir şey yoksa hiçbir şey yapmaz."""
    conn = get_conn()
    tasinan = 0
    try:
        for tablo in ("students", "admins"):
            try:
                satirlar = conn.execute(
                    f"SELECT username FROM {tablo}").fetchall()
            except Exception:
                continue
            mevcut = {s["username"] for s in satirlar}
            for s in satirlar:
                eski = s["username"]
                yeni = kullanici_adi_duzelt(eski)
                if yeni == eski or not yeni or yeni in mevcut:
                    continue
                conn.execute(f"UPDATE {tablo} SET username = ? WHERE username = ?",
                             (yeni, eski))
                mevcut.discard(eski)
                mevcut.add(yeni)
                tasinan += 1
                if tablo != "students":
                    continue
                for _t, _s in (("results", "student_name"),
                               ("in_progress", "student_name")):
                    try:
                        conn.execute(f"UPDATE {_t} SET {_s} = ? WHERE {_s} = ?",
                                     (yeni, eski))
                    except Exception:
                        pass
                try:
                    for _a in conn.execute(
                            "SELECT key FROM settings WHERE key LIKE ?",
                            ("wrongmode:%",)).fetchall():
                        if f":{eski}:" in _a["key"]:
                            _yeni_a = _a["key"].replace(f":{eski}:", f":{yeni}:", 1)
                            conn.execute("DELETE FROM settings WHERE key = ?",
                                         (_yeni_a,))
                            conn.execute("UPDATE settings SET key = ? WHERE key = ?",
                                         (_yeni_a, _a["key"]))
                except Exception:
                    pass
        conn.commit()
    finally:
        conn.close()
    return tasinan


def _hash_password(password, salt=None):
    """Şifreyi salt + sha256 ile hash'ler. Düz metin asla saklanmaz."""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return salt, digest


@_yazma
def create_student(username, display_name, password):
    _yazim = (username or "").strip().replace(" ", "_")
    username = kullanici_adi_duzelt(username)
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
        """INSERT INTO students (username, username_yazim, display_name,
                                salt, password_hash, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (username, _yazim, display_name, salt, pw_hash,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return True, "Hesap oluşturuldu."


@_yazma
def ensure_default_admin(username, display_name, password):
    """Admins tablosu boşsa, config.py'deki başlangıç bilgileriyle ilk yönetici
    hesabını oluşturur. Var olan bir yönetici hesabına ASLA dokunmaz -- yani
    admin panelinden şifre değiştirildikten sonra bu fonksiyon onu geri almaz."""
    username = kullanici_adi_duzelt(username or "admin")
    conn = get_conn()
    # ÖNEMLİ - "DEĞİŞTİRDİĞİM ESKİ YÖNETİCİ ADI GERİ GELİYOR": Burada
    # eskiden SADECE bu kullanıcı adı var mı diye bakılıyordu. Yönetici,
    # panelden kullanıcı adını değiştirdiğinde config.py'deki eski ad
    # artık tabloda bulunmuyor ve program her açılışta o eski hesabı
    # VARSAYILAN ŞİFREYLE yeniden oluşturuyordu. Yani değiştirdiğiniz
    # kullanıcı adı+şifre yanınızda dururken, eski "admin" hesabı da
    # arka kapı gibi açık kalıyordu.
    # Artık kural şu: tabloda HERHANGİ bir yönetici varsa hiçbir şey
    # yapılmaz. İlk hesap yalnızca tablo bomboşken kurulur.
    _var_mi = conn.execute("SELECT 1 FROM admins LIMIT 1").fetchone()
    exists = _var_mi
    if not exists:
        salt, pw_hash = _hash_password(password)
        conn.execute(
            """INSERT INTO admins (username, display_name, salt, password_hash, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (username, display_name or "Yönetici", salt, pw_hash, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    conn.close()


@_onbellekli
def admin_var_mi(username):
    """Bu kullanıcı adında bir yönetici hesabı var mı?

    Giriş ekranı, kullanıcı adı kutusunu buna göre dolduruyor:
    config.py'deki başlangıç adı artık kullanılmıyorsa (yönetici adını
    değiştirmişse) kutu BOŞ açılıyor -- yoksa ekranda olmayan bir ad
    yazılı duruyor ve "şifrem çalışmıyor" sanılıyor."""
    if not username:
        return False
    conn = get_conn()
    try:
        return bool(conn.execute(
            "SELECT 1 FROM admins WHERE username = ?",
            (kullanici_adi_duzelt(username),)).fetchone())
    finally:
        conn.close()


def verify_admin(username, password):
    username = kullanici_adi_duzelt(username)
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


@_yazma
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


@_yazma
def rename_student(old_username, new_username):
    """Bir öğrencinin kullanıcı adını değiştirir (görünen ad aynı kalır).
    Sonuç geçmişi de (results.student_name) otomatik olarak yeni ada taşınır."""
    # Yönetici "E.M.ONUR" yazdıysa ekranda öyle görünsün: yazıldığı hâl
    # ayrıca saklanıyor. Giriş anahtarı yine küçük harfli olan.
    _yazim = (new_username or "").strip().replace(" ", "_")
    old_username = kullanici_adi_duzelt(old_username)
    new_username = kullanici_adi_duzelt(new_username)
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
    try:
        conn.execute(
            "UPDATE students SET username = ?, username_yazim = ? WHERE username = ?",
            (new_username, _yazim, old_username))
    except Exception:
        # Sütun henüz yoksa (çok eski veritabanı) eski davranış sürsün.
        conn.execute("UPDATE students SET username = ? WHERE username = ?",
                     (new_username, old_username))
    conn.execute("UPDATE results SET student_name = ? WHERE student_name = ?", (new_username, old_username))
    # ONEMLI - CEVAP KAYBI: Burada eskiden SADECE students ve results
    # guncelleniyordu. Ogrencinin YARIM KALMIS sinavi in_progress
    # tablosunda eski kullanici adina bagli kaliyor ve isaretledigi tum
    # cevaplar erisilemez oluyordu. "Yanlislari duzeltme turu"nun hangi
    # sorulari soracagi da settings tablosunda kullanici adiyla saklandigi
    # icin o da kopuyordu. Ucu birden tasiniyor.
    conn.execute(
        "UPDATE in_progress SET student_name = ? WHERE student_name = ?",
        (new_username, old_username),
    )
    try:
        _eski_onek = f":{old_username}:"
        _yeni_onek = f":{new_username}:"
        _satirlar = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?", ("wrongmode:%",)
        ).fetchall()
        for _s in _satirlar:
            if _eski_onek in _s["key"]:
                _yeni = _s["key"].replace(_eski_onek, _yeni_onek, 1)
                conn.execute("DELETE FROM settings WHERE key = ?", (_yeni,))
                conn.execute(
                    "UPDATE settings SET key = ? WHERE key = ?", (_yeni, _s["key"])
                )
    except Exception:
        pass  # settings tablosu eski kurulumlarda olmayabilir
    conn.commit()
    conn.close()
    return True, "Kullanıcı adı güncellendi."


@_yazma
def change_admin_username(current_username, current_password, new_username):
    """Yöneticinin kullanıcı adını değiştirir -- mevcut şifreyle doğrular."""
    admin = verify_admin(current_username, current_password)
    if not admin:
        return False, "Mevcut şifre yanlış."
    new_username = kullanici_adi_duzelt(new_username)
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


@_onbellekli
def get_students():
    """Kayıtlı tüm öğrencileri (kullanıcı adı + görünen ad) döner -- admin panelinde listelemek için."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT username, username_yazim, display_name, created_at "
        "FROM students ORDER BY display_name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@_yazma
def delete_student(username, sonuclari_da_sil=True):
    """Bir ogrenci kaydini siler.

    sonuclari_da_sil=True ise o ogrencinin cozdugu sinav sonuclari ve yarim
    kalmis ilerlemesi de silinir. False ise sonuclar veritabaninda kalir
    (hesap gider ama gecmis raporlar korunur)."""
    username = kullanici_adi_duzelt(username)
    if not username:
        return False, "Kullanıcı adı boş olamaz."
    conn = get_conn()
    exists = conn.execute("SELECT 1 FROM students WHERE username = ?", (username,)).fetchone()
    if not exists:
        conn.close()
        return False, "Bu kullanıcı adında bir öğrenci bulunamadı."
    silinen = 0
    if sonuclari_da_sil:
        silinen = conn.execute(
            "SELECT COUNT(*) AS n FROM results WHERE student_name = ?", (username,)
        ).fetchone()["n"]
        conn.execute("DELETE FROM results WHERE student_name = ?", (username,))
        conn.execute("DELETE FROM in_progress WHERE student_name = ?", (username,))
    conn.execute("DELETE FROM students WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    if sonuclari_da_sil:
        return True, f"Öğrenci silindi ({silinen} sınav sonucu da silindi)."
    return True, "Öğrenci silindi; sınav sonuçları veritabanında bırakıldı."


@_onbellekli
def genel_istatistikler():
    """Ana sayfadaki sayaclar icin ozet bilgi uretir.

    Donus: {"toplam_deneme","toplam_soru","toplam_sonuc","ogrenci_sayisi",
            "kategoriler": {kategori: {"deneme","soru","cozulen"}}}"""
    conn = get_conn()
    exams = conn.execute("SELECT id, category, structure FROM exams").fetchall()
    sonuc_sayilari = {
        r["exam_id"]: r["n"]
        for r in conn.execute(
            "SELECT exam_id, COUNT(*) AS n FROM results GROUP BY exam_id"
        ).fetchall()
    }
    toplam_sonuc = conn.execute("SELECT COUNT(*) AS n FROM results").fetchone()["n"]
    ogrenci_sayisi = conn.execute("SELECT COUNT(*) AS n FROM students").fetchone()["n"]
    conn.close()

    kategoriler = {}
    toplam_soru = 0
    for e in exams:
        try:
            yapi = json.loads(e["structure"])
        except (TypeError, ValueError):
            yapi = {}
        soru = sum(
            (meta or {}).get("count", 0)
            for bolum in yapi.values()
            for meta in bolum.values()
        )
        toplam_soru += soru
        k = kategoriler.setdefault(e["category"], {"deneme": 0, "soru": 0, "cozulen": 0})
        k["deneme"] += 1
        k["soru"] += soru
        k["cozulen"] += sonuc_sayilari.get(e["id"], 0)

    return {
        "toplam_deneme": len(exams),
        "toplam_soru": toplam_soru,
        "toplam_sonuc": toplam_sonuc,
        "ogrenci_sayisi": ogrenci_sayisi,
        "kategoriler": dict(sorted(kategoriler.items())),
    }


@_yazma
def reset_student_password(username, new_password):
    """Admin tarafından bir öğrencinin şifresini sıfırlar (öğrenci unutursa)."""
    username = kullanici_adi_duzelt(username)
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
    username = kullanici_adi_duzelt(username)
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

@_onbellekli
def get_categories():
    conn = get_conn()
    # NOT: Eskiden "ORDER BY rowid" kullanılıyordu; "rowid" SQLite'a özel bir
    # sütundur ve PostgreSQL'de yoktur. Sıralamayı SQL'e bırakmak yerine
    # burada yapıyoruz: önce hazır kategoriler kendi sırasıyla, sonra sonradan
    # eklenenler alfabetik. Böylece iki veritabanında da aynı sonuç çıkar.
    rows = conn.execute("SELECT name FROM categories").fetchall()
    conn.close()
    adlar = [r["name"] for r in rows]
    hazir = [c for c in DEFAULT_CATEGORIES if c in adlar]
    digerleri = sorted(a for a in adlar if a not in DEFAULT_CATEGORIES)
    return hazir + digerleri


@_yazma
def add_category(name):
    if not name:
        return
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()


@_yazma
def delete_category(name):
    """Bir BÖLÜM (kategori) adını siler.

    NEDEN GEREKLİ: Yanlış adla eklenen testler silinince bölüm adı
    listede kalmaya devam ediyordu ("Ünite Testleri - English" gibi).
    Kullanıcı onu bir daha kaldıramıyordu. Artık İÇİ BOŞ bölümler
    silinebiliyor; içinde deneme varsa silinmiyor (yanlışlıkla veri
    kaybı olmasın)."""
    if not name:
        return False, "Bölüm adı boş."
    conn = get_conn()
    try:
        _adet = conn.execute(
            "SELECT COUNT(*) AS n FROM exams WHERE category = ?", (name,)
        ).fetchone()["n"]
        if _adet:
            return False, (f"'{name}' bölümünde {_adet} deneme var. "
                           f"Önce onları silin.")
        conn.execute("DELETE FROM categories WHERE name = ?", (name,))
        conn.commit()
    finally:
        conn.close()
    return True, f"'{name}' bölümü silindi."


def bos_kategoriler():
    """İçinde hiç deneme kalmamış, sonradan eklenmiş bölüm adları.
    Hazır bölümler (8. Sınıf LGS vb.) listeye alınmaz."""
    conn = get_conn()
    try:
        _tum = [r["name"] for r in
                conn.execute("SELECT name FROM categories").fetchall()]
        _dolu = {r["category"] for r in
                 conn.execute("SELECT DISTINCT category FROM exams").fetchall()}
    finally:
        conn.close()
    return sorted(a for a in _tum
                  if a not in _dolu and a not in DEFAULT_CATEGORIES)


# ---------- exams ----------

@_yazma
def add_exam(title, category, pdf_path, structure, answer_key, source="manuel",
             pdf_path_original=None, source_url=None):
    conn = get_conn()
    c = conn.cursor()
    exam_id = _insert_id(
        c,
        """INSERT INTO exams (title, category, source, pdf_path, structure, answer_key, created_at, pdf_path_original, source_url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            title,
            category,
            source,
            pdf_path,
            json.dumps(structure, ensure_ascii=False),
            json.dumps(answer_key, ensure_ascii=False),
            datetime.now().isoformat(timespec="seconds"),
            pdf_path_original,
            source_url,
        ),
    )
    conn.commit()
    conn.close()
    return exam_id


def exam_exists(title, category):
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM exams WHERE title = ? AND category = ?", (title, category)
    ).fetchone()
    conn.close()
    return row is not None


@_onbellekli
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


@_onbellekli
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


@_yazma
def delete_exam(exam_id):
    """Sınavı veritabanından siler VE diskte kalan PDF dosyalarını da
    temizler (silmezsek dosyalar sunucuda gereksiz yer kaplamaya devam eder)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT pdf_path, pdf_path_original FROM exams WHERE id = ?", (exam_id,)
    ).fetchone()
    conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
    try:
        conn.execute("DELETE FROM exam_files WHERE exam_id = ?", (exam_id,))
    except Exception:
        pass  # eski veritabanlarında bu tablo olmayabilir
    # ÖNEMLİ: Denemeye ait ÇÖZÜM KAYITLARI da silinir. Eskiden bunlar
    # kalıyordu; "Gelişim Raporum" sayfasında adı okunamayan, açılmayan
    # hayalet satırlar olarak görünüyorlardı.
    for _sql in ("DELETE FROM results WHERE exam_id = ?",
                 "DELETE FROM in_progress WHERE exam_id = ?"):
        try:
            conn.execute(_sql, (exam_id,))
        except Exception:
            pass
    # "Yanlislari duzeltme turu"nun hangi sorulari soracagi settings
    # tablosunda "wrongmode:<deneme>:<ogrenci>:<tur>" anahtariyla duruyor.
    # Deneme silinince bunlar da silinmeli; yoksa tablo sonsuza dek buyur.
    try:
        conn.execute("DELETE FROM settings WHERE key LIKE ?", (f"wrongmode:{exam_id}:%",))
    except Exception:
        pass
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

@_onbellekli
def get_setting(key, default=None):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


@_yazma
def set_setting(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


@_yazma
def add_result(exam_id, student_name, per_subject, total_net, weighted_score,
               answers_detail=None, attempt_no=0, mode="tam"):
    """Her çağrı YENİ bir satır ekler (üzerine yazmaz) -- böylece aynı öğrenci
    aynı denemeyi 10 kere çözse bile 10 ayrı sonuç kaydı tutulur.

    mode: "tam"    -> sinavin tamami cozuldu
          "yanlis" -> "ikinci sans": sadece onceki yanlis/bos sorular cozuldu"""
    conn = get_conn()
    c = conn.cursor()
    result_id = _insert_id(
        c,
        """INSERT INTO results (exam_id, student_name, per_subject, total_net, weighted_score, created_at, answers_detail, attempt_no, mode)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            exam_id,
            student_name,
            json.dumps(per_subject, ensure_ascii=False),
            total_net,
            weighted_score,
            datetime.now().isoformat(timespec="seconds"),
            json.dumps(answers_detail, ensure_ascii=False) if answers_detail is not None else None,
            attempt_no,
            mode,
        ),
    )
    conn.commit()
    conn.close()
    return result_id


@_yazma
def delete_result(result_id):
    """Bir sonuç kaydını siler. SADECE admin panelinden çağrılmalı --
    öğrenci arayüzünde bu işlev için hiçbir düğme yoktur."""
    conn = get_conn()
    conn.execute("DELETE FROM results WHERE id = ?", (result_id,))
    conn.commit()
    conn.close()


@_onbellekli
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


@_onbellekli
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


@_yazma
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


@_yazma
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


@_onbellekli
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


@_yazma
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


# =====================================================================
#  DENEME PDF'LERİNİN KALICI SAKLANMASI
# =====================================================================
# ÖNEMLİ - "PDF DOSYASI SUNUCUDA BULUNAMADI" HATASI:
# Streamlit'in ücretsiz bulut sunucusu, uygulamanın diskini her yeniden
# başlatmada sıfırlar. Sınav KAYDI kalıcı veritabanında (Supabase) durduğu
# için listede görünmeye devam ediyor, ama PDF DOSYASI silindiği için
# açılmıyordu. Aşağıdaki fonksiyonlar PDF'i de veritabanında saklar;
# dosya kaybolduğunda buradan geri yazılır.
#
# Otomatik indirilen MEB kitapçıkları için dosyayı saklamaya gerek yok:
# onların kaynak adresi (source_url) saklanıyor, gerekince yeniden iniyor.

PDF_SAKLAMA_SINIRI = 30 * 1024 * 1024  # 30 MB'tan büyük dosyalar saklanmaz


@_yazma
def pdf_kaydet(exam_id, filename, data):
    """Bir denemenin PDF'ini veritabanına yazar (varsa üzerine)."""
    if not data:
        return False, "Dosya boş."
    if len(data) > PDF_SAKLAMA_SINIRI:
        return False, (
            f"Dosya {len(data) / 1e6:.0f} MB; veritabanında saklamak için çok büyük "
            f"(sınır {PDF_SAKLAMA_SINIRI / 1e6:.0f} MB)."
        )
    conn = get_conn()
    conn.execute("DELETE FROM exam_files WHERE exam_id = ?", (exam_id,))
    conn.execute(
        """INSERT INTO exam_files (exam_id, filename, data, boyut, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (exam_id, filename, data, len(data), datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return True, None


def pdf_getir(exam_id):
    """Denemenin PDF içeriğini veritabanından okur; yoksa None."""
    conn = get_conn()
    row = conn.execute(
        "SELECT filename, data FROM exam_files WHERE exam_id = ?", (exam_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None, None
    ham = row["data"]
    # PostgreSQL sürücüsü ikili veriyi 'memoryview' olarak döndürür.
    if isinstance(ham, memoryview):
        ham = bytes(ham)
    elif isinstance(ham, str):
        ham = ham.encode("latin-1", "ignore")
    return row["filename"], ham


@_onbellekli
def pdf_saklananlar():
    """Hangi denemelerin PDF'i veritabanında duruyor: {exam_id: boyut}"""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT exam_id, boyut FROM exam_files").fetchall()
    except Exception:
        rows = []
    conn.close()
    return {r["exam_id"]: r["boyut"] for r in rows}


@_yazma
def pdf_sil(exam_id):
    conn = get_conn()
    conn.execute("DELETE FROM exam_files WHERE exam_id = ?", (exam_id,))
    conn.commit()
    conn.close()


@_yazma
def exam_kaynak_adresi_yaz(exam_id, url):
    conn = get_conn()
    conn.execute("UPDATE exams SET source_url = ? WHERE id = ?", (url, exam_id))
    conn.commit()
    conn.close()


@_yazma
def exam_pdf_guncelle(exam_id, pdf_path, pdf_path_original=None):
    """Var olan bir denemenin kitapçık dosya yolunu günceller.

    ÖNEMLİ - NEDEN SİLİP YENİDEN EKLEMİYORUZ: Denemeyi silmek, o denemeye
    ait GEÇMİŞ SONUÇLARI da siler (results tablosunda exam_id'ye bağlı
    ON DELETE CASCADE vardır). Kitapçığı yeniden indirip aynı kayda
    bağlamak, çocuğun çözdüğü sınavların sonuçlarını korur."""
    conn = get_conn()
    if pdf_path_original:
        conn.execute(
            "UPDATE exams SET pdf_path = ?, pdf_path_original = ? WHERE id = ?",
            (pdf_path, pdf_path_original, exam_id),
        )
    else:
        conn.execute("UPDATE exams SET pdf_path = ? WHERE id = ?", (pdf_path, exam_id))
    conn.commit()
    conn.close()


# =====================================================================
#  OTURUMUN SAYFA YENİLEMEDE KAYBOLMAMASI
# =====================================================================
# ÖNEMLİ - "SAYFAYI YENİLEYİNCE PROGRAMDAN ÇIKIYORUM": Streamlit'in oturum
# belleği (session_state) tarayıcı bağlantısına bağlıdır. F5'e basmak,
# sekmeyi kapatıp açmak, tablette uygulamayı arka plana atıp geri dönmek --
# bunların hepsi YENİ bir oturum başlatır ve o bellek boşalır; kullanıcı
# giriş yapmamış sayılır.
#
# Çözüm: girişten sonra tarayıcının adres çubuğuna imzalı bir "giriş
# jetonu" konur (?oturum=...). Sayfa yenilendiğinde adres aynı kaldığı için
# jeton geri okunur ve oturum kendiliğinden kurulur.
#
# Jeton ŞİFRE İÇERMEZ: sadece kullanıcı adı, rol ve son kullanma tarihi
# vardır; bunlar sunucuda saklanan gizli bir anahtarla (HMAC) imzalanır.
# İmza tutmuyorsa jeton yok sayılır -- yani elle uydurulamaz.

import hmac
import base64
import time

_JETON_OMRU = 30 * 24 * 3600  # 30 gün


def _imza_anahtari():
    """Jetonları imzalamak için kullanılan gizli anahtar (veritabanında
    saklanır, ilk çağrıda üretilir)."""
    deger = get_setting("_oturum_imza_anahtari")
    if not deger:
        deger = secrets.token_hex(32)
        set_setting("_oturum_imza_anahtari", deger)
    return deger.encode("utf-8")


def _imzala(govde):
    return hmac.new(_imza_anahtari(), govde.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def oturum_jetonu_uret(kullanici, rol="ogrenci"):
    """Giriş yapan kişi için adres çubuğuna konacak imzalı jeton üretir."""
    if not kullanici:
        return ""
    govde = f"{rol}|{kullanici}|{int(time.time()) + _JETON_OMRU}"
    ham = f"{govde}|{_imzala(govde)}"
    return base64.urlsafe_b64encode(ham.encode("utf-8")).decode("ascii").rstrip("=")


def oturum_jetonu_coz(jeton):
    """Jetonu doğrular. Geçerliyse (rol, kullanici) döner, değilse (None, None).

    Jeton geçerli olsa bile hesabın HÂLÂ var olduğu veritabanından
    doğrulanır -- silinmiş bir öğrencinin eski jetonu işe yaramaz."""
    if not jeton:
        return None, None
    try:
        ham = base64.urlsafe_b64decode(jeton + "=" * (-len(jeton) % 4)).decode("utf-8")
        rol, kullanici, bitis, imza = ham.split("|")
    except Exception:
        return None, None
    govde = f"{rol}|{kullanici}|{bitis}"
    if not hmac.compare_digest(imza, _imzala(govde)):
        return None, None
    try:
        if int(bitis) < time.time():
            return None, None
    except ValueError:
        return None, None
    tablo = "admins" if rol == "yonetici" else "students"
    conn = get_conn()
    try:
        row = conn.execute(
            f"SELECT username, display_name FROM {tablo} WHERE username = ?", (kullanici,)
        ).fetchone()
    except Exception:
        row = None
    conn.close()
    if not row:
        return None, None
    return rol, dict(row)
