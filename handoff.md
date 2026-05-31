# Thrive365 — Session Handoff

## Goal

Turn the existing Flask demo into a serious, production-quality AI accountability product for Burgas. Theme stays eco/Burgas. Three headline AI systems:

1. **AI image verification** — uploaded photo → verified/rejected + confidence % + written feedback
2. **AI chat assistant ("ThriveAI")** — context-aware chat that knows the user's streak, points, tasks, history
3. **Personalised tasks** — AI rewrites task copy to reference each user's neighborhood, transport habits, interests from onboarding

---

## Current state of the repo

### Branch situation — two diverged codebases

```
* team-main  6ea9486  [origin/main]  ← team's version (CURRENTLY CHECKED OUT)
             Merge pull request #1 from GKEnev24/swap-gemini-for-qwen

  main        8dea422  [origin/main: ahead 1, behind 6]  ← our React SPA work
             Add React/Vite SPA, modular Flask API, and AI feature services

Both diverged from: eabb0bd  Add .gitignore
```

**`team-main` (origin/main) is what's running right now.** The local `main` branch holds the React/Vite SPA work from a previous session — it is NOT merged and NOT reflected in the running app.

### The team's version (team-main / origin/main) — CONFIRMED WORKING

Full Flask + Jinja2 app, no React. Running on **port 5001**, demo login at `/login/demo`, admin at `/admin`.

```
app.py           — 875 lines, all routes inline, monolith
thriveai.py      — 610 lines, self-contained AI module (Qwen 2.5 local + heuristic fallback)
models.py        — 172 lines
database.py      — 266 lines
requirements.txt — Flask, Flask-Login, Flask-SQLAlchemy, Pillow, llama-cpp-python, huggingface-hub
assets/logo/     — logo.png
static/css/style.css
static/js/main.js, thriveai.js
templates/       — admin, base, index, leaderboard, login, map, onboarding,
                   profile, register, shop, tasks, thriveai
```

**All routes:**
| Route | Purpose |
|-------|---------|
| `/` | Landing (index.html) |
| `/register` | Email/password registration |
| `/login` | Email/password login |
| `/login/google` | Google OAuth |
| `/login/demo` | One-click demo (Ivan Demov, 325 pts, 3-day streak) |
| `/logout` | Logout |
| `/onboarding` | 5-step profile setup (neighborhood, transport, home, interests, commitment) |
| `/tasks` | Today's challenges (5 per day, personalized) |
| `/tasks/complete/<id>` | POST: submit photo for verification |
| `/map` | Leaflet map |
| `/api/map-data` | GeoJSON for map pins |
| `/leaderboard` | Rankings |
| `/profile` | User stats + badges |
| `/shop` + `/shop/redeem/<id>` | Prize shop, redemption |
| `/thriveai` | Chat UI page |
| `/api/thriveai/chat` | AJAX chat endpoint |
| `/admin` | Admin panel |
| `/admin/template/add` | Add task template |
| `/admin/template/<id>/delete` | Delete template |
| `/admin/template/<id>/toggle` | Toggle template active |
| `/admin/logout` | Admin logout |
| `/language` | Toggle EN/BG |

**Data models:**
- `User` — id, google_id, name, email, password_hash, points, streak, last_active_date, language_preference, is_admin, onboarded, neighborhood, transport, home_type, garden_access, household_size, interests (csv), time_commitment, activity_level, age_range
- `TaskTemplate` — curated library: title_en/bg, description_en/bg, points, category, effort, tags, neighborhood, location_name, lat/lng, active
- `AssignedTask` — per-user per-day snapshot (optionally AI-tailored): title_en/bg, description_en/bg, points, category, location_name, lat/lng, verified, photo_path, ai_feedback, completed_at
- `Prize` — title_en/bg, description_en/bg, points_cost, stock, image (emoji char)
- `Redemption` — user_id, prize_id, code, redeemed_at
- `Badge` / `UserBadge` — gamification badges

### thriveai.py — AI module (the key file)

Provider chain tried in order:
1. **Qwen 2.5-3B-Instruct-GGUF** (chat) — embedded via `llama_cpp`, weights from Hugging Face, cached locally after first download
2. **Qwen 2.5-VL-3B-Instruct-GGUF** (vision/verification) — same pattern, needs `mmproj` file too
3. **Heuristic fallback** — always works, zero deps, deterministic

Three public functions:
- `thriveai.chat(message, history, lang, context)` → `{reply, engine}`
- `thriveai.verify_image(task, photo_path, lang)` → `(verified: bool, feedback: str)`
- `thriveai.tailor_daily_tasks(profile, tasks, lang)` → personalised task list (same length, same order)

Model warm-up is kicked off on first HTTP request (background thread) to avoid the Flask debug-reloader double-load problem. App is fully usable on heuristic fallback while model loads.

**Important config env vars:**
```
QWEN_MODEL_REPO / QWEN_MODEL_FILE      — chat model (default Qwen2.5-3B-Instruct-GGUF)
QWEN_VISION_REPO / QWEN_VISION_FILE / QWEN_VISION_MMPROJ  — vision model
QWEN_CTX          — context window (default 4096)
QWEN_GPU_LAYERS   — GPU offload layers (default 0 = CPU)
VERIFY_MIN_CONFIDENCE — reject "verified" results below this % (default 50)
HF_HUB_DISABLE_XET=1 — already defaulted in thriveai.py (prevents HF Xet downloader hang on Python 3.14)
```

### What was visually confirmed running (screenshots taken this session)

- **Landing page** — logo, Create Account / Log In / Try Demo CTAs, "How it works" section
- **Today's Challenges** — 5 tasks, "✨ Personalised for you" badge, category emoji icons, Complete Task buttons, progress bar
- **ThriveAI chat** — greeting message loaded ("Hi Иван!"), 4 suggestion chips, input bar
- **Profile** — avatar initial, Cyrillic name, points/streak/tasks-done stat cards, badges grid with progress list
- **Prize Shop** — balance card, 2×2 prize grid, emoji icons, Redeem buttons
- **No JS console errors**

---

## Files actively being edited / touched this session

| File | What happened |
|------|--------------|
| `frontend/src/pages/Today.jsx` | Removed unused `import { motion, AnimatePresence } from 'framer-motion'` (line 3 deleted). File is on `main` branch, not `team-main`. |
| `.claude/launch.json` | Team's version had a Windows path (`.venv\Scripts\python.exe`). Fixed back to `python3` for macOS. Currently on `team-main`, the file has only the single `thrive365` API server entry (port 5001). |

---

## What failed and why (do not retry)

### From previous session (on local `main` / React SPA)

1. **`AnimatePresence mode="wait"` on AppShell route wrapper** — content stuck at opacity 0.23 on every route change. Removed entirely.
2. **React StrictMode + framer-motion** — StrictMode's double-mount freezes JS-driven entrance animations at `initial` state. StrictMode removed in `main.jsx`.
3. **Framer `motion.div` variant inheritance in Sheet** — y/opacity froze at closed values even with per-element `animate={}` props. Switched to pure CSS `transition-transform`.
4. **Tailwind `-translate-x-1/2` + framer translateY** — framer overwrote the same `transform` property, pushing sheet off-screen. Replaced with `inset-x-0 mx-auto`.
5. **Staggered `motion.div` lists** — random items stalled at opacity 0. Replaced with CSS keyframe `@keyframes rise` + `.rise` utility class.

**Rule:** anything with a JS-driven `initial → animate` transition is the suspect. Replace with `.rise` or a Tailwind `transition-*` class.

### This session

6. **`pip install llama-cpp-python` on Python 3.14** — needed to compile from source. Took ~2 minutes. No errors, compiled correctly (`import llama_cpp` works). Just slow, not broken.
7. **Stale SQLite database** — switching from `main` (our schema) to `team-main` (team's schema) caused `no such column: prizes.image` on startup. Fix: delete `instance/thrive365.db` and restart. Flask's `db.create_all()` recreates from scratch.
8. **Preview harness + existing Vite process** — `preview_start` refused to attach to an already-running Vite on port 5173. Had to kill the existing process first. For the team's Flask app, same issue: had to kill the existing process then use `preview_start`.
9. **`launch.json` wiped by git checkout** — checking out `team-main` restored the team's Windows `launch.json` (`.venv\Scripts\python.exe`). Fixed manually after checkout.

---

## Git state right now

```
Currently on: team-main (tracking origin/main)
Local main:   8dea422 — React SPA + modular API (1 commit ahead of origin/main's divergence point)
Stash:        19cf0cf — settings.local.json change stashed during checkout
```

To get back to our React SPA:
```bash
git stash pop
git checkout main
```

To stay on team's version:
```bash
# already here — team-main
python3 app.py  # http://localhost:5001
```

---

## How to run

```bash
cd /Users/valeritenev/Project/Thrive365

# Team's version (current branch: team-main)
python3 app.py          # http://localhost:5001
# Demo login:  http://localhost:5001/login/demo
# Admin panel: http://localhost:5001/admin  (password: admin123 or ADMIN_PASSWORD env)

# Our React SPA version (branch: main)
git checkout main
python3 app.py          # backend on :5001
cd frontend && npm run dev -- --host   # SPA on :5173
```

Port 5000 blocked by macOS AirPlay Receiver — that's why API is on 5001.
Node v26 installed via Homebrew.
`llama-cpp-python` and `huggingface-hub` are now installed system-wide on Python 3.14.

---

## Open decisions (whoever picks this up needs to decide)

### Big one: which architecture wins?

The two branches represent incompatible choices. You need to pick one base and port the best parts of the other onto it.

| | `team-main` (origin/main) | `main` (local) |
|--|--|--|
| Frontend | Jinja2 + vanilla JS | React/Vite SPA |
| AI | Local Qwen (no API key, ~2GB GGUF) | Claude API (Anthropic key required) |
| Auth | Register + login + Google OAuth | Demo only |
| Onboarding | ✅ 5-step profile | ❌ |
| Personalised tasks | ✅ AI-tailored copy | ❌ |
| Companion creature (Verda) | ❌ | ✅ 6 evolution stages |
| Modular services layer | ❌ | ✅ api/ + services/ |
| Bilingual EN/BG | ✅ (Jinja `lang` context) | ✅ (i18n.js) |

**Recommended path:** use `team-main` as the base (it has auth, onboarding, personalised tasks, and runs without any API key — better for a demo). Port Verda companion back in as a new `models.py` entry + `companion_service.py` + a `companion.html` template. The companion logic is fully self-contained in the `main` branch's `services/companion_service.py`.

---

## Immediate next steps (in priority order)

1. **Decide on architecture** (see above). If going with `team-main`:

2. **Port the Companion (Verda) back in** from `main` branch:
   - Copy `services/companion_service.py` → add to `team-main`
   - Add `Companion` model to `models.py` (see `main` branch for schema)
   - Add `/companion` route in `app.py`
   - Create `templates/companion.html` (can adapt the SVG from `frontend/src/components/Creature.jsx`)
   - Add "Companion" tab to `templates/base.html` nav

3. **Test the complete verification flow** — click "Complete Task" on a task card, upload a photo, confirm the AI feedback shows. The Qwen vision model will download on first use (~3GB, takes several minutes). Until it downloads, `verify_image()` returns `(True, "Great eco-action!")` heuristic fallback — tasks still complete, just no real AI feedback.

4. **Test the onboarding flow** — create a new account at `/register`, complete onboarding, verify that today's tasks have personalised copy. The demo user (`Ivan Demov`) has `onboarded=True` seeded but no profile fields set, so `tailor_daily_tasks` won't fire for him; use a fresh account.

5. **Map page** — navigate to `/map`, confirm Leaflet loads, pins appear, info cards open on click. This hasn't been tested this session.

6. **Admin panel** — visit `/admin` (password: `admin123`), add/toggle/delete task templates, confirm seeded data loads.

7. **Write ARCHITECTURE.md** — system diagram, DB schema, route table (already above), AI provider chain, onboarding → personalisation flow, MVP→prod roadmap (Postgres swap, Redis for map, S3 for uploads, background worker for Qwen inference, Sentry, rate limiting).

---

## Things worth flagging

- **Qwen model download** — first time `verify_image` or `tailor_daily_tasks` runs with the model available, it downloads ~2GB (chat) and ~3GB (vision) from Hugging Face into `~/.cache/huggingface/`. Set `HF_HOME` to a different path if disk space is tight.
- **`HF_HUB_DISABLE_XET=1`** is defaulted in `thriveai.py` — this prevents a known hang on Python 3.14 with the Xet downloader. Don't remove it.
- **Python 3.14 + Pillow** — Pillow was dropped from our `main` branch because its pinned wheel didn't build. The team added it back unpinned (`Pillow>=10.0.0`) and it installs fine on 3.14 now. It's used in `thriveai.py`'s `_prepare_image()` to downscale photos before sending to the vision model (max edge 768px = seconds of inference vs. minutes for a full phone photo).
- **VERIFY_MIN_CONFIDENCE=50** — any "verified" response from the model with confidence below 50 is automatically downgraded to a rejection. Prevents the model being sycophantic.
- **Demo user is seeded with `onboarded=True` but empty profile fields** — so `tailor_daily_tasks` won't personalise his tasks. This is fine for demoing the task list; to demo personalisation, register a new account and complete onboarding.
- **`instance/thrive365.db`** is gitignored. If you pull a branch with a schema change, delete the DB and restart to recreate cleanly.
- **Google OAuth** needs `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` in `.env`. Not required for demo.
