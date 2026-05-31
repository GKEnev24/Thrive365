"""
ThriveAI — the brain behind Thrive365.

A single, self-contained module that powers both the conversational assistant
and the eco-task photo verifier. It is designed to be SMART and FREE:

    Provider chain (tried in order, first one that works wins):

        1. Qwen 2.5 — runs EMBEDDED inside this process via llama-cpp-python.
                      The GGUF weights are downloaded once from Hugging Face and
                      cached locally, then loaded straight into the app — no
                      separate server, no API key, no quota. Just `pip install`.
        2. Heuristic — built-in rule-based brain (always works, zero deps).

The rest of the app never talks to a provider directly — it only calls
`thriveai.chat(...)` and `thriveai.verify_image(...)`. Swapping or adding a
provider later means editing this file and nothing else.

Configuration (all optional — sensible defaults, everything degrades gracefully):

    QWEN_MODEL_REPO       HF repo for the chat GGUF
                          (default: Qwen/Qwen2.5-3B-Instruct-GGUF)
    QWEN_MODEL_FILE       GGUF filename within that repo
                          (default: qwen2.5-3b-instruct-q4_k_m.gguf)
    QWEN_VISION_REPO      HF repo for the vision GGUF + mmproj
                          (default: ggml-org/Qwen2.5-VL-3B-Instruct-GGUF)
    QWEN_VISION_FILE      vision GGUF filename
                          (default: Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf)
    QWEN_VISION_MMPROJ    multimodal projector filename
                          (default: mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf)
    QWEN_CTX              context window in tokens (default: 4096)
    QWEN_GPU_LAYERS       layers to offload to GPU, -1 = all (default: 0 = CPU)
"""

import os
import json
import base64
import threading
import traceback
import concurrent.futures

# Hugging Face's native Xet downloader (hf_xet) can hang at 0% on some setups —
# notably Python 3.14 — leaving model downloads stuck forever. Default to the
# classic, reliable LFS downloader. Set HF_HUB_DISABLE_XET=0 to opt back into Xet.
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')

# ── Configuration ────────────────────────────────────────────────────────────

QWEN_MODEL_REPO = os.environ.get('QWEN_MODEL_REPO', 'Qwen/Qwen2.5-3B-Instruct-GGUF').strip()
QWEN_MODEL_FILE = os.environ.get(
    'QWEN_MODEL_FILE', 'qwen2.5-3b-instruct-q4_k_m.gguf').strip()

QWEN_VISION_REPO = os.environ.get(
    'QWEN_VISION_REPO', 'ggml-org/Qwen2.5-VL-3B-Instruct-GGUF').strip()
QWEN_VISION_FILE = os.environ.get(
    'QWEN_VISION_FILE', 'Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf').strip()
QWEN_VISION_MMPROJ = os.environ.get(
    'QWEN_VISION_MMPROJ', 'mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf').strip()

try:
    QWEN_CTX = int(os.environ.get('QWEN_CTX', '4096'))
except ValueError:
    QWEN_CTX = 4096
try:
    QWEN_GPU_LAYERS = int(os.environ.get('QWEN_GPU_LAYERS', '0'))
except ValueError:
    QWEN_GPU_LAYERS = 0

# Photo verification strictness: if the model passes a photo but reports a confidence
# below this (0-100), we override it to a rejection. Higher = stricter. 0 disables.
# Default 60 — borderline "maybe this is the right action" passes are downgraded.
# A genuine, clear photo of the task scores 80+ on Qwen 2.5-VL; raising the floor
# from 50 → 60 trims the false-positive band without blocking honest users.
try:
    VERIFY_MIN_CONFIDENCE = int(os.environ.get('VERIFY_MIN_CONFIDENCE', '60'))
except ValueError:
    VERIFY_MIN_CONFIDENCE = 60

# Hard timeout (seconds) on the task-personalisation Qwen call. If the model
# can't respond within this window we return the original curated task copy.
# This protects the onboarding/login redirect from a slow CPU inference
# (Qwen 3B can take 30-60s for a multi-task JSON rewrite on a Mac CPU).
try:
    TAILOR_TIMEOUT_S = float(os.environ.get('THRIVE_TAILOR_TIMEOUT', '5'))
except ValueError:
    TAILOR_TIMEOUT_S = 5.0

# Hard timeout (seconds) on conversational chat. Qwen 3B on CPU generates ~10-30
# tokens/sec; 500 tokens worst-case = ~16-50s. We bound the wait to 25s and
# fall back to the heuristic brain if it overruns. Override with THRIVE_CHAT_TIMEOUT.
try:
    CHAT_TIMEOUT_S = float(os.environ.get('THRIVE_CHAT_TIMEOUT', '25'))
except ValueError:
    CHAT_TIMEOUT_S = 25.0

# Hard timeout (seconds) on photo verification. Vision inference is heavier
# than text — a downscaled 768px photo plus ~300 tokens of JSON output takes
# 15-40s on a Mac CPU. We bound at 45s; on timeout we auto-accept with a
# friendly message so a slow model doesn't punish the user.
try:
    VERIFY_TIMEOUT_S = float(os.environ.get('THRIVE_VERIFY_TIMEOUT', '45'))
except ValueError:
    VERIFY_TIMEOUT_S = 45.0

# Background-pool workers used to run Qwen inference with a wall-clock timeout.
# Single worker per pool because llama-cpp models are NOT safe for concurrent
# inference on the same instance. Separate pools per feature so a slow vision
# call doesn't block the next chat reply (and vice versa).
_tailor_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1,
                                                    thread_name_prefix='thriveai-tailor')
_chat_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1,
                                                   thread_name_prefix='thriveai-chat')
_verify_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1,
                                                     thread_name_prefix='thriveai-verify')

# How many past turns of conversation to keep as context.
MAX_HISTORY_TURNS = 12

MEDIA_TYPES = {
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
    'gif': 'image/gif', 'webp': 'image/webp',
}

# ── Embedded Qwen 2.5 (llama-cpp-python) ──────────────────────────────────────
#
# Models load lazily on first use and stay resident. Loading is guarded by a lock
# so concurrent requests don't trigger two downloads/loads. If llama-cpp-python
# isn't installed or a model can't be loaded, the relevant feature degrades to the
# heuristic brain instead of crashing — the app always keeps working.

_chat_model = None          # llama_cpp.Llama for text chat
_vision_model = None        # llama_cpp.Llama (+ mmproj) for image verification
_chat_failed = False        # don't retry a load that already hard-failed
_vision_failed = False
_load_lock = threading.Lock()


def _llama_import():
    """Import llama_cpp lazily; return the module or None if unavailable."""
    try:
        import llama_cpp  # noqa: PLC0415 — optional heavy dep, imported on demand
        return llama_cpp
    except ImportError:
        return None


def _get_chat_model(block=True):
    """Return the embedded Qwen 2.5 chat model, loading it if needed. None on
    failure or, when block=False, if it isn't loaded yet — request handlers pass
    block=False so they fall back to the heuristic brain instead of waiting on a
    multi-minute first-run download. The background warmup thread does the load."""
    global _chat_model, _chat_failed
    if _chat_model is not None or _chat_failed:
        return _chat_model
    if not block:
        return None
    with _load_lock:
        if _chat_model is not None or _chat_failed:
            return _chat_model
        llama_cpp = _llama_import()
        if llama_cpp is None:
            print('[ThriveAI] llama-cpp-python not installed — using heuristic brain. '
                  'Run: pip install -r requirements.txt')
            _chat_failed = True
            return None
        try:
            print(f'[ThriveAI] Loading Qwen 2.5 chat model ({QWEN_MODEL_FILE})… '
                  'first run downloads the weights from Hugging Face.')
            _chat_model = llama_cpp.Llama.from_pretrained(
                repo_id=QWEN_MODEL_REPO,
                filename=QWEN_MODEL_FILE,
                n_ctx=QWEN_CTX,
                n_gpu_layers=QWEN_GPU_LAYERS,
                verbose=False,
            )
            print('[ThriveAI] Qwen 2.5 chat model ready.')
        except Exception as e:  # noqa: BLE001 — any load error must not crash the app
            print(f'[ThriveAI] could not load Qwen chat model, using heuristic: {e}')
            _chat_failed = True
        return _chat_model


def _get_vision_model(block=True):
    """Return the embedded Qwen 2.5-VL vision model, loading it if needed. None on
    failure or, when block=False, if it isn't loaded yet (see _get_chat_model)."""
    global _vision_model, _vision_failed
    if _vision_model is not None or _vision_failed:
        return _vision_model
    if not block:
        return None
    with _load_lock:
        if _vision_model is not None or _vision_failed:
            return _vision_model
        llama_cpp = _llama_import()
        if llama_cpp is None:
            _vision_failed = True
            return None
        try:
            from llama_cpp.llama_chat_format import Qwen25VLChatHandler
            from huggingface_hub import hf_hub_download
        except ImportError as e:
            print(f'[ThriveAI] vision deps unavailable, photos auto-accepted: {e}')
            _vision_failed = True
            return None
        try:
            print(f'[ThriveAI] Loading Qwen 2.5-VL vision model ({QWEN_VISION_FILE})…')
            mmproj_path = hf_hub_download(QWEN_VISION_REPO, QWEN_VISION_MMPROJ)
            handler = Qwen25VLChatHandler(clip_model_path=mmproj_path, verbose=False)
            _vision_model = llama_cpp.Llama.from_pretrained(
                repo_id=QWEN_VISION_REPO,
                filename=QWEN_VISION_FILE,
                chat_handler=handler,
                n_ctx=max(QWEN_CTX, 4096),
                n_gpu_layers=QWEN_GPU_LAYERS,
                verbose=False,
            )
            print('[ThriveAI] Qwen 2.5-VL vision model ready.')
        except Exception as e:  # noqa: BLE001
            print(f'[ThriveAI] could not load Qwen vision model, photos auto-accepted: {e}')
            _vision_failed = True
        return _vision_model


def warmup(vision=False):
    """Pre-load the model(s) so the first user request isn't slow. Safe to call
    from a background thread at app startup; never raises."""
    _get_chat_model()
    if vision:
        _get_vision_model()


def active_engine():
    """Name of the engine that would handle a request RIGHT NOW (non-blocking).
    While the model is still loading/downloading, this is 'heuristic' — requests
    use the heuristic brain until the background warmup makes Qwen resident."""
    return 'qwen' if _chat_model is not None else 'heuristic'


# ── System prompt (ThriveAI's personality + live app knowledge) ───────────────

def build_system_prompt(lang, context=None):
    """Build the system instruction. `context` is an optional dict with live
    user/app state so ThriveAI can give grounded, personalised answers."""
    reply_lang = 'Bulgarian (български)' if lang == 'bg' else 'English'

    base = f"""You are ThriveAI — a knowledgeable, professional, genuinely helpful AI assistant. \
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
- Professional, warm and respectful in tone. Do NOT use emojis. When it fits naturally, \
you can gently connect things back to sustainable living, but don't force it.
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
                status = 'done' if t.get('done') else 'not yet done'
                lines.append(
                    f"    - \"{t['title']}\" (+{t['points']} pts, {t['location']}) — {status}")
        if context.get('earned_badges'):
            lines.append(f"- Badges earned: {', '.join(context['earned_badges'])}")
        if context.get('next_prize'):
            np = context['next_prize']
            lines.append(
                f"- Closest affordable reward: \"{np['title']}\" costs {np['cost']} pts")
        base += "\n".join(lines)

    return base


# ── Embedded Qwen 2.5 inference helpers ───────────────────────────────────────

def _qwen_chat(system, history, message):
    model = _get_chat_model()
    if model is None:
        raise RuntimeError('Qwen chat model unavailable')
    messages = [{'role': 'system', 'content': system}]
    for turn in history:
        role = 'assistant' if turn.get('role') == 'assistant' else 'user'
        messages.append({'role': role, 'content': turn.get('content', '')})
    messages.append({'role': 'user', 'content': message})
    # max_tokens 500 (was 1500): chat replies are conversational, not essays.
    # On a Mac CPU this bounds the worst-case generation time to ~20s instead of ~60s.
    out = model.create_chat_completion(
        messages=messages, temperature=0.7, max_tokens=500)
    text = out['choices'][0]['message']['content'].strip()
    if not text:
        raise ValueError('Qwen returned empty content')
    return text


def _qwen_json(prompt, temperature=0.6, max_tokens=2000):
    """Single-shot Qwen call that returns parsed JSON (forced JSON output)."""
    model = _get_chat_model()
    if model is None:
        raise RuntimeError('Qwen chat model unavailable')
    out = model.create_chat_completion(
        messages=[{'role': 'user', 'content': prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={'type': 'json_object'},
    )
    text = out['choices'][0]['message']['content'].strip()
    if not text:
        raise ValueError('Qwen returned empty content')
    return json.loads(text)


def _qwen_vision(prompt, image_b64, mime):
    model = _get_vision_model()
    if model is None:
        raise RuntimeError('Qwen vision model unavailable')
    data_uri = f'data:{mime};base64,{image_b64}'
    out = model.create_chat_completion(
        messages=[{
            'role': 'user',
            'content': [
                {'type': 'image_url', 'image_url': {'url': data_uri}},
                {'type': 'text', 'text': prompt},
            ],
        }],
        temperature=0.1,
        max_tokens=300,
    )
    text = out['choices'][0]['message']['content'].strip()
    if not text:
        raise ValueError('Qwen vision returned empty content')
    return text


# ── Heuristic provider (always-on, offline, zero-cost fallback) ───────────────

def _heuristic_chat(message, lang, context=None):
    """A rule-based brain. Not "smart", but never fails and stays on-topic."""
    msg = (message or '').lower()
    bg = lang == 'bg'
    ctx = context or {}

    def has(*words):
        return any(w in msg for w in words)

    if has('point', 'точк', 'score', 'резултат') and ctx.get('points') is not None:
        if bg:
            return f"В момента имаш {ctx['points']} точки. Изпълни още една днешна задача, за да добавиш."
        return f"You currently have {ctx['points']} points. Complete one of today's tasks to add more."

    if has('streak', 'серия') and ctx.get('streak') is not None:
        if bg:
            return f"Серията ти е {ctx['streak']} дни. Влизай и изпълнявай по една задача всеки ден, за да я задържиш."
        return f"Your streak is {ctx['streak']} day(s). Complete a task each day to keep it alive."

    if has('task', 'задач', 'today', 'днес', 'challenge', 'предизвикател', 'do '):
        tasks = ctx.get('today_tasks') or []
        pending = [t for t in tasks if not t.get('done')]
        if pending:
            t = pending[0]
            if bg:
                return f"Опитай „{t['title']}“ ({t['location']}) за +{t['points']} точки. Качи снимка, за да я потвърдя."
            return f"Try \"{t['title']}\" ({t['location']}) for +{t['points']} points. Upload a photo and I'll verify it."
        if bg:
            return "Изпълни всички днешни задачи. Върни се утре за нови предизвикателства."
        return "You've completed all of today's tasks. Come back tomorrow for new challenges."

    if has('shop', 'reward', 'prize', 'redeem', 'магазин', 'наград', 'купи'):
        if bg:
            return "В Магазина можеш да замениш точки за реални награди в Бургас — кафе, карта за транспорт, еко чанта или засаждане на дърво."
        return "In Rewards you can spend points on real Burgas partner offers — coffee, a bus pass, an eco tote, or planting a tree."

    if has('badge', 'значк', 'achievement', 'постижен'):
        if bg:
            return "Значките се отключват с напредъка ти: първа задача, 100/500/1000 точки и 7-дневна серия. Виж ги в Профила."
        return "Badges unlock as you progress: first task, 100/500/1000 points, and a 7-day streak. Check them in your Profile."

    if has('map', 'карта', 'where', 'къде', 'location', 'локац'):
        if bg:
            return "Картата показва къде са днешните задачи и топлинна карта на активността в Бургас. Виж раздел „Карта“."
        return "The Map shows where today's tasks are plus a community activity heatmap of Burgas. Open the Map tab."

    if has('photo', 'verif', 'снимк', 'провер', 'upload', 'качи'):
        if bg:
            return "За да изпълниш задача, качи снимка на действието си. Аз я преглеждам и ако е автентична — печелиш точките."
        return "To complete a task, upload a photo of the action. I review it and if it's authentic, you earn the points."

    if has('recycl', 'рецикл', 'waste', 'боклук', 'отпадъц', 'trash'):
        if bg:
            return "Разделяй отпадъците: пластмаса, хартия, стъкло и био. В Бургас има цветни контейнери в повечето квартали."
        return "Separate your waste: plastic, paper, glass, and organics. Burgas has colour-coded bins in most neighbourhoods."

    if has('hello', 'hi ', 'hey', 'здрав', 'здравей', 'привет') or msg.strip() in ('hi', 'hello', 'здрасти'):
        name = ctx.get('name', '')
        if bg:
            return f"Здравей{', ' + name if name else ''}. Аз съм ThriveAI. Питай ме за днешните задачи, точки, награди или съвети за по-зелен Бургас."
        return f"Hi{', ' + name if name else ''}, I'm ThriveAI. Ask me about today's tasks, your points, rewards, or tips for a greener Burgas."

    if bg:
        return ("В момента работя в опростен офлайн режим, затова мога да помагам най-вече с "
                "Thrive365 — задачи, точки, значки, награди и еко-съвети. За пълни отговори на "
                "всякакви въпроси, инсталирай зависимостите (pip install -r requirements.txt), "
                "за да заредиш Qwen 2.5. С какво да помогна?")
    return ("I'm running in a simplified offline mode right now, so I can mainly help with "
            "Thrive365 — tasks, points, badges, rewards, and sustainability tips. To answer any "
            "question fully, install the dependencies (pip install -r requirements.txt) so "
            "Qwen 2.5 can load. How can I help?")


# ── Public API: chat ──────────────────────────────────────────────────────────

def chat(message, history=None, lang='en', context=None):
    """Generate a ThriveAI reply.

    Returns a dict: {'reply': str, 'engine': 'qwen'|'heuristic'}.
    Never raises — always degrades to the heuristic brain.
    """
    history = (history or [])[-MAX_HISTORY_TURNS:]
    system = build_system_prompt(lang, context)

    if _get_chat_model(block=False) is not None:
        try:
            # Hard wall-clock timeout so a slow CPU inference can't make the
            # browser give up with "could not reach the server". On timeout we
            # fall back to the heuristic brain rather than leave the user hanging.
            future = _chat_pool.submit(_qwen_chat, system, history, message)
            reply = future.result(timeout=CHAT_TIMEOUT_S)
            return {'reply': reply, 'engine': 'qwen'}
        except concurrent.futures.TimeoutError:
            print(f'[ThriveAI] Qwen chat exceeded {CHAT_TIMEOUT_S}s, using heuristic. '
                  'Subsequent calls will be faster as the model stays resident.')
        except Exception as e:  # noqa: BLE001
            print(f'[ThriveAI] Qwen chat failed, falling back: {e}')

    return {'reply': _heuristic_chat(message, lang, context), 'engine': 'heuristic'}


# ── Public API: personalise selected tasks for a user ─────────────────────────

def tailor_daily_tasks(profile, tasks, lang='en'):
    """Personalise a list of already-selected curated tasks for one user.

    `profile` is a small dict (neighborhood, transport, interests, ...).
    `tasks` is a list of dicts each with title_en/title_bg/description_en/
    description_bg/category. Returns a list of the same length/order with the
    text personalised. ALWAYS safe: if no AI provider or anything goes wrong,
    the original curated tasks are returned unchanged.
    """
    if not tasks or _get_chat_model(block=False) is None:
        return tasks

    compact = [{
        'title_en': t['title_en'], 'title_bg': t['title_bg'],
        'description_en': t['description_en'], 'description_bg': t['description_bg'],
        'category': t.get('category', ''),
    } for t in tasks]

    prompt = f"""You personalise eco-task wording for Thrive365, a sustainability app in \
Burgas, Bulgaria. Rewrite each task below so it feels tailored to THIS resident, while \
keeping the SAME eco-action and difficulty.

Resident profile (JSON): {json.dumps(profile, ensure_ascii=False)}

Tasks to personalise (JSON array): {json.dumps(compact, ensure_ascii=False)}

Rules for EACH task:
- Keep the exact same real-world action and any location — do not invent a different task.
- ALWAYS keep the photo-proof requirement (the user must photograph their action).
- You may reference their neighborhood, transport or interests to make it motivating.
- Keep titles short (max ~8 words). Keep descriptions 1-2 sentences.
- Provide BOTH English and Bulgarian versions.

Return ONLY a JSON object with a "tasks" array of the same length and order, each item:
{{"tasks": [{{"title_en": "...", "title_bg": "...", "description_en": "...", "description_bg": "..."}}]}}"""

    try:
        # Run with a hard wall-clock timeout. Onboarding redirects MUST NOT wait
        # on a 30-60s CPU inference — we'd rather show the curated task copy
        # immediately than have the browser hang and the user think the app
        # has crashed. max_tokens kept tight (800 ≈ enough for 5 short tasks
        # × 2 languages) so the typical happy-path call returns well under 5s.
        future = _tailor_pool.submit(_qwen_json, prompt, 0.6, 800)
        try:
            data = future.result(timeout=TAILOR_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            print(f'[ThriveAI] tailor_daily_tasks exceeded {TAILOR_TIMEOUT_S}s, '
                  'returning curated copy. The model keeps generating in the '
                  'background — next task generation will be cached and instant.')
            return tasks
        if isinstance(data, dict):
            data = data.get('tasks')
        if not isinstance(data, list):
            return tasks
        out = []
        for i, original in enumerate(tasks):
            merged = dict(original)
            item = data[i] if i < len(data) and isinstance(data[i], dict) else {}
            for key in ('title_en', 'title_bg', 'description_en', 'description_bg'):
                val = item.get(key)
                if isinstance(val, str) and len(val.strip()) >= 8:
                    merged[key] = val.strip()
            out.append(merged)
        return out
    except Exception as e:  # noqa: BLE001 — never break task assignment
        print(f'[ThriveAI] task tailoring failed, using curated text: {e}')
        return tasks


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

    Returns (verified: bool, feedback: str). Never raises. Blocks on the
    vision model load (which can take 10-60s on first call) so we actually
    verify the photo instead of silently auto-accepting. Hard timeout via
    THRIVE_VERIFY_TIMEOUT prevents indefinite hangs.
    """
    prompt = _verification_prompt(task, lang)
    try:
        image_b64, mime = _prepare_image(photo_path)
    except Exception as e:  # noqa: BLE001 — corrupt/unsupported image must not 500
        print(f'[ThriveAI] could not read/process photo: {e}')
        return _accept_fallback(lang)

    # block=True: we want to actually run the model on the photo. Auto-accepting
    # on every upload because the model hadn't warmed up yet was making
    # verification look broken — the user saw "verified" with no real check.
    # The vision-pool wrapper still bounds total time at VERIFY_TIMEOUT_S.
    def _run():
        if _get_vision_model(block=True) is None:
            return None
        return _qwen_vision(prompt, image_b64, mime)

    try:
        future = _verify_pool.submit(_run)
        raw = future.result(timeout=VERIFY_TIMEOUT_S)
        if raw is None:
            return _accept_fallback(lang)
        return _parse_verification(raw)
    except concurrent.futures.TimeoutError:
        print(f'[ThriveAI] vision verification exceeded {VERIFY_TIMEOUT_S}s, accepting. '
              'The model will be resident for subsequent uploads (much faster).')
        return _accept_fallback(lang)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        print(f'[ThriveAI] Qwen vision failed, falling back: {e}')
        return _accept_fallback(lang)


# Longest edge (px) we feed the vision model. A phone photo is ~3000px, which the
# model expands into a huge number of image tokens — minutes of CPU inference. A
# bike vs. a shower is just as recognisable at 768px, and it runs in seconds.
VERIFY_MAX_EDGE = 768


def _prepare_image(photo_path):
    """Downscale + re-encode the photo so vision inference is fast. Returns
    (base64_str, mime). Falls back to the raw file if Pillow isn't available."""
    try:
        import io
        from PIL import Image, ImageOps
    except ImportError:
        ext = photo_path.lower().rsplit('.', 1)[-1]
        with open(photo_path, 'rb') as f:
            return base64.standard_b64encode(f.read()).decode('utf-8'), \
                MEDIA_TYPES.get(ext, 'image/jpeg')

    with Image.open(photo_path) as img:
        img = ImageOps.exif_transpose(img)  # honour camera orientation
        img = img.convert('RGB')
        img.thumbnail((VERIFY_MAX_EDGE, VERIFY_MAX_EDGE))  # in-place, keeps aspect
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode('utf-8'), 'image/jpeg'


def _accept_fallback(lang):
    if lang == 'bg':
        return True, 'Действието е потвърдено. Благодарим за приноса към по-зелен Бургас.'
    return True, 'Action verified. Thank you for contributing to a greener Burgas.'
