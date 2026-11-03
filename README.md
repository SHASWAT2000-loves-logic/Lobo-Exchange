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

Registration accepts addresses ending in `@unm.edu`, but does not verify mailbox ownership. Passwords are stored as hashes. Listings require a title, description, category, price, and a JPEG, PNG, WebP, or GIF image under 4 MB.

## AI listing suggestions

Set `GROQ_API_KEY` in `.env` for local use. `GROQ_MODEL` defaults to `qwen/qwen3.8-27b`. Restart Flask after editing `.env`. On **Post a listing**, choose a photo and click **Generate with AI**. The app sends the image and any existing seller input to Groq. Empty fields may be filled automatically; populated seller fields are not silently overwritten. Suggestions for populated fields appear separately and can be **Used** or **Dismissed**. The seller controls the final asking price; the AI price range is approximate guidance. Generating suggestions never publishes a listing, and manual listing creation remains available if Groq fails.

## Advisory listing review

**Review before posting** checks the current title, description, price, and photo, then shows possible concerns before the seller chooses **Keep editing**, **Publish listing**, or **Publish anyway**. It does not label a seller as fraudulent or automatically block a listing. Editing the form after a review requires another review before publishing. Groq provides cautious scam, spam, and photo privacy signals. Local checks flag likely phone numbers, emails, street addresses, and student IDs in the text; those detected values are removed from text sent to Groq. A price below half the AI range's low end or above 1.5 times its high end gets an advisory warning. If Groq is unavailable, local checks still run and the seller can publish after reviewing manually.

## Live deployment

The marketplace is live at **<https://lobo-exchange.vercel.app/>**. Its [GitHub repository](https://github.com/SHASWAT2000-loves-logic/Lobo-Exchange) is connected to the Vercel project, with `main` as the production branch. Pushing to `main` triggers a production deployment.

Vercel runs the root `app.py` as Flask with Python 3.13 from `.python-version`. Production uses a free-tier Neon PostgreSQL database and a public Vercel Blob store for listing photos. Local development still uses SQLite and `instance/uploads/` when cloud variables are absent. No `vercel.json` is needed; `.vercelignore` excludes local secrets and data from CLI deployments.

Production environment variable names are `DATABASE_URL`, `BLOB_READ_WRITE_TOKEN`, `SECRET_KEY`, and `GROQ_API_KEY`. `GROQ_MODEL` is optional and defaults to `qwen/qwen3.8-27b`. Keep their values in Vercel's environment settings, never in source control. The local `.env` and Vercel-generated `.env.local` are ignored by Git; `.env.example` contains placeholders only.

The initial production check confirmed that the homepage, Register, Login, CSS, and JavaScript load; Neon contains the `user` and `listing` tables; and a small Blob upload and deletion succeed. A full listing workflow has not yet been exercised against production services.

The app creates missing database tables with `db.create_all()` on startup, but has no schema migrations. Local SQLite records and photos are not copied to production. Listing photos in Blob are public. If Blob deletion fails after a listing is removed from the database, the object may need manual cleanup; Vercel's cache can also serve a deleted image briefly. Registration checks the `@unm.edu` address format but does not verify mailbox ownership.

## Try the deployed prototype

The deployed version of Lobo Exchange is available at:

https://lobo-exchange.vercel.app/

To try the application:

1. Open the deployed website and select Register.
2. Create an account using an unused test email address ending in `@unm.edu`, such as `grader-test123@unm.edu`.
3. Log in with the account you created.
4. Browse existing marketplace listings or select Post a listing to create a new one.
5. Upload an item image and either enter the listing details manually or use Generate with AI.
6. If information has already been entered, AI suggestions appear separately and can be accepted with Use or ignored with Dismiss.
7. Review the approximate AI price guidance if available.
8. Select Review before posting to check the listing for possible privacy, suspicious-content, spam-like, or unusual-price concerns.
9. Edit the listing if needed, then publish it.
10. Open the published listing from the Browse page to view its details. The seller who created the listing can also delete it.

The current prototype checks that registration addresses end in `@unm.edu`, but it does not yet verify ownership of the email account. Therefore, an actual UNM mailbox is not required for prototype testing.
