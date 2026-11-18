# יומן לילי

כל ריצה לילית מוסיפה רשומה בראש הקובץ (החדשה למעלה).

תבנית:

```
## YYYY-MM-DD
**מה השתנה:**
- ...
**מדדים (לפני ← אחרי):**
- ...
**מה כדאי לבדוק:**
- ...
**חסום / צריך את נועם:**
- ...
**הלילה הבא:**
- ...
```

---

## 2026-11-18
**מה השתנה:**
- **M0 הושלם.** נבחר stack (D-001): Python + FastAPI, SQLAlchemy עם SQLite כברירת מחדל (מוחלף ב-`DATABASE_URL`), תור עבודות מבוסס-DB בלי Redis, ממשק Jinja2 בלי שלב build.
- שלד פרויקט: חבילת `clipmaker` תחת `src/`, פקודות `clipmaker serve` / `clipmaker probe` / `clipmaker <input> --out <dir>`, דף בית ו-`/api/health`, `.env.example`, `Dockerfile` ו-`docker-compose.yml`, הוראות הרצה ל-Windows ב-README.
- 14 בדיקות pytest (הגדרות, אתר, ffprobe, CLI, מדדים).
- CI ב-GitHub Actions: בדיקות + הרצת eval + בניית Docker ובדיקת health. הדחיפה של `.github/workflows/` עברה.
- `scripts/eval.py` + `clipmaker/metrics.py`: כל המדדים מחוזה ה-eval כבר ממומשים ונבדקים (WER, גבולות משפט, פתיחה ב-hook, שקט מת, פנים בתוך החיתוך, חיתוכי סצנה, תקינות פלט, מהירות) ומחזירים null עד שהצינור יתמוך בהם. גם contact sheets.
**מדדים (לפני ← אחרי):**
- לפני: אין קוד. אחרי: 14/14 בדיקות ירוקות; eval רץ על 4 סרטונים, 0 שגיאות, 0 קליפים (הצינור עדיין שלד), כל מדדי האיכות null.
**מה כדאי לבדוק:**
- D-001 ב-`docs/DECISIONS.md` (בחירת ה-stack).
- לנסות `pip install -e ".[dev]"` ואז `clipmaker serve` ב-Windows.
- `docker compose up` לא נבדק כאן כי Docker Hub חסום בסביבת הענן; ה-CI בונה את האימג' ובודק אותו.
**חסום / צריך את נועם:**
- H-001 (אופציונלי): huggingface.co עדיין חסום (403). לא חוסם; התמלול ישתמש במודלים מ-GitHub releases.
**הלילה הבא:**
- M1: תמלול עם חותמות זמן למילים (Whisper ב-sherpa-onnx מ-GitHub releases), ואז פילוח למשפטים וקליפים מועמדים.

## 2026-11-17 (הקמה, לא ריצה לילית)
**מה השתנה:**
- נוספו `CLAUDE.md` (הוראות לסוכן), `docs/ROADMAP.md`, `docs/DECISIONS.md`, `docs/NEEDS_HUMAN.md`.
- נוסף `scripts/make_test_videos.py`, שיוצר 4 סרטוני בדיקה סינתטיים עם תשובות נכונות מדויקות: פודקאסט לרוחב עם "פנים" שזזות ושקט מת, סרטון לאורך, דיבור בעברית, וחיתוכי סצנה.
**הלילה הבא:**
- אבן דרך M0: בחירת stack, שלד פרויקט, בדיקות ו-eval.
