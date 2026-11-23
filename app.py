"""Skill Gap Coach - compares a student's resume with a target job role.

One endpoint does the work:  POST /api/analyze
  in : resume (pasted text or a PDF), target role, optional job description
  out: skills you have, skills you're missing, a 4-week plan, interview questions

The heavy lifting is one Claude API call with a JSON schema, so the response
always has the same shape and the frontend can render it directly.
"""
import base64
import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

load_dotenv(Path(__file__).parent / ".env")  # local dev only; on Railway use its Variables tab

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# Gemini (free tier) is used when its key is set; otherwise Claude.
PROVIDER = "gemini" if GEMINI_KEY else "claude"
MODEL = (os.getenv("GEMINI_MODEL", "gemini-2.5-flash") if PROVIDER == "gemini"
         else os.getenv("CLAUDE_MODEL", "claude-opus-5-5"))
MAX_TEXT = 12_000          # characters per text field - keeps cost per request bounded
MAX_PDF_BYTES = 3_000_000  # 3 MB
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_HOUR", "10"))  # per IP; protects your API credits on a public URL

app = FastAPI(title="Skill Gap Coach")
client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
STATIC = Path(__file__).parent / "static"

SYSTEM = """You are a practical career coach for college students and fresh graduates in India and elsewhere.
You compare a candidate's resume with what employers actually expect for a target job role.

Rules:
- Base "skills_have" only on evidence in the resume. Quote or paraphrase the evidence briefly.
- If a job description is provided, treat its requirements as the main source of truth. Otherwise use typical
  entry-level industry expectations for the role, not senior-level ones.
- "skills_missing" should be the 4-8 gaps that matter most for getting hired, most important first.
- The 4-week plan must be realistic for a student studying ~10 hours a week, use free resources where possible,
  and include one small portfolio project that proves the missing skills.
- Interview questions should be ones this candidate is likely to face for this role, with a one-line tip that
  refers to their own background where possible.
- match_score is 0-100: how ready the candidate is to apply for this role today.
- Be honest but encouraging. No generic filler."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["role_summary", "match_score", "skills_have", "skills_missing",
                 "plan", "interview_questions", "resume_tips"],
    "properties": {
        "role_summary": {"type": "string", "description": "2 sentences: what this role does and what employers look for"},
        "match_score": {"type": "integer"},
        "skills_have": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["skill", "evidence"],
            "properties": {"skill": {"type": "string"}, "evidence": {"type": "string"}}}},
        "skills_missing": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["skill", "importance", "why"],
            "properties": {
                "skill": {"type": "string"},
                "importance": {"type": "string", "enum": ["must-have", "nice-to-have"]},
                "why": {"type": "string"}}}},
        "plan": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["week", "focus", "tasks"],
            "properties": {
                "week": {"type": "integer"},
                "focus": {"type": "string"},
                "tasks": {"type": "array", "items": {"type": "string"}}}}},
        "interview_questions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["question", "tip"],
            "properties": {"question": {"type": "string"}, "tip": {"type": "string"}}}},
        "resume_tips": {"type": "array", "items": {"type": "string"}},
    },
}

_hits: dict[str, deque] = defaultdict(deque)


def _check_rate_limit(ip: str) -> None:
    now, window = time.time(), _hits[ip]
    while window and now - window[0] > 3600:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise HTTPException(429, f"Limit of {RATE_LIMIT} analyses per hour reached. Try again later.")
    window.append(now)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def _key_configured() -> bool:
    if PROVIDER == "gemini":
        return True
    key = os.getenv("ANTHROPIC_API_KEY", "")
    return bool(key) and key != "paste-your-key-here"


@app.get("/health")
def health():
    return {"status": "ok", "provider": PROVIDER, "model": MODEL, "api_key_set": _key_configured()}


_gemini_client = None


def _ask_gemini(prompt: str, pdf: bytes | None) -> str:
    from google import genai
    from google.genai import errors, types

    # Keep one client for the app's lifetime: a temporary client gets garbage-collected
    # (and closes its HTTP connection) before the request finishes.
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=GEMINI_KEY,
                                      # v1beta: system instructions + JSON schema aren't on v1
                                      http_options=types.HttpOptions(api_version="v1beta",
                                                                     timeout=90_000))  # ms

    parts = [types.Part.from_bytes(data=pdf, mime_type="application/pdf")] if pdf else []
    parts.append(types.Part.from_text(text=prompt))
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        response_mime_type="application/json",
        response_json_schema=SCHEMA,
    )
    try:
        for attempt in (1, 2):  # one retry for dropped connections
            try:
                resp = _gemini_client.models.generate_content(model=MODEL, contents=parts, config=config)
                break
            except (errors.APIError, HTTPException):
                raise
            except Exception:  # network-level failure (connection reset, DNS, timeout)
                if attempt == 2:
                    raise HTTPException(502, "Could not reach the AI service. Check your connection and try again.")
    except errors.ClientError as e:
        if e.code == 429:
            raise HTTPException(503, "Free AI quota reached for now. Please try again in a minute.")
        if e.code in (400, 401, 403) and "API key" in str(e):
            raise HTTPException(500, "Server has an invalid GEMINI_API_KEY.")
        raise HTTPException(502, f"AI service error ({e.code}).")
    except errors.ServerError as e:
        raise HTTPException(503, f"The AI service is busy ({e.code}). Please try again.")
    if not resp.text:
        raise HTTPException(422, "The AI returned no answer. Please check the input and try again.")
    return resp.text


@app.post("/api/analyze")
async def analyze(
    request: Request,
    role: str = Form(...),
    resume_text: str = Form(""),
    job_description: str = Form(""),
    resume_pdf: UploadFile | None = File(None),
):
    role, resume_text, job_description = role.strip(), resume_text.strip(), job_description.strip()
    if not role:
        raise HTTPException(400, "Please enter a target job role.")
    if len(role) > 200 or len(resume_text) > MAX_TEXT or len(job_description) > MAX_TEXT:
        raise HTTPException(400, "Input is too long. Please shorten the resume or job description.")

    content: list[dict] = []
    pdf: bytes | None = None
    if resume_pdf is not None and resume_pdf.filename:
        pdf = await resume_pdf.read()
        if len(pdf) > MAX_PDF_BYTES:
            raise HTTPException(400, "PDF is larger than 3 MB.")
        if not pdf.startswith(b"%PDF"):
            raise HTTPException(400, "That file doesn't look like a PDF.")
        content.append({"type": "document", "title": "Candidate resume", "source": {
            "type": "base64", "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf).decode()}})
    elif not resume_text:
        raise HTTPException(400, "Paste your resume or upload it as a PDF.")

    if not _key_configured():
        raise HTTPException(500, "Server is not configured: set GEMINI_API_KEY (free) or ANTHROPIC_API_KEY in .env.")
    _check_rate_limit(request.client.host if request.client else "unknown")

    prompt = f"Target role: {role}\n\n"
    if resume_text:
        prompt += f"<resume>\n{resume_text}\n</resume>\n\n"
    else:
        prompt += "The resume is the attached PDF.\n\n"
    if job_description:
        prompt += f"<job_description>\n{job_description}\n</job_description>\n\n"
    prompt += "Analyse the skill gap and return the result in the required JSON format."
    if PROVIDER == "gemini":
        return json.loads(_ask_gemini(prompt, pdf))
    content.append({"type": "text", "text": prompt})

    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            # If a safety classifier declines, the API retries on a suitable fallback model automatically.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise HTTPException(500, "Server is missing a valid ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        raise HTTPException(503, "The AI service is busy. Please try again in a minute.")
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"AI service error ({e.status_code}).")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Could not reach the AI service.")

    if response.stop_reason == "refusal":
        raise HTTPException(422, "The AI declined this request. Please check the input and try again.")
    if response.stop_reason == "max_tokens":
        raise HTTPException(502, "The analysis was cut off. Please try a shorter resume.")

    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)
