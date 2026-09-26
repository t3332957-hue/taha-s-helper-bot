# Taha's Helper Bot — آماده استقرار روی Render

## تنظیمات Render
- Runtime: Python 3
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`
- Plan: Free

## Environment Variables
در Render این دو متغیر را اضافه کن:

- `GROQ_API_KEY` = کلید Groq خودت
- `POLLINATIONS_API_KEY` = کلید Pollinations (اختیاری؛ فقط برای ساخت تصویر)

کلیدها را داخل GitHub یا فایل کد قرار نده.

## نکته مهم درباره Free
Render برای سرویس رایگان بعد از 15 دقیقه بدون درخواست، سرویس را متوقف می‌کند و درخواست بعدی آن را دوباره بالا می‌آورد. همچنین فایل‌های محلی مثل تاریخچه و فایل‌های آپلودشده روی Free پایدار نیستند و با restart/redeploy/spin-down ممکن است از بین بروند.
