# SmartyMS Quiz System

Apni khud ki PDF (original content, jiske rights aapke paas hon) upload karo → text/OCR se
questions auto-extract hote hain → ek shareable quiz link (`/play?v=<id>`) generate hota hai →
koi bhi us link se quiz attempt kar sakta hai (Next / Previous / Skip / Submit / Result / Solutions / Reattempt).

## How PDF → Quiz extraction works

- Text-based PDF pages → parsed directly (`pdfplumber`).
- Scanned/image-only pages → auto OCR (`pytesseract` + `pdf2image`, Hindi + English).
- Questions must roughly follow this pattern to be detected:
  ```
  Q1  Question text here...
  (A) Option one
  (B) Option two
  (C) Option three
  (D) Option four
  ```
- If your PDF has an **Answer Key** section (e.g. `Q1  (D)`), correct answers are
  auto-matched and used for scoring. If no answer key exists, the quiz still works —
  users can attempt it, but Correct/Incorrect scoring will show those as unscored
  (skipped-in-scoring) since there's nothing to compare against.
- This is heuristic/regex-based OCR parsing — very unusual page layouts may need a
  cleanly formatted PDF for best results.

## Repo structure

```
SmartyMS-Toxic-Quiz-System/
├── app.py                 # Flask app + all routes/APIs
├── requirements.txt
├── Dockerfile
├── .env.example           # copy values into Render's Environment tab
├── utils/
│   ├── db.py               # MongoDB connection
│   └── pdf_parser.py       # PDF text/OCR extraction + MCQ parser
├── templates/
│   ├── index.html          # upload page (root "/")
│   ├── generated.html      # link-generated page
│   └── play.html           # quiz attempt page
└── static/
    ├── css/style.css
    └── js/
        ├── main.js          # upload page logic
        ├── generated.js     # copy-to-clipboard logic
        └── quiz.js          # quiz engine (nav, timer, scoring, solutions)
```

## 1. MongoDB setup

1. Go to your MongoDB Atlas cluster → **Connect → Drivers** → copy the connection string.
2. ⚠️ **Important:** rotate/reset your database user's password before going live if you've
   ever shared the connection string anywhere (chat, screenshots, etc.) — treat it as a
   leaked secret once shared outside your own environment.
3. Whitelist `0.0.0.0/0` in Atlas → Network Access (so Render can connect), or better,
   restrict to Render's static outbound IPs if you're on a paid Render plan.

## 2. Deploy to Render

1. Push this repo to GitHub.
2. On Render → **New → Web Service** → connect your GitHub repo.
3. Environment: **Docker** (Render will auto-detect the `Dockerfile`).
4. Under **Environment → Environment Variables**, add (from `.env.example`):
   | Key | Value |
   |---|---|
   | `MONGO_URI` | your real Atlas connection string with real password |
   | `MONGO_DB_NAME` | `smartyms_quiz_system` (or any name you like) |
   | `BASE_URL` | leave blank for first deploy, Render will assign a URL — then come back and set this to that exact URL (e.g. `https://smartyms-toxic-quiz-system.onrender.com`) and redeploy |
   | `MAX_CONTENT_MB` | `500` (optional, adjust as needed) |
5. Deploy. First build takes a few minutes (installing Tesseract + Poppler in Docker).
6. Once live, copy the Render URL into `BASE_URL` env var and **manually redeploy** so
   generated quiz links point to the correct domain.

## 3. Using it

1. Open your Render URL → upload a PDF → tap **GENERATE QUIZ**.
2. You'll see a success popup, then land on a page with the quiz **Title** and
   **Quiz Link** (`https://<your-app>.onrender.com/play?v=<id>`), each with a copy button.
3. Share that link with anyone — no login required, no expiry, multiple people can
   attempt the same link independently (each browser gets its own anonymous attempt
   tracked via a local id, stored server-side in MongoDB `attempts` collection).
4. Tap **GENERATE ANOTHER** to upload a new PDF and repeat.

## Notes on the Flask "keep-alive" snippet

You mentioned a Flask keep-alive pattern normally used to stop a **separate bot process**
from sleeping (e.g. a Telegram bot running in a background thread, with Flask just there
to expose an open port to Render). In this project, **Flask itself is the main app**, so
that pattern isn't needed — `app.run(host="0.0.0.0", port=PORT)` (or `gunicorn` in
Docker, as configured) already binds the port Render checks. If you ever merge this with
a bot process later, reuse the same "run Flask in a background thread" trick from your
bot's `main.py`.

## Limits / honest caveats

- OCR accuracy depends on PDF/image quality — very low-res scans may parse imperfectly.
- The regex parser expects `Q<number>` + `(A)(B)(C)(D)` markers; wildly different
  formats (e.g. options as a), b), c) or numbered 1),2),3),4)) won't be picked up
  without extending `utils/pdf_parser.py`.
- Large PDFs (100s of pages) with OCR fallback can take a while to process on first
  upload — consider showing a spinner/progress state in production use.
