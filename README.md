# ShortMaker

Videodan kerakli qismni kesib, vertikal 9:16 MP4 qilib beradigan sayt.

Ikki rejim:
- **Videoni yuklash** (asosiy): foydalanuvchi o'z videosini tanlaydi. YouTube'ga bog'liq emas.
- **YouTube havolasi**: server videoni yuklab oladi. Ba'zi serverlarda YouTube bloklaydi.

## Lokal ishga tushirish

```
python -m pip install -r requirements.txt
python app.py
```

ffmpeg o'rnatilgan bo'lishi shart. Brauzerda: http://127.0.0.1:5000

## Sozlamalar (muhit o'zgaruvchilari)

- `MAX_UPLOAD_MB` (100), `MAX_PARALLEL` (1), `OUT_W`/`OUT_H` (720x1280), `YTDLP_COOKIES`
