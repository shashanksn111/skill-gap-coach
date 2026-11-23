# Skill Gap Coach

An AI tool that tells a student which skills they're missing for a target job role, and what to do about it in the next 4 weeks.

**Live demo:** https://skill-gap-coach.onrender.com (free tier: the first visit after a quiet spell takes ~30–60 s to wake up) · **Demo video:** _add your link here_

## The problem

Many college students and fresh graduates don't know which skills companies actually expect for a specific job role. College curriculum, industry requirements and job descriptions often don't line up, so students spend months learning technologies without knowing whether they're relevant. Today they piece guidance together from YouTube, online courses, job portals, LinkedIn, friends and placement cells, comparing job descriptions by hand. That process is scattered, slow and rarely personal.

## What it does

1. You enter a target role (for example "Data Analyst"), paste your resume or upload it as a PDF, and optionally paste a real job posting.
2. The app sends these to an AI model with a fixed JSON schema: Google Gemini (free tier) by default, or Claude (Anthropic) if you configure a Claude key instead.
3. You get back:
   - a **readiness score** (0–100) for the role
   - **skills you already have**, each with the evidence from your resume
   - **skills you're missing**, marked must-have or nice-to-have, with the reason each one matters
   - a **4-week learning plan** for about 10 hours a week, ending with a small portfolio project
   - **likely interview questions** with tips tied to your background
   - **resume tips**

## How it works

```
Browser (static/index.html)
   │  form: role + resume text/PDF + job description
   ▼
FastAPI (app.py)  POST /api/analyze
   │  one Claude API call with a system prompt and a JSON schema (structured outputs)
   ▼
Claude returns JSON in a guaranteed shape ──► the page renders it
```

- **Structured outputs:** the response must match a JSON schema, so the frontend never has to parse free text.
- **PDF resumes** are sent to Claude directly as a document. No PDF parsing library is needed.
- **Cost guards for a public URL:** input length limits, a 3 MB PDF cap, and a per-IP limit of 10 analyses per hour.

## Run it locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env          # then put your key in .env
uvicorn app:app --reload
```

Get a free Gemini key at https://aistudio.google.com/apikey and set `GEMINI_API_KEY`. To use Claude instead, leave that empty and set `ANTHROPIC_API_KEY`.

Open http://localhost:8000.

## Deploy on Render (free)

1. Push this folder to a public GitHub repository.
2. On render.com choose **New → Blueprint** and pick the repository. Render reads `render.yaml`.
3. Paste your `GEMINI_API_KEY` when asked, then click **Apply**.
4. Your public URL appears on the service page (`https://skill-gap-coach-xxxx.onrender.com`).

The free tier sleeps after 15 minutes without visitors, so the first visit after that takes about 30–60 seconds to wake up.

## Deploy on Railway

1. Push this folder to a public GitHub repository.
2. On railway.app choose **New Project → Deploy from GitHub repo** and pick the repository.
3. Under **Variables**, add `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY`).
4. Under **Settings → Networking**, click **Generate Domain**. That's your public URL.

The `Procfile` tells Railway how to start the app.

Optional variables: `GEMINI_MODEL` (default `gemini-2.5-flash`, the fastest and most reliable on the free tier when tested), `CLAUDE_MODEL` (default `claude-opus-5-5`) and `RATE_LIMIT_PER_HOUR` (default `10`).

## What I learned

_Fill this in for your reflection: what worked, what broke, and what you'd improve next._

## Ideas for next steps

- Let users compare against several job postings at once and show the most common requirements
- Save progress through the plan and tick off finished tasks
- Suggest specific free courses for each missing skill
