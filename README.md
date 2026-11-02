# Lobo Exchange

A small peer-to-peer marketplace prototype for UNM students, built with Flask, SQLAlchemy, Jinja, Bootstrap, and Groq/Qwen.

## Run locally (PowerShell)

From this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open <http://127.0.0.1:5000/>. Stop the server with `Ctrl+C`. With `DATABASE_URL` and `BLOB_READ_WRITE_TOKEN` unset, the app uses SQLite at `instance/lobo_exchange.db` and local photos in `instance/uploads/`, just as before. The existing `.env` is loaded by the app. Keep it private; `.env` is excluded from Git. To run all tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Registration accepts addresses ending in `@unm.edu`, but does not verify mailbox ownership. Passwords are stored as hashes. Listings require a title, description, category, price, and a JPEG, PNG, WebP, or GIF image under 5 MB.

## AI listing suggestions

Set `GROQ_API_KEY` in `.env` for local use. `GROQ_MODEL` defaults to `qwen/qwen3.8-27b`. Restart Flask after editing `.env`. On **Post a listing**, choose a photo and click **Generate with AI**. The app sends the image and any existing seller input to Groq. Empty fields may be filled automatically; populated seller fields are not silently overwritten. Suggestions for populated fields appear separately and can be **Used** or **Dismissed**. The seller controls the final asking price; the AI price range is approximate guidance. Generating suggestions never publishes a listing, and manual listing creation remains available if Groq fails.

## Advisory listing review

**Review before posting** checks the current title, description, price, and photo, then shows possible concerns before the seller chooses **Keep editing**, **Publish listing**, or **Publish anyway**. It does not label a seller as fraudulent or automatically block a listing. Editing the form after a review requires another review before publishing. Groq provides cautious scam, spam, and photo privacy signals. Local checks flag likely phone numbers, emails, street addresses, and student IDs in the text; those detected values are removed from text sent to Groq. A price below half the AI range's low end or above 1.5 times its high end gets an advisory warning. If Groq is unavailable, local checks still run and the seller can publish after reviewing manually.

## Deploy on Vercel

The root `app.py` exports `app`, so Vercel detects Flask without `vercel.json`. `.python-version` pins Python 3.13. The app uses PostgreSQL when `DATABASE_URL` is set and Vercel Blob when `BLOB_READ_WRITE_TOKEN` is set. On Vercel, `DATABASE_URL`, `BLOB_READ_WRITE_TOKEN`, and `SECRET_KEY` are required; startup fails if any is missing. `GROQ_API_KEY` enables AI, and `GROQ_MODEL` optionally overrides the default. Do not put credentials in source control. `.env.example` contains placeholders only.

1. Create a Neon project and database in the [Neon console](https://console.neon.tech/). In its **Connect** dialog, select the database and a pooled connection string, then copy the PostgreSQL URI (including `sslmode=require` if shown). This is the value for `DATABASE_URL`; the app accepts both `postgresql://` and `postgres://` forms and uses psycopg.
2. Push this project to a Git repository and import it as a project in [Vercel](https://vercel.com/new), with this folder as the project root. Use Vercel's detected Flask/Python settings; no build command, output directory, or `vercel.json` is needed.
3. In the Vercel project's **Storage** tab, choose **Create Database → Blob**, set access to **Public**, create the store, and connect it to the deployment environment. Vercel adds `BLOB_READ_WRITE_TOKEN` to that environment. Public listing photos then use the returned Blob URLs and persist across function restarts. Keep the token secret.
4. In **Project Settings → Environment Variables**, set `DATABASE_URL`, `SECRET_KEY` (a long random value), and `GROQ_API_KEY`. Optionally set `GROQ_MODEL`. Confirm `BLOB_READ_WRITE_TOKEN` is present for the same environment. Apply these to **Production**; configure Preview separately if you intend to use it. Do not point a Preview deployment at the production database unless that is intentional.
5. Deploy from the Vercel dashboard. Open the deployed URL, register a test `@unm.edu` account, create a listing, view its photo, then delete it. The app creates missing tables with `db.create_all()` on startup. It does not copy local SQLite data or local photos into Neon/Blob.

Vercel's filesystem is not persistent storage. The cloud configuration is therefore required on Vercel. Uploaded listing images are public marketplace photos. This prototype has no database migrations; `db.create_all()` creates tables but will not alter an existing PostgreSQL schema. Deleting a listing attempts to delete its Blob object; if Blob deletion fails after the database commit, an orphaned object may need manual cleanup. Vercel's CDN may continue serving a deleted image briefly. The prototype still does not verify ownership of an `@unm.edu` mailbox.
