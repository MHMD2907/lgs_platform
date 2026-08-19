# Proje Durumu — LGS Eğitim Platformu

> Bu dosya, projenin nerede kaldığını özetler. Yeni bir Claude oturumunda
> devam edecekseniz, "Bu klasördeki PROJE_DURUMU.md dosyasını oku" deyip
> kaldığınız yerden devam edebilirsiniz.

Son güncelleme: 19 Ağustos 2026

---

## Proje nedir?

Tablet veya bilgisayar tarayıcısından çalışan, geçmiş yıl LGS (ve 6-7. sınıf,
İOKBS/bursluluk, genel soru bankası) sınavlarını çözdüren ve otomatik puanlayan
bir sistem. Ekranın solunda sınav PDF'i, sağında dijital optik form görünür.
Cevap anahtarı öğrenciye hiç gösterilmez; karşılaştırma sunucu tarafında yapılır.

Teknoloji: Python + Streamlit + SQLite. Bilgisayarda çalışır, tablet aynı
Wi-Fi üzerinden tarayıcıyla bağlanır.

---

## Bu sürümde ne yapıldı

Proje bir Gemini sohbetinden çıkan yol haritasına (`LGS Tablet Uygulaması
Geliştirme Yol Haritası`) dayanıyordu. O sohbette biriken eksikler kapatıldı:

### Düzeltilen kritik hata
Eski koddaki cevap anahtarı okuma mantığı **yanlış çalışıyordu**. MEB'in cevap
anahtarı sayfası dersleri yan yana sütunlar halinde basar
(`1.A  1.B  1.D  1.D` → Türkçe/İnkılap/Din/İngilizce aynı satırda). Eski kod
sayfayı tek bir metin bloğu olarak regex'liyor, bu yüzden her derse yanlışlıkla
ilk sütunun (Türkçe/Matematik) cevaplarını atıyordu → yanlış puanlama.

Yeni `parsing.py`, kelimelerin sayfadaki (x, y) koordinatlarını kullanarak
sütunları ayırır. Gerçek 2026 LGS PDF'leriyle test edildi, 90 sorunun tamamı
doğru okundu.

### Eklenen özellikler
- Gerçek puanlama motoru: 3 yanlış 1 doğru götürür + ders katsayıları
  (eski kodda buton vardı ama hesaplama yapmıyordu)
- Sonuç geçmişi ve "Gelişim Raporum" sekmesi (tablo + grafik)
- Kategoriler: 8. Sınıf LGS / 7. Sınıf / 6. Sınıf / İOKBS / Genel Soru Bankası
- Cevap anahtarı sayfasını PDF'ten otomatik kırpma (öğrenci göremez)
- Sözel + Sayısal kitapçıkları tek dosyada birleştirme
- Admin şifreli giriş; öğrenci admin sekmesini göremez
- Otomatik indirme botu (resmi EBA adres kalıbı) + URL'den indirme
- Google Drive'dan içe aktarma (opsiyonel, kurulum gerekir)
- Testi sıfırlayıp yeniden çözme

### Performans düzeltmesi
PDF'ler `static/pdfs` klasöründen normal dosya adresi üzerinden sunulur.
(Yaygın base64 gömme yöntemi 25 MB'lık kitapçığı ~33 MB metne çevirip her
yenilemede tablete yeniden gönderiyor ve kasmaya yol açıyordu.)

---

## Test durumu

**Gerçek verilerle test edildi ve çalışıyor:**
- Cevap anahtarı ayrıştırma (a_2026_sozel.pdf + a_2026_sayisal.pdf)
- Cevap anahtarı sayfasının kırpılması ve gizlendiğinin doğrulanması
- Puanlama (tam doğru = 90 net; hepsi yanlış = negatif net)
- Veritabanı kayıt/okuma
- Uygulamanın hatasız açılması ve PDF'in URL üzerinden sunulması

**Test EDİLEMEDİ (ortam kısıtı nedeniyle, sizin bilgisayarınızda denenmeli):**
- Otomatik EBA indirme (geliştirme ortamında dış ağ erişimi kapalıydı)
- Google Drive entegrasyonu (kullanıcının Google hesabını gerektirir)
- Tabletten gerçek kullanım deneyimi

---

## Dosyalar

```
lgs_platform/
├── BASLAT.bat        → ÇİFT TIKLAYIN: kurar, adresi yazar, başlatır
├── app.py            → ana uygulama (arayüz)
├── db.py             → veritabanı işlemleri
├── parsing.py        → PDF'ten cevap anahtarı okuma + son sayfayı kırpma
├── scoring.py        → net ve puan hesaplama
├── bot.py            → otomatik PDF indirme
├── drive_sync.py     → Google Drive (opsiyonel)
├── config.py         → ADMİN ŞİFRESİ burada
├── requirements.txt  → gerekli kütüphaneler
├── .streamlit/       → görünüm ve sunucu ayarları
├── static/pdfs/      → işlenmiş güvenli PDF'ler buraya kaydedilir
├── README.md         → kurulum ve kullanım kılavuzu
└── PROJE_DURUMU.md   → bu dosya
```

---

## Sıradaki adımlar

1. `config.py` içindeki `ADMIN_PASSWORD` değerini değiştirin (henüz varsayılan).
2. `BASLAT.bat` ile çalıştırın.
3. Admin panelinden ilk LGS denemesini yükleyin (Sözel + Sayısal PDF).
4. Tabletten `http://<bilgisayar-ip>:8501` adresine bağlanıp deneyin.
5. Sorun çıkarsa not alın; düzeltilecek.

### İleride yapılabilecekler (henüz yapılmadı)
- Bulut sunucuya taşıma (bilgisayar kapalıyken de tabletten erişim için)
- Birden fazla öğrenci hesabı / öğrenci bazlı detaylı raporlama
- Soru bazlı analiz (hangi konuda zayıf)
- Süre tutma / sınav süresi sınırı
