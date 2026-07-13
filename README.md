# Nepal EE Vacancy Monitor — Browser-Only Setup (GitHub + Render + cron-job.org)

No local install. Same workflow as your other projects.

## 1. Create the GitHub repo
- github.com → New repository → `ee-vacancy-monitor`
- Upload files via "Add file → Upload files" (browser, drag-and-drop): `app.py`, `config.json`, `requirements.txt`, `Procfile`
- Do NOT upload any file containing your Gmail password or GitHub token — those go into Render's environment variables, never into the repo.

## 2. Create a GitHub Personal Access Token (for dedup storage)
- github.com → Settings → Developer settings → Personal access tokens → Fine-grained tokens
- Repository access: only this repo (`ee-vacancy-monitor`)
- Permissions: Contents → Read and write
- Generate, copy the token (starts with `github_pat_...`) — you'll paste it into Render, not GitHub

## 3. Get a Gmail app password
- https://myaccount.google.com/security → turn on 2-Step Verification
- https://myaccount.google.com/apppasswords → generate one for "Mail"
- Copy the 16-character password

## 4. Deploy on Render (browser only)
- render.com → New → Web Service → connect your GitHub repo
- Runtime: Python 3
- Build command: `pip install -r requirements.txt`
- Start command: (Render will auto-detect the Procfile — `gunicorn app:app`)
- Instance type: Free

Add these Environment Variables in the Render dashboard:

| Key | Value |
|---|---|
| `TRIGGER_SECRET` | any random string you make up, e.g. `ee-mon-8k2j9x` |
| `GITHUB_TOKEN` | the token from step 2 |
| `GITHUB_REPO` | `yourusername/ee-vacancy-monitor` |
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-char app password from step 3 |

Deploy. Once live, note your Render URL, e.g. `https://ee-vacancy-monitor.onrender.com`

## 5. Test it manually first
Visit in browser:
```
https://ee-vacancy-monitor.onrender.com/run-check?token=YOUR_TRIGGER_SECRET
```
You should get a JSON response listing how many new vacancies were found. First run treats everything as new (no history yet) — check your inbox for the email.

Visit the same URL again — `new_vacancies_found` should now be `0` for anything already seen, confirming dedup via GitHub is working (check the repo — it should now have a `seen_vacancies.json` file committed by GitHub Actions bot... actually committed by your token, so it'll show as authored by you).

## 6. Set up cron-job.org
- cron-job.org → Create cronjob
- URL: `https://ee-vacancy-monitor.onrender.com/run-check?token=YOUR_TRIGGER_SECRET`
- Schedule: every 12 hours
- Since Render free web services sleep after ~15 min of inactivity, the first ping after a long gap will be slow (cold start, up to ~50s) — cron-job.org's default timeout usually tolerates this, but if it times out, bump the request timeout setting in cron-job.org's advanced options to 60s+.

## Known limitations
- Keyword-matching link scraper, not a structured field extractor — no reliable salary/deadline/qualification parsing out of the box.
- Sites needing JavaScript to render listings won't show results with plain `requests`; flag which source is empty and I can add a headless-render fallback.
- No PSC/Lok Sewa source yet — add it once you check that site's HTML structure.
- Dedup history lives in your repo as `seen_vacancies.json`, committed automatically — don't hand-edit it while the service is running to avoid a conflicting `sha`.
