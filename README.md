# LGS Eğitim Platformu

Geçmiş yıl LGS (ve 6-7. sınıf, İOKBS/bursluluk, genel soru bankası) sınavlarını
tablet veya bilgisayar üzerinde çözüp otomatik puanlayan sistem. PDF'i tarayıcıda
solda gösterir, sağda dijital optik form ile cevap alır, cevap anahtarını
öğrenciye hiç göstermeden sunucu tarafında (bilgisayarınızda) puanlar.

## 1) Kurulum

### En kolay yol: BASLAT.bat

Klasördeki **`BASLAT.bat`** dosyasına çift tıklayın. Bu dosya sırasıyla:

1. Python'un kurulu olup olmadığını kontrol eder (kurulu değilse ne yapmanız
   gerektiğini söyler),
2. gerekli kütüphaneleri otomatik kurar (ilk seferde birkaç dakika sürer),
3. tabletten bağlanmak için kullanacağınız adresi ekrana yazar,
4. uygulamayı başlatır.

Açılan siyah pencereyi **kapatmayın** — uygulama o pencere açık olduğu sürece
çalışır. Kapatırsanız tabletten bağlantı da kesilir.

### Elle yapmak isterseniz

```
cd Desktop\lgs_platform
pip install -r requirements.txt
python -m streamlit run app.py
```

(`streamlit is not recognized` hatası alırsanız `python -m streamlit run app.py`
şeklindeki hali işe yarar; `BASLAT.bat` zaten bu hali kullanır.)

Tarayıcınızda `http://localhost:8501` adresi açılır.

**Admin şifresini değiştirin:** `config.py` dosyasını açıp `ADMIN_PASSWORD` değerini
kendi şifrenizle değiştirin. Bunu yapmazsanız öğrenciler admin paneline girip
cevap anahtarlarını görebilir.

## 2) Tabletten Bağlanmak (Aynı Wi-Fi Üzerinden)

Uygulama bilgisayarınızda çalışır; tabletten bağlanmak için bilgisayarın yerel
ağ (LAN) IP adresini bulmanız gerekir:

1. Bilgisayarınızda CMD açıp `ipconfig` yazın, "IPv4 Adresi" satırını bulun
   (örn. `192.168.1.24`).
2. Tabletin **aynı Wi-Fi ağına** bağlı olduğundan emin olun.
3. Tabletin tarayıcısından `http://192.168.1.24:8501` adresine gidin
   (kendi IP adresinizle değiştirin).
4. Tarayıcı menüsünden "Ana Ekrana Ekle" seçeneğini kullanırsanız, uygulama
   tabletinizde tam ekran bir uygulama simgesi gibi açılır (kurulum gerekmez).

Windows Güvenlik Duvarı ilk seferde bir izin penceresi açabilir; "İzin Ver"e
tıklayın (özel/ev ağı için).

Bilgisayarınız kapandığında veya `streamlit run` durduğunda tabletten erişim
de kesilir — uygulamanın çalışması için bilgisayarın açık ve aynı ağda olması
gerekir. Her yerden (bilgisayar kapalıyken de) erişim istiyorsanız, uygulamayı
Streamlit Community Cloud gibi ücretsiz bir buluta taşımak ayrı bir adımdır;
isterseniz bu konuda da yardımcı olabiliriz.

## 3) Kullanım

- **Sınav Çöz** sekmesi: öğrenci adını girer, kategori ve deneme seçer, solda
  PDF'i okuyup sağda optik formu doldurur, "Sınavı Bitir ve Puanla" ile
  sonucu görür. "Testi Sıfırla / Yeniden Çöz" ile aynı denemeyi tekrar çözebilir.
- **Gelişim Raporum**: öğrencinin bugüne kadar çözdüğü tüm denemelerin net ve
  puan geçmişini tablo ve grafikle gösterir.
- **Admin Paneli** (soldan şifreyle giriş yapınca görünür):
  - **8. Sınıf LGS Denemesi Ekle**: Sözel + Sayısal PDF'lerini yükleyin, sistem
    cevap anahtarını son sayfadan otomatik okur, son sayfaları siler ve
    öğrenciye temiz PDF'i gösterir.
  - **Diğer Kategori / Soru Bankası Ekle**: 6-7. sınıf, İOKBS veya konu bazlı
    testler için — ders adlarını ve soru sayılarını siz tanımlarsınız, sistem
    otomatik okumayı dener, olmazsa elle cevap girebilirsiniz.
  - **Otomatik İndirme (Resmi EBA Arşivi)**: MEB'in resmi EBA içerik
    sunucusundaki bilinen adres kalıbını kullanarak, yıl (veya yıl aralığı,
    örn. 2022-2026) girerek geçmiş LGS kitapçıklarını otomatik indirip işler.
    Not: bu kalıp her yıl için garanti değildir; MEB arşiv yapısını
    değiştirebilir. Başarısız olursa "URL'den PDF İndir" veya manuel yükleme
    kullanılabilir.
  - **URL'den PDF İndir**: internette bulduğunuz herhangi bir doğrudan PDF
    linkini yapıştırıp indirtebilirsiniz (farklı siteler için).
  - **Google Drive'dan İçe Aktar**: aşağıdaki 4. bölümdeki kurulumu
    yaptıysanız, kendi Drive klasörünüzdeki PDF'leri listeleyip indirebilirsiniz.
  - **Kayıtlı Denemeler**: sistemdeki tüm testleri görüp silebilirsiniz.

## 4) Google Drive Entegrasyonu (Opsiyonel)

Bu özellik PDF'lerinizi kendi Google Drive'ınızda tutup oradan içe aktarmanızı
sağlar. Kurulumu Google hesabınızla yalnızca SİZ yapabilirsiniz:

1. https://console.cloud.google.com adresine gidin, yeni bir proje oluşturun.
2. Sol menüden "APIs & Services" → "Library" → "Google Drive API" arayıp
   **Enable** deyin.
3. "APIs & Services" → "Credentials" → "Create Credentials" → "OAuth client ID".
   İlk seferde "OAuth consent screen" ayarlarını (External / Test kullanıcı
   olarak kendi e-postanız) tamamlamanız istenebilir.
4. Uygulama türü olarak **Desktop app** seçin, oluşturduktan sonra JSON'u indirin.
5. İndirilen dosyayı `credentials.json` adıyla bu proje klasörüne (app.py'nin
   yanına) koyun.
6. Uygulamayı yeniden başlatıp Admin Paneli → "Google Drive'dan İçe Aktar"
   bölümüne girin; ilk kullanımda bir tarayıcı sekmesi açılıp Google hesabınızla
   giriş yapmanızı isteyecektir.

Drive'daki bir klasörün ID'sini, o klasörü tarayıcıda açtığınızda adres
çubuğundaki `.../folders/` sonrasındaki uzun koddan alabilirsiniz.

## 5) Sık Sorulanlar

### Bilgisayarın açık olması şart mı?
**Evet.** Bu mimaride uygulama bilgisayarınızda çalışan bir Python sunucusudur;
tablet sadece bir tarayıcı penceresidir. Bilgisayar kapalıysa, uyku modundaysa
veya `streamlit run` komutu durduysa tabletten hiçbir şey açılmaz. Tabletin de
aynı Wi-Fi ağında olması gerekir.

Bilgisayardan bağımsız, her yerden çalışsın isterseniz uygulamayı ücretsiz bir
buluta (ör. Streamlit Community Cloud) taşımak gerekir — bu ayrı bir adımdır.

### Google Drive bağlarsam tablet daha az kasar mı?
**Hayır, tabletin yükü değişmez.** Sık karıştırılan bir nokta olduğu için net
olarak açıklayalım:

- Tüm işlem (PDF ayrıştırma, cevap karşılaştırma, puanlama) **her zaman
  bilgisayarda** yapılır. Tablet zaten hiç hesap yapmıyor.
- PDF'in ekranda görünmesi için tablete gitmesi **zorunludur** — dosya ister
  bilgisayarda ister Drive'da dursun, tablet onu görüntülemek için indirmek
  zorundadır.
- Drive kullanırsanız dosya önce Drive'dan bilgisayara, oradan tablete gider;
  yani teorik olarak biraz **daha yavaş** olur.

Google Drive'ın gerçek faydası şudur: PDF arşiviniz bilgisayarınızın diskini
doldurmaz, dosyaları başka bir yerden de yönetebilirsiniz. Yani **depolama
kolaylığı** sağlar, **tablet performansı** değil.

Tablet performansı için asıl önemli olan, PDF'in tablete nasıl gönderildiğidir.
Bu sürümde PDF'ler `static/pdfs` klasöründen normal bir dosya adresi üzerinden
sunulur; tarayıcı dosyayı bir kez indirip önbelleğe alır. (Yaygın yapılan
base64 ile gömme yöntemi, 25 MB'lık bir LGS kitapçığını ~33 MB metne çevirip
her sayfa yenilemesinde yeniden gönderir ve tablette ciddi yavaşlamaya yol
açar; bu sürümde bilerek o yöntem kullanılmamıştır.)

### Admin tarafı bende mi kalıyor, ayrı bir program mı?
Tek bir uygulama var ve **hem siz hem öğrenci aynı adresi** açarsınız. Ayrım
cihaza göre değil, **şifreye** göre yapılır:

- Uygulamayı açan herkes önce sadece "Sınav Çöz" ve "Gelişim Raporum"
  sekmelerini görür. Admin sekmesi ortada yoktur.
- Siz soldaki "Yönetici Girişi" bölümüne `config.py`'deki şifreyi girince
  "Admin Paneli" sekmesi **sadece sizin ekranınızda** belirir.
- Her tarayıcı kendi oturumunu tutar. Yani siz bilgisayarda admin girişi
  yaptığınızda, tabletteki öğrenci bundan etkilenmez; onun ekranında admin
  sekmesi çıkmaz.
- Şifreyi tablete de girerseniz tabletten de yükleme yapabilirsiniz — yani
  admin işini istediğiniz cihazdan yapabilirsiniz. Önemli olan şifreyi
  öğrencilerle paylaşmamaktır.

## 6) Bilinen Sınırlar ve Dürüstlük Notu

- **"Tahmini Ağırlıklı Puan"**, ders katsayılarıyla (Türkçe/Matematik x4,
  diğerleri x1) ağırlıklandırılmış bir net toplamıdır; MEB'in resmi 100-500
  aralığındaki LGS puanı, o yılki TÜM Türkiye'nin ortalama/standart sapmasına
  dayanan istatistiksel bir hesaptır ve tek bir öğrencinin verisiyle
  hesaplanamaz. Bu yüzden gösterilen puan resmi değildir, sadece
  karşılaştırma/takip amaçlıdır.
- Otomatik cevap anahtarı okuma, MEB'in yaygın "1. A  1. B  1. D  1. D" sütunlu
  formatına göre test edilmiştir (gerçek 2026 LGS PDF'leriyle doğrulandı).
  Farklı yayınevlerinin farklı tasarımlarında otomatik okuma başarısız
  olabilir — bu durumda uygulama hatayı açıkça gösterir ve elle giriş seçeneği
  sunar (sessizce yanlış veri KAYDETMEZ).
- Aynı bilgisayarda hem admin hem öğrenci arayüzü çalışır (şifreyle ayrılır).
  Daha yüksek güvenlik için (örn. iki ayrı adres/alt ağ) istenirse mimari
  ayrıca genişletilebilir.
- Otomatik indirme (EBA) modülü, geliştirme ortamının ağ kısıtı nedeniyle
  canlı olarak test EDİLEMEDİ; sizin bilgisayarınızdan çalışması beklenir.
  Çalışmazsa "URL'den PDF İndir" veya elle yükleme her zaman yedektir.
- Google Drive modülü de sizin Google hesabınızı gerektirdiği için buradan
  test edilemedi; kurulum adımları 4. bölümdedir.

## 7) Klasör Yapısı

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
└── static/pdfs/      → işlenmiş güvenli PDF'ler buraya kaydedilir
```

Uygulama ilk çalıştığında `lgs_platform.db` dosyasını kendisi oluşturur.
