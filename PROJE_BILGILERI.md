# M.ONUR LGS Eğitim Platformu — Bilgi Notu

Bu dosya, sistemin nerede ne olduğunu hatırlamanız için hazırlandı.
**İçinde hiçbir şifre yoktur** — şifreleri aşağıdaki listeye bakarak
kendiniz ayrı ve güvenli bir yere not edin.

---

## 1. Sistemin parçaları

| Parça | Ne işe yarıyor | Adres |
|---|---|---|
| **GitHub** | Programın kodu burada durur. Herkese açık, sorun değil. | github.com/MHMD2907/lgs_platform |
| **Streamlit** | Programı internette çalıştıran yer. | m-onur-lgs-platform.streamlit.app |
| **Supabase** | Öğrenci hesapları ve sınav sonuçlarının kalıcı olarak saklandığı özel veritabanı. | supabase.com → MHMD2907'nin Projesi |
| **Bilgisayarınız** | Aynı programın yerel kopyası. `BASLAT.bat` ile açılır. Kendi ayrı veritabanı vardır. | Masaüstü\lgs_platform |

**Önemli:** Bulut ile bilgisayarınızdaki kopya **ayrı ayrı** veritabanları
kullanır. Birine eklenen öğrenci/deneme diğerinde görünmez.

---

## 2. Kendinize not etmeniz gerekenler

Bunları bu dosyaya YAZMAYIN; ayrı, size özel bir yere kaydedin:

- [ ] **Yönetici şifresi** (uygulamaya yönetici olarak girerken)
- [ ] **Supabase veritabanı şifresi** (proje kurulurken belirlenen)
- [ ] **Rapor PIN kodu** (Admin Paneli → Hesap Ayarları'ndan belirlediğiniz)
- [ ] **Öğrenci kullanıcı adı ve şifresi**

Şifreleri unutursanız:
- Yönetici şifresi → Streamlit → Settings → Secrets → `ADMIN_PASSWORD`
- Supabase şifresi → Supabase → Project Settings → Database → *Reset database password*
- Öğrenci şifresi → Admin Paneli → Öğrenci Hesapları → Şifreyi Sıfırla

---

## 3. Gizli bilgiler nerede duruyor

Şifreler **hiçbir zaman GitHub'a konmaz.** İki yerde dururlar:

- **Bulutta:** Streamlit → uygulamanın yanındaki ⋮ → Settings → Secrets
- **Bilgisayarınızda:** `lgs_platform\.streamlit\secrets.toml`

Secrets kutusunda iki satır olmalı:

```
ADMIN_PASSWORD = "..."
DB_URL = "postgresql://postgres.dcnkfdmvatmmoiswfzay:...@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
```

`.gitignore` dosyası, bu dosyaların kazara GitHub'a gitmesini engeller.

---

## 4. Güncelleme nasıl yapılır

1. Yeni dosyalar `Masaüstü\lgs_platform` klasörüne kaydedilir
2. github.com/MHMD2907/lgs_platform → **Add file → Upload files**
3. Dosyaları sürükleyip **Commit changes**
4. Streamlit kendini yeniler (1-5 dakika)

`requirements.txt` veya `packages.txt` değiştiyse kurulum uzun sürer,
diğer durumlarda hızlıdır.

---

## 5. Kontrol listesi

Bir şey ters giderse önce şunlara bakın:

- **Admin Paneli'nin en üstü:** yeşil "🔒 Veriler kalıcı veritabanında
  saklanıyor" yazıyorsa Supabase bağlantısı çalışıyor demektir.
- **Sarı uyarı** çıkıyorsa `DB_URL` okunamıyor → Streamlit Secrets'ı kontrol edin.
- **Sınav Çöz sekmesindeki sayaçlar:** kaç deneme, kaç soru, kaç çözüm var.

---

## 6. Ücretsiz plan sınırları (Supabase)

| Kaynak | Sınır | Bizim kullanımımız |
|---|---|---|
| Veritabanı boyutu | 500 MB | Çok altında (sonuç kayıtları küçüktür) |
| Dosya depolama | 1 GB | Şu an kullanılmıyor |
| Aylık aktif kullanıcı | 50.000 | 1-2 kişi |

Rahatlıkla yeter. Proje **7 gün hiç kullanılmazsa** Supabase onu uyutabilir;
uygulamaya tekrar girildiğinde kendiliğinden uyanır, veri kaybolmaz.

---

## 7. Programın özellikleri (kısa liste)

**Öğrenci tarafı**
- Kitapçık sayfa sayfa açılır, optik form yanında durur
- "Görünüm" düğmesiyle yan yana / alt alta seçilebilir
- İşaretlenen cevaplar anında kaydedilir, yarıda kalınırsa devam edilir
- Soru sayacı (kaç soru işaretlendi)
- Deneme listesi **1'den başlayarak** sıralıdır; çözülenlerin başında ✅ ve net vardır
- Sınav sonunda ders ders doğru/yanlış/boş ve net
- **İkinci şans:** sadece yanlış yapılan soruları tekrar çözme
- Aynı denemenin birden çok turu varsa **tur seçici** ile geçmiş turlara dönülür
- **Gelişim Raporum:** her sınav tek satır; işaretle → **Değerlendir** → tek pencerede
  ilk çözüm / ikinci şans karşılaştırması ve gün gün toplamlar
- Her satırdaki **🔍 Sınav Detayı** o sınavın soru soru dökümünü açar
- Ayrıntılı döküm **PIN kodu** ile korunur (bir kez girilir, oturum boyunca sorulmaz)
- Açılan pencereler başlığından tutulup **taşınabilir**

**Yönetici tarafı**
- Geçmiş yıl LGS kitapçıklarını otomatik indirme (2018-2025)
- Bursluluk (İOKBS) kitapçıklarını otomatik indirme
- Soru bankası PDF'ini test test ayırıp ekleme
- Kendi PDF'ini yükleyip cevap anahtarını otomatik okutma
- Öğrenci ekleme/silme, şifre sıfırlama, sonuç silme
- Cevap anahtarlı orijinal kitapçığı sadece yönetici görebilir
