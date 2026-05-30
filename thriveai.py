"""
ThriveAI — the brain behind Thrive365. 🌱🤖

A single, self-contained module that powers both the conversational assistant
and the eco-task photo verifier. It is designed to be SMART and FREE:

    Provider chain (tried in order, first one that works wins):

        1. Gemini   — Google's free-tier API (smart + multimodal). Set GEMINI_API_KEY.
        2. Ollama   — a local model on your own GPU (offline fallback). No key, no quota.
        3. Heuristic — built-in rule-based brain (always works, zero deps).

The rest of the app never talks to a provider directly — it only calls
`thriveai.chat(...)` and `thriveai.verify_image(...)`. Swapping or adding a
provider later means editing this file and nothing else.

Configuration (all optional — sensible defaults, everything degrades gracefully):

    GEMINI_API_KEY        free key from https://aistudio.google.com/apikey
    GEMINI_MODEL          default: gemini-2.0-flash
    OLLAMA_URL            default: http://localhost:11434
    OLLAMA_MODEL          default: llama3.1          (chat)
    OLLAMA_VISION_MODEL   default: llava             (image verification)
"""

import os
import json
import time
import base64

import requests

# ── Configuration ────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '').strip()
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.0-flash').strip()
GEMINI_ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/models'

OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://localhost:11434').rstrip('/')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'llama3.1').strip()
OLLAMA_VISION_MODEL = os.environ.get('OLLAMA_VISION_MODEL', 'llava').strip()

# Photo verification strictness: if the model passes a photo but reports a confidence
# below this (0-100), we override it to a rejection. Higher = stricter. 0 disables.
try:
    VERIFY_MIN_CONFIDENCE = int(os.environ.get('VERIFY_MIN_CONFIDENCE', '50'))
except ValueError:
    VERIFY_MIN_CONFIDENCE = 50

# Network timeouts (seconds). Generous enough for a local GPU model to think.
GEMINI_TIMEOUT = 30
OLLAMA_TIMEOUT = 120
_AVAILABILITY_TTL = 30  # re-probe a provider's reachability at most this often

# How many past turns of conversation to keep as context.
MAX_HISTORY_TURNS = 12

MEDIA_TYPES = {
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
    'gif': 'image/gif', 'webp': 'image/webp',
}

# ── Small availability cache (avoids hammering an unreachable Ollama) ──────────

_ollama_state = {'ok': False, 'checked_at': 0.0}


def _ollama_available():
    """Return True if a local Ollama server is reachable. Cached briefly."""
    now = time.time()
    if now - _ollama_state['checked_at'] < _AVAILABILITY_TTL:
        return _ollama_state['ok']
    ok = False
    try:
        r = requests.get(f'{OLLAMA_URL}/api/tags', timeout=2)
        ok = r.status_code == 200
    except requests.RequestException:
        ok = False
    _ollama_state.update(ok=ok, checked_at=now)
    return ok


def active_engine():
    """Human-readable name of the engine that would currently handle a request."""
    if GEMINI_API_KEY:
        return 'gemini'
    if _ollama_available():
        return 'ollama'
    return 'heuristic'


# ── System prompt (ThriveAI's personality + live app knowledge) ───────────────

def build_system_prompt(lang, context=None):
    """Build the system instruction. `context` is an optional dict with live
    user/app state so ThriveAI can give grounded, personalised answers."""
    reply_lang = 'Bulgarian (български)' if lang == 'bg' else 'English'

    base = f"""You are ThriveAI 🌱 — a smart, friendly, genuinely helpful AI assistant. \
You are a FULL general-purpose assistant: you can answer ANY question and help with \
ANY topic, exactly like a capable modern AI chatbot. That includes science, technology, \
math, coding, history, geography, health, cooking, languages and translation, writing, \
study help, life advice, current concepts, fun facts, brainstorming — anything the user asks. \
Answer real questions directly and accurately. Never refuse a normal question just because \
it isn't about the app.

You ALSO live inside Thrive365, a gamified sustainability app for the city of Burgas, \
Bulgaria, so you double as its expert in-app guide. When the user asks about the app, \
their progress, eco-actions, or Burgas, use this knowledge:
- Daily eco-tasks (plant a seedling, pick up litter, cycle instead of drive, photograph \
wildlife, etc.). A task is completed by uploading a photo, which an AI verifier checks.
- Verified tasks award points → they fuel the leaderboard and can be spent in the Shop on \
real Burgas rewards (free coffee, bus day-pass, eco tote bag, planting a tree, ...).
- Streaks reward consecutive active days. Badges unlock from milestones (first task, \
100/500/1000 points, 7-day streak). There's an interactive map of Burgas with task \
locations and a community activity heatmap.
- You know Burgas well: Primorski Park / Sea Garden, Atanasovsko Ezero salt lake, Ezeroto, \
cycling routes, recycling, local nature and sustainability.

How to respond:
- Be accurate and genuinely useful first. For general questions, give a real, correct, \
complete answer (use clear structure, examples, or short code blocks when helpful).
- Be reasonably concise but never unhelpfully short — match the depth the question needs.
- Warm and encouraging, with the occasional tasteful emoji. When it fits naturally, you \
can gently connect things back to greener living, but don't force it.
- Use the live user data below for anything app/progress related, and NEVER invent point \
totals, prize codes, ranks, or task results — only state what the data shows.
- If you genuinely don't know or aren't sure, say so honestly.

IMPORTANT: Always write your reply in {reply_lang}, regardless of the language of the question."""

    if context:
        lines = ["\n\nLive context about the person you're talking to:"]
        if context.get('name'):
            lines.append(f"- Name: {context['name']}")
        if context.get('points') is not None:
            lines.append(f"- Points: {context['points']}")
        if context.get('streak') is not None:
            lines.append(f"- Current streak: {context['streak']} day(s)")
        if context.get('rank') is not None:
            lines.append(f"- Leaderboard rank: #{context['rank']}")
        if context.get('today_tasks'):
            lines.append("- Today's eco-tasks:")
            for t in context['today_tasks']:
                status = '✅ done' if t.get('done') else 'not yet done'
                lines.append(
                    f"    • \"{t['title']}\" (+{t['points']} pts, {t['location']}) — {status}")
        if context.get('earned_badges'):
            lines.append(f"- Badges earned: {', '.join(context['earned_badges'])}")
        if context.get('next_prize'):
            np = context['next_prize']
            lines.append(
                f"- Closest affordable reward: \"{np['title']}\" costs {np['cost']} pts")
        base += "\n".join(lines)

    return base


# ── Gemini provider ───────────────────────────────────────────────────────────

# Transient HTTP statuses worth retrying (free-tier rate limits / brief overload).
_RETRYABLE_STATUS = {403, 429, 500, 503}


def _gemini_request(payload, max_retries=2):
    url = f'{GEMINI_ENDPOINT}/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}'
    for attempt in range(max_retries + 1):
        r = requests.post(url, json=payload, timeout=GEMINI_TIMEOUT)
        if r.status_code in _RETRYABLE_STATUS and attempt < max_retries:
            # brief backoff, then retry — the free tier throttles short bursts
            time.sleep(1.2 * (attempt + 1))
            continue
        r.raise_for_status()
        data = r.json()
        candidates = data.get('candidates') or []
        if not candidates:
            raise ValueError(f'Gemini returned no candidates: {data}')
        parts = candidates[0].get('content', {}).get('parts', [])
        text = ''.join(p.get('text', '') for p in parts).strip()
        if not text:
            raise ValueError('Gemini returned empty text')
        return text
    # exhausted retries on a retryable status — surface it for graceful fallback
    r.raise_for_status()


def _gemini_chat(system, history, message):
    contents = []
    for turn in history:
        role = 'model' if turn.get('role') == 'assistant' else 'user'
        contents.append({'role': role, 'parts': [{'text': turn.get('content', '')}]})
    contents.append({'role': 'user', 'parts': [{'text': message}]})
    payload = {
        'system_instruction': {'parts': [{'text': system}]},
        'contents': contents,
        'generationConfig': {'temperature': 0.7, 'maxOutputTokens': 1500},
    }
    return _gemini_request(payload)


def _gemini_vision(prompt, image_b64, mime):
    payload = {
        'contents': [{
            'role': 'user',
            'parts': [
                {'inline_data': {'mime_type': mime, 'data': image_b64}},
                {'text': prompt},
            ],
        }],
        'generationConfig': {
            'temperature': 0.1,
            'maxOutputTokens': 800,
            # Force a clean JSON object — no prose, no markdown fences.
            'responseMimeType': 'application/json',
            # Disable "thinking" so the token budget isn't consumed before the
            # answer (gemini-2.5-flash thinks by default, which truncated output).
            'thinkingConfig': {'thinkingBudget': 0},
        },
    }
    return _gemini_request(payload)


# ── Ollama provider (local) ───────────────────────────────────────────────────

def _ollama_chat(system, history, message):
    messages = [{'role': 'system', 'content': system}]
    for turn in history:
        role = 'assistant' if turn.get('role') == 'assistant' else 'user'
        messages.append({'role': role, 'content': turn.get('content', '')})
    messages.append({'role': 'user', 'content': message})
    r = requests.post(f'{OLLAMA_URL}/api/chat', timeout=OLLAMA_TIMEOUT, json={
        'model': OLLAMA_MODEL,
        'messages': messages,
        'stream': False,
        'options': {'temperature': 0.7},
    })
    r.raise_for_status()
    text = r.json().get('message', {}).get('content', '').strip()
    if not text:
        raise ValueError('Ollama returned empty content')
    return text


def _ollama_vision(prompt, image_b64):
    r = requests.post(f'{OLLAMA_URL}/api/chat', timeout=OLLAMA_TIMEOUT, json={
        'model': OLLAMA_VISION_MODEL,
        'messages': [{'role': 'user', 'content': prompt, 'images': [image_b64]}],
        'stream': False,
        'options': {'temperature': 0.1},
    })
    r.raise_for_status()
    text = r.json().get('message', {}).get('content', '').strip()
    if not text:
        raise ValueError('Ollama vision returned empty content')
    return text


# ── Heuristic provider (always-on, offline, zero-cost fallback) ───────────────

def _heuristic_chat(message, lang, context=None):
    """A rule-based brain. Not "smart", but never fails and stays on-topic."""
    msg = (message or '').lower()
    bg = lang == 'bg'
    ctx = context or {}

    def has(*words):
        return any(w in msg for w in words)

    # Personalised greeting / status
    if has('point', 'точк', 'score', 'резултат') and ctx.get('points') is not None:
        if bg:
            return f"В момента имаш {ctx['points']} точки ⚡. Изпълни още задача от днешните, за да добавиш повече!"
        return f"You currently have {ctx['points']} points ⚡. Complete one of today's tasks to add more!"

    if has('streak', 'серия') and ctx.get('streak') is not None:
        if bg:
            return f"Серията ти е {ctx['streak']} дни 🔥. Влизай и изпълнявай по една задача всеки ден, за да не я загубиш!"
        return f"Your streak is {ctx['streak']} day(s) 🔥. Complete a task each day to keep it alive!"

    if has('task', 'задач', 'today', 'днес', 'challenge', 'предизвикател', 'do '):
        tasks = ctx.get('today_tasks') or []
        pending = [t for t in tasks if not t.get('done')]
        if pending:
            t = pending[0]
            if bg:
                return f"Опитай „{t['title']}“ ({t['location']}) за +{t['points']} точки 🌿. Качи снимка, за да я потвърдя!"
            return f"Try \"{t['title']}\" ({t['location']}) for +{t['points']} points 🌿. Upload a photo and I'll verify it!"
        if bg:
            return "Изпълни всички днешни задачи! 🎉 Върни се утре за нови предизвикателства."
        return "You've done all of today's tasks! 🎉 Come back tomorrow for new challenges."

    if has('shop', 'reward', 'prize', 'redeem', 'магазин', 'наград', 'купи'):
        if bg:
            return "В Магазина можеш да обмениш точки за реални награди в Бургас — кафе, карта за транспорт, еко чанта или дори засаждане на дърво 🌳."
        return "In the Shop you can spend points on real Burgas rewards — coffee, a bus pass, an eco tote, or even planting a tree 🌳."

    if has('badge', 'значк', 'achievement', 'постижен'):
        if bg:
            return "Значките се отключват с напредъка ти: първа задача 🌱, 100/500/1000 точки и 7-дневна серия 🔥. Виж ги в Профила."
        return "Badges unlock as you progress: first task 🌱, 100/500/1000 points, and a 7-day streak 🔥. Check them in your Profile."

    if has('map', 'карта', 'where', 'къде', 'location', 'локац'):
        if bg:
            return "Картата показва къде са днешните задачи и топлинна карта на активността в Бургас 🗺️. Виж раздел „Карта“."
        return "The Map shows where today's tasks are plus a community activity heatmap of Burgas 🗺️. Open the Map tab."

    if has('photo', 'verif', 'снимк', 'провер', 'upload', 'качи'):
        if bg:
            return "За да изпълниш задача, качи снимка на еко-действието си. Аз я преглеждам и ако е автентична — печелиш точките! 📸"
        return "To complete a task, upload a photo of your eco-action. I review it and if it's authentic — you earn the points! 📸"

    if has('recycl', 'рецикл', 'waste', 'боклук', 'отпадъц', 'trash'):
        if bg:
            return "Разделяй отпадъците: пластмаса, хартия, стъкло и био 🟡🔵🟢. В Бургас има цветни контейнери в повечето квартали."
        return "Separate your waste: plastic, paper, glass, and organics 🟡🔵🟢. Burgas has colour-coded bins in most neighbourhoods."

    if has('hello', 'hi ', 'hey', 'здрав', 'здравей', 'привет') or msg.strip() in ('hi', 'hello', 'здрасти'):
        name = ctx.get('name', '')
        if bg:
            return f"Здравей{', ' + name if name else ''}! 🌱 Аз съм ThriveAI. Питай ме за днешните задачи, точки, награди или съвети за по-зелен Бургас."
        return f"Hi{', ' + name if name else ''}! 🌱 I'm ThriveAI. Ask me about today's tasks, your points, rewards, or tips for a greener Burgas."

    # Default — heuristic mode can't answer open-ended/general questions; be honest.
    if bg:
        return ("В момента работя в опростен офлайн режим, затова мога да помагам най-вече с "
                "Thrive365 — задачи, точки, значки, награди и еко-съвети. За пълни отговори на "
                "всякакви въпроси, свържи Gemini API ключ (безплатен) в .env. С какво да помогна? 🌱")
    return ("I'm running in a simplified offline mode right now, so I can mainly help with "
            "Thrive365 — tasks, points, badges, rewards, and eco-tips. To answer any question "
            "fully, connect a free Gemini API key in .env. How can I help? 🌱")


# ── Public API: chat ──────────────────────────────────────────────────────────

def chat(message, history=None, lang='en', context=None):
    """Generate a ThriveAI reply.

    Returns a dict: {'reply': str, 'engine': 'gemini'|'ollama'|'heuristic'}.
    Never raises — always degrades to the heuristic brain.
    """
    history = (history or [])[-MAX_HISTORY_TURNS:]
    system = build_system_prompt(lang, context)

    if GEMINI_API_KEY:
        try:
            return {'reply': _gemini_chat(system, history, message), 'engine': 'gemini'}
        except Exception as e:  # noqa: BLE001 — never let a provider error reach the user
            print(f'[ThriveAI] Gemini chat failed, falling back: {e}')

    if _ollama_available():
        try:
            return {'reply': _ollama_chat(system, history, message), 'engine': 'ollama'}
        except Exception as e:  # noqa: BLE001
            print(f'[ThriveAI] Ollama chat failed, falling back: {e}')

    return {'reply': _heuristic_chat(message, lang, context), 'engine': 'heuristic'}


# ── Public API: image verification ────────────────────────────────────────────

def _verification_prompt(task, lang):
    reply_lang = 'Bulgarian (български)' if lang == 'bg' else 'English'
    return f"""You are ThriveAI's strict photo-verification system for Thrive365, a \
gamified sustainability app in Burgas, Bulgaria. Your job is to judge — accurately and \
objectively — whether a submitted photo is genuine, real-world evidence that THIS specific \
eco-task was actually done. Users earn points for passing, so you must not be fooled.

THE TASK TO VERIFY
  Title: "{task.title_en}"
  Description: "{task.description_en}"

STEP 1 — Identify the required evidence.
From the task title and description, determine the specific, concrete things that MUST be \
visible in a genuine photo of this task (the subject/action, relevant objects, and setting). \
Example: a "pick up litter" task needs visible collected trash, a bag, or hands cleaning; a \
"plant a seedling" task needs a plant/seedling and soil or planting; a "cycle instead of \
drive" task needs a bicycle in a real outdoor street setting; a "photograph wildlife" task \
needs actual wildlife.

STEP 2 — Examine the photo carefully and check it against that evidence.
Verify ONLY if the photo clearly and plausibly shows the required real-world evidence for \
THIS task. Be rigorous and literal — the photo must match the actual task, not merely be \
"eco-related".

REJECT the photo if ANY of these are true:
- It does not clearly show the specific action/subject this task requires.
- It shows a different activity (e.g. a plant photo submitted for a litter-cleanup task).
- It is unrelated, random, blank, too dark/blurry to tell, or just text/a selfie with no \
task evidence.
- It is clearly a screenshot, a stock/internet image, a screen/monitor, a drawing, or AI-\
generated rather than a real photo the user took.
- It only vaguely suggests the theme without actual evidence the task was performed.

When in genuine doubt about whether the evidence is present, REJECT — accuracy matters more \
than being nice. Do not pass a photo you are not confident about.

Write the "feedback" in {reply_lang}. If verified, acknowledge the SPECIFIC thing you saw \
that proves it. If rejected, briefly say what was missing and exactly what photo to upload \
instead.

Respond with ONLY valid JSON — no extra text, no markdown fences:
{{"verified": true, "confidence": 0-100, "feedback": "1-2 sentences naming the specific evidence you saw"}}
or
{{"verified": false, "confidence": 0-100, "feedback": "1-2 sentences: what was missing and what to upload instead"}}"""


def _parse_verification(text):
    """Extract {verified, feedback} from a model's (possibly messy) JSON reply."""
    text = (text or '').strip()
    if '```' in text:
        # strip a ```json ... ``` fence if present
        parts = text.split('```')
        if len(parts) >= 2:
            text = parts[1].strip()
            if text.lower().startswith('json'):
                text = text[4:].strip()
    # If there's surrounding prose, grab the outermost JSON object.
    if not text.startswith('{'):
        start, end = text.find('{'), text.rfind('}')
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
    result = json.loads(text)
    # Strict default: if the model omits "verified", treat as NOT verified.
    verified = bool(result.get('verified', False))
    feedback = result.get('feedback', 'Task reviewed!')
    confidence = result.get('confidence')
    # Code-level guard: a low-confidence "pass" is downgraded to a rejection.
    if verified and isinstance(confidence, (int, float)) and confidence < VERIFY_MIN_CONFIDENCE:
        verified = False
        if not result.get('feedback'):
            feedback = ('The photo did not clearly show this task was completed. '
                        'Please upload a clear photo of the actual eco-action.')
    return verified, feedback


def verify_image(task, photo_path, lang='en'):
    """Verify a task-completion photo.

    Returns (verified: bool, feedback: str). Never raises. If no AI provider is
    available, accepts the photo with an encouraging message (same friendly
    behaviour the app had before, so verification never hard-blocks a user).
    """
    prompt = _verification_prompt(task, lang)
    try:
        with open(photo_path, 'rb') as f:
            image_b64 = base64.standard_b64encode(f.read()).decode('utf-8')
    except OSError as e:
        print(f'[ThriveAI] could not read photo: {e}')
        return _accept_fallback(lang)

    ext = photo_path.lower().rsplit('.', 1)[-1]
    mime = MEDIA_TYPES.get(ext, 'image/jpeg')

    if GEMINI_API_KEY:
        try:
            return _parse_verification(_gemini_vision(prompt, image_b64, mime))
        except Exception as e:  # noqa: BLE001
            print(f'[ThriveAI] Gemini vision failed, falling back: {e}')

    if _ollama_available():
        try:
            return _parse_verification(_ollama_vision(prompt, image_b64))
        except Exception as e:  # noqa: BLE001
            print(f'[ThriveAI] Ollama vision failed, falling back: {e}')

    return _accept_fallback(lang)


def _accept_fallback(lang):
    if lang == 'bg':
        return True, 'Чудесно еко-действие! Приносът ти за по-зелен Бургас е потвърден. 🌿'
    return True, 'Great eco-action! Your contribution to a greener Burgas is verified. 🌿'
