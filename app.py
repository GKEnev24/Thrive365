import os
import uuid
import random
from datetime import datetime, date, timedelta
from functools import wraps

from dotenv import load_dotenv
load_dotenv()  # load .env (QWEN_* overrides, etc.) before anything reads os.environ

import threading

from flask import (Flask, render_template, redirect, url_for, session,
                   request, jsonify, flash)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from werkzeug.utils import secure_filename
from sqlalchemy.exc import IntegrityError

from models import (db, User, TaskTemplate, AssignedTask, Prize, Redemption,
                    Badge, UserBadge, Hazard, RouteCompletion)
from database import seed_db
from services import routing as routing_service
import thriveai

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'thrive365-dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///thrive365.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # phone JPGs can exceed 16 MB

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

oauth = OAuth(app)

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

# Warm up the embedded Qwen 2.5 model in the background, kicked off by the first
# HTTP request rather than at import time. Flask's debug reloader runs two
# processes (a watcher + the serving worker); only the worker handles requests,
# so this fires exactly once — avoiding the double download / double 2 GB load
# that happens if both processes warm up. The app stays usable while it loads:
# ThriveAI falls back to the heuristic brain until the model is resident.
_warmup_started = False
_warmup_lock = threading.Lock()


@app.errorhandler(413)
def _too_large(_e):
    # Return JSON (not Werkzeug's default HTML page) so the photo-upload frontend
    # can show a real message instead of a generic "Upload Error".
    return jsonify({
        'success': False, 'verified': False,
        'message': 'That photo is too large. Please upload an image under 32 MB.'
    }), 413


@app.before_request
def _kickoff_warmup():
    global _warmup_started
    if _warmup_started:
        return
    with _warmup_lock:
        if _warmup_started:
            return
        _warmup_started = True
        threading.Thread(
            target=thriveai.warmup, kwargs={'vision': True}, daemon=True).start()


google = None
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    google = oauth.register(
        name='google',
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'}
    )

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
TASKS_PER_DAY = 5

# ── Onboarding option catalogue (value + bilingual label + icon) ──────────────
NEIGHBORHOODS = ['Център', 'Лазур', 'Възраждане', 'Братя Миладинови', 'Славейков',
                 'Изгрев', 'Зорница', 'Меден рудник', 'Сарафово', 'Крайморие',
                 'Победа', 'Долно Езерово', 'Горно Езерово', 'Акациите', 'Банево',
                 'Ветрен', 'Друг / Other']

ONBOARD_OPTIONS = {
    'transport': [
        {'value': 'car',     'en': 'Mostly car',       'bg': 'Предимно кола',         'icon': 'bus'},
        {'value': 'bike',    'en': 'Bicycle',          'bg': 'Колело',                'icon': 'bike'},
        {'value': 'walk',    'en': 'On foot',          'bg': 'Пеша',                  'icon': 'user'},
        {'value': 'transit', 'en': 'Public transport', 'bg': 'Градски транспорт',     'icon': 'bus'},
        {'value': 'mixed',   'en': 'A mix',            'bg': 'Смесено',               'icon': 'arrow-right'},
    ],
    'home_type': [
        {'value': 'apartment', 'en': 'Apartment', 'bg': 'Апартамент', 'icon': 'home'},
        {'value': 'house',     'en': 'House',     'bg': 'Къща',       'icon': 'home'},
    ],
    'garden_access': [
        {'value': 'none',    'en': 'No outdoor space', 'bg': 'Без външно пространство', 'icon': 'home'},
        {'value': 'balcony', 'en': 'Balcony',          'bg': 'Балкон',                  'icon': 'leaf'},
        {'value': 'yard',    'en': 'Small yard',       'bg': 'Малък двор',              'icon': 'tree'},
        {'value': 'garden',  'en': 'Garden',           'bg': 'Градина',                 'icon': 'tree'},
    ],
    'interests': [
        {'value': 'recycling', 'en': 'Recycling',          'bg': 'Рециклиране',              'icon': 'recycle'},
        {'value': 'nature',    'en': 'Nature & planting',  'bg': 'Природа и засаждане',      'icon': 'leaf'},
        {'value': 'cycling',   'en': 'Cycling & transport','bg': 'Колоездене и транспорт',   'icon': 'bike'},
        {'value': 'energy',    'en': 'Saving energy',      'bg': 'Пестене на енергия',       'icon': 'bulb'},
        {'value': 'water',     'en': 'Saving water',       'bg': 'Пестене на вода',          'icon': 'droplet'},
        {'value': 'community', 'en': 'Community action',   'bg': 'Общностни действия',       'icon': 'handshake'},
        {'value': 'food',      'en': 'Sustainable food',   'bg': 'Устойчива храна',          'icon': 'salad'},
        {'value': 'waste',     'en': 'Litter & cleanups',  'bg': 'Боклук и почистване',      'icon': 'broom'},
    ],
    'time_commitment': [
        {'value': 'low',    'en': 'A few minutes',     'bg': 'Няколко минути',           'icon': 'calendar'},
        {'value': 'medium', 'en': '15–30 min/day',     'bg': '15–30 мин/ден',            'icon': 'calendar'},
        {'value': 'high',   'en': 'I want a challenge','bg': 'Искам предизвикателство',  'icon': 'flame'},
    ],
    'activity_level': [
        {'value': 'low',    'en': 'Light & easy', 'bg': 'Леко и спокойно', 'icon': 'leaf'},
        {'value': 'medium', 'en': 'Moderate',     'bg': 'Умерено',         'icon': 'user'},
        {'value': 'high',   'en': 'Very active',  'bg': 'Много активно',   'icon': 'flame'},
    ],
    'age_range': [
        {'value': 'teen',   'en': 'Under 18', 'bg': 'Под 18', 'icon': 'user'},
        {'value': 'adult',  'en': '18–60',    'bg': '18–60',  'icon': 'user'},
        {'value': 'senior', 'en': '60+',      'bg': '60+',    'icon': 'user'},
    ],
}

_VALID = {k: {o['value'] for o in v} for k, v in ONBOARD_OPTIONS.items()}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def get_lang():
    if current_user.is_authenticated:
        return current_user.language_preference or 'en'
    return session.get('lang', 'en')


@app.context_processor
def inject_globals():
    return {
        'lang': get_lang(),
        'current_year': datetime.now().year,
        'today': date.today(),
        'google_enabled': bool(google),
    }


@app.template_filter('fdate')
def fdate(value, fmt='%d %b %Y'):
    """Cross-platform date/datetime formatting. glibc no-pad tokens (%-d, %-m,
    %-H ...) crash on Windows, so substitute them manually. Works for date and
    datetime; only touches the time attributes actually requested."""
    if value is None:
        return ''
    substitutions = {
        '%-d': lambda: str(value.day),
        '%-m': lambda: str(value.month),
        '%-H': lambda: str(value.hour),
        '%-I': lambda: str((value.hour % 12) or 12),
        '%-M': lambda: str(value.minute),
        '%-S': lambda: str(value.second),
    }
    for token, resolver in substitutions.items():
        if token in fmt:
            fmt = fmt.replace(token, resolver())
    return value.strftime(fmt)


@app.before_request
def enforce_onboarding():
    """Authenticated users must finish onboarding before using the app."""
    if not current_user.is_authenticated or current_user.onboarded:
        return
    allowed = {'onboarding', 'logout', 'switch_language', 'static',
               'login', 'register', 'login_google', 'google_callback',
               'login_demo', 'index'}
    if request.endpoint in allowed:
        return
    return redirect(url_for('onboarding'))


# ─── Gamification helpers ───────────────────────────────────────────────────────

def update_streak(user):
    today = date.today()
    if user.last_active_date is None:
        user.streak = 1
    elif user.last_active_date == today:
        pass
    elif user.last_active_date == today - timedelta(days=1):
        user.streak += 1
    else:
        user.streak = 1
    user.last_active_date = today


def check_and_award_badges(user):
    new_badges = []
    earned_ids = {ub.badge_id for ub in UserBadge.query.filter_by(user_id=user.id).all()}
    completed_count = AssignedTask.query.filter_by(user_id=user.id, verified=True).count()

    for badge in Badge.query.all():
        if badge.id in earned_ids:
            continue
        earned = (
            (badge.condition_type == 'points' and user.points >= badge.condition_value) or
            (badge.condition_type == 'streak' and user.streak >= badge.condition_value) or
            (badge.condition_type == 'tasks' and completed_count >= badge.condition_value)
        )
        if earned:
            db.session.add(UserBadge(user_id=user.id, badge_id=badge.id))
            new_badges.append(badge)
    return new_badges


# ─── Personalised task assignment ───────────────────────────────────────────────

_EFFORT_RANK = {'low': 1, 'medium': 2, 'high': 3}


def _template_eligible(user, t):
    """Hard requirements: drop tasks the user physically cannot do."""
    tags = set(t.tag_list)
    garden = user.garden_access or 'none'
    if 'needs_garden' in tags and garden not in ('yard', 'garden'):
        return False
    if 'needs_outdoor_space' in tags and garden == 'none':
        return False
    return True


def _score_template(user, t):
    """Higher = better fit for this user's profile."""
    score = 0.0
    interests = set(user.interest_list)
    if t.category in interests:
        score += 4
    if t.neighborhood and user.neighborhood and t.neighborhood == user.neighborhood:
        score += 3
    if not t.neighborhood:
        score += 1  # city-wide tasks are always doable
    if t.category == 'transport' and (user.transport or '') == 'car':
        score += 3  # biggest impact: nudge a driver toward greener transport
    if t.category == 'cycling' and (user.transport or '') in ('bike', 'mixed'):
        score += 1.5
    # effort vs available time
    if _EFFORT_RANK.get(t.effort, 2) <= _EFFORT_RANK.get(user.time_commitment or 'medium', 2):
        score += 1.5
    else:
        score -= 1.0
    if t.effort == 'high' and (user.activity_level or 'medium') == 'low':
        score -= 1.5
    return score


def _pick_diverse(templates, count):
    """Take the best, but cap each category at 2 for variety."""
    chosen, cat_count = [], {}
    for t in templates:
        if cat_count.get(t.category, 0) >= 2:
            continue
        chosen.append(t)
        cat_count[t.category] = cat_count.get(t.category, 0) + 1
        if len(chosen) >= count:
            return chosen
    for t in templates:  # backfill if diversity cap left us short
        if t not in chosen:
            chosen.append(t)
            if len(chosen) >= count:
                break
    return chosen[:count]


def _profile_dict(user):
    return {
        'neighborhood': user.neighborhood,
        'transport': user.transport,
        'home_type': user.home_type,
        'garden_access': user.garden_access,
        'interests': user.interest_list,
        'time_commitment': user.time_commitment,
    }


def generate_daily_tasks(user, target_date=None, count=TASKS_PER_DAY):
    """Return the user's tasks for the day, creating them from the curated
    library (profile-filtered, scored, diversified, AI-tailored) if needed."""
    target_date = target_date or date.today()
    existing = (AssignedTask.query
                .filter_by(user_id=user.id, date=target_date)
                .order_by(AssignedTask.id).all())
    if existing:
        return existing

    templates = TaskTemplate.query.filter_by(active=True).all()
    eligible = [t for t in templates if _template_eligible(user, t)] or templates
    rng = random.Random(f"{user.id}-{target_date.isoformat()}")
    ranked = sorted(eligible, key=lambda t: (_score_template(user, t), rng.random()), reverse=True)
    chosen = _pick_diverse(ranked, count)

    base = [{
        'title_en': t.title_en, 'title_bg': t.title_bg,
        'description_en': t.description_en, 'description_bg': t.description_bg,
        'category': t.category,
    } for t in chosen]

    tailored = thriveai.tailor_daily_tasks(_profile_dict(user), base,
                                           lang=user.language_preference or 'en')

    rows = []
    for template, text in zip(chosen, tailored):
        at = AssignedTask(
            user_id=user.id, template_id=template.id, date=target_date,
            title_en=text['title_en'], title_bg=text['title_bg'],
            description_en=text['description_en'], description_bg=text['description_bg'],
            points=template.points, category=template.category,
            location_name=template.location_name, lat=template.lat, lng=template.lng,
        )
        db.session.add(at)
        rows.append(at)
    db.session.commit()
    return rows


# ─── Auth routes ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('tasks'))
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('tasks'))
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        confirm = request.form.get('confirm') or ''

        errors = []
        if len(name) < 2:
            errors.append('Please enter your name.')
        if '@' not in email or '.' not in email.split('@')[-1]:
            errors.append('Please enter a valid email address.')
        if len(password) < 6:
            errors.append('Password must be at least 6 characters.')
        if password != confirm:
            errors.append('Passwords do not match.')
        if User.query.filter_by(email=email).first():
            errors.append('An account with this email already exists.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('register.html', name=name, email=email)

        user = User(name=name, email=email, points=0, streak=0,
                    language_preference=get_lang())
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('An account with this email already exists.', 'error')
            return render_template('register.html', name=name, email=email)

        login_user(user, remember=True)
        return redirect(url_for('onboarding'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('tasks'))
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            if not user.onboarded:
                return redirect(url_for('onboarding'))
            return redirect(url_for('tasks'))
        flash('Incorrect email or password.', 'error')
        return render_template('login.html', email=email)
    return render_template('login.html')


@app.route('/login/google')
def login_google():
    if not google:
        flash('Google login is not configured. Use email or Demo Login instead.', 'warning')
        return redirect(url_for('login'))
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route('/auth/google/callback')
def google_callback():
    if not google:
        return redirect(url_for('login'))
    try:
        token = google.authorize_access_token()
        userinfo = token.get('userinfo') or google.userinfo()
        user = User.query.filter_by(google_id=userinfo['sub']).first()
        if not user:
            user = User.query.filter_by(email=userinfo['email']).first()
        if not user:
            user = User(
                google_id=userinfo['sub'],
                name=userinfo.get('name', userinfo['email'].split('@')[0]),
                email=userinfo['email'],
                avatar=userinfo.get('picture', ''),
                points=0, streak=0
            )
            db.session.add(user)
        else:
            user.google_id = userinfo['sub']
            user.name = userinfo.get('name', user.name)
            user.avatar = userinfo.get('picture', user.avatar)
        db.session.commit()
        login_user(user, remember=True)
        if not user.onboarded:
            return redirect(url_for('onboarding'))
        return redirect(url_for('tasks'))
    except Exception as e:
        print(f"OAuth error: {e}")
        flash('Login failed. Please try again.', 'error')
        return redirect(url_for('login'))


@app.route('/login/demo')
def login_demo():
    demo_email = 'demo@thrive365.bg'
    user = User.query.filter_by(email=demo_email).first()
    if not user:
        user = User(name='Иван Демов', email=demo_email, avatar='',
                    points=325, streak=3,
                    last_active_date=date.today() - timedelta(days=1),
                    language_preference='en')
        db.session.add(user)
    # ensure the demo account is fully onboarded with a sample profile
    if not user.onboarded:
        user.onboarded = True
        user.neighborhood = 'Лазур'
        user.transport = 'car'
        user.home_type = 'apartment'
        user.garden_access = 'balcony'
        user.household_size = 2
        user.interests = 'recycling,nature,cycling,community'
        user.time_commitment = 'medium'
        user.activity_level = 'medium'
        user.age_range = 'adult'
    db.session.commit()
    generate_daily_tasks(user)
    login_user(user, remember=True)
    return redirect(url_for('tasks'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/language', methods=['POST'])
def switch_language():
    lang = request.form.get('lang', 'en')
    if lang not in ['en', 'bg']:
        lang = 'en'
    if current_user.is_authenticated:
        current_user.language_preference = lang
        db.session.commit()
    else:
        session['lang'] = lang
    return redirect(request.form.get('next', url_for('index')))


# ─── Onboarding ─────────────────────────────────────────────────────────────────

@app.route('/onboarding', methods=['GET', 'POST'])
@login_required
def onboarding():
    if request.method == 'POST':
        def pick(field):
            val = (request.form.get(field) or '').strip()
            return val if val in _VALID.get(field, set()) else None

        current_user.neighborhood = (request.form.get('neighborhood') or '').strip() or None
        current_user.transport = pick('transport')
        current_user.home_type = pick('home_type')
        current_user.garden_access = pick('garden_access')
        current_user.time_commitment = pick('time_commitment')
        current_user.activity_level = pick('activity_level')
        current_user.age_range = pick('age_range')

        try:
            current_user.household_size = max(1, min(20, int(request.form.get('household_size', 1))))
        except (TypeError, ValueError):
            current_user.household_size = 1

        interests = [i for i in request.form.getlist('interests') if i in _VALID['interests']]
        current_user.interests = ','.join(interests)

        current_user.onboarded = True
        db.session.commit()

        # build the first set of personalised tasks immediately
        generate_daily_tasks(current_user)
        flash('Your profile is set. Here are your personalised eco-tasks.', 'success')
        return redirect(url_for('tasks'))

    return render_template('onboarding.html', neighborhoods=NEIGHBORHOODS,
                           options=ONBOARD_OPTIONS)


# ─── Main app routes ───────────────────────────────────────────────────────────

@app.route('/tasks')
@login_required
def tasks():
    today_tasks = generate_daily_tasks(current_user)
    return render_template('tasks.html', tasks=today_tasks)


@app.route('/tasks/complete/<int:task_id>', methods=['POST'])
@login_required
def complete_task(task_id):
    at = db.session.get(AssignedTask, task_id)
    if not at or at.user_id != current_user.id:
        return jsonify({'success': False, 'message': 'Task not found.'})
    if at.date != date.today():
        return jsonify({'success': False, 'message': 'This task is not available today.'})
    if at.verified:
        return jsonify({'success': False, 'message': 'You have already completed this task!'})

    if 'photo' not in request.files or request.files['photo'].filename == '':
        return jsonify({'success': False, 'message': 'Please upload a photo to verify your task.'})

    file = request.files['photo']
    if not allowed_file(file.filename):
        return jsonify({'success': False, 'message': 'Invalid file type. Please upload a JPG, PNG, or WebP image.'})

    filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    upload_folder = app.config['UPLOAD_FOLDER']
    os.makedirs(upload_folder, exist_ok=True)
    photo_path = os.path.join(upload_folder, filename)
    file.save(photo_path)

    try:
        verified, feedback = thriveai.verify_image(at, photo_path, lang=get_lang())
        at.ai_feedback = feedback

        if verified:
            at.verified = True
            at.photo_path = f"uploads/{filename}"
            at.completed_at = datetime.utcnow()
            current_user.points += at.points
            update_streak(current_user)
            new_badges = check_and_award_badges(current_user)
            db.session.commit()
            return jsonify({
                'success': True, 'verified': True,
                'points_awarded': at.points,
                'total_points': current_user.points,
                'streak': current_user.streak,
                'feedback': feedback,
                'new_badges': [{'name': b.name, 'icon': b.icon} for b in new_badges]
            })
        else:
            try:
                os.remove(photo_path)
            except OSError:
                pass
            db.session.commit()
            return jsonify({'success': False, 'verified': False, 'feedback': feedback})
    except Exception:  # noqa: BLE001 — always answer with JSON, never a 500 HTML page
        import traceback
        traceback.print_exc()
        db.session.rollback()
        return jsonify({
            'success': False, 'verified': False,
            'message': 'Verification failed on the server. Please try again.'
        }), 200


@app.route('/map')
@login_required
def map_page():
    return render_template('map.html')


@app.route('/api/map-data')
@login_required
def map_data():
    today = date.today()
    my_tasks = AssignedTask.query.filter_by(user_id=current_user.id, date=today).all()

    heatmap = []
    for c in AssignedTask.query.filter_by(verified=True).all():
        if c.lat is not None and c.lng is not None:
            heatmap.append([c.lat, c.lng, 0.6])

    rng = random.Random(99)
    activity_centers = [
        (42.4947, 27.4731), (42.5048, 27.4580), (42.5048, 27.4626),
        (42.5150, 27.4700), (42.5576, 27.5011)
    ]
    for lat, lng in activity_centers:
        for _ in range(12):
            heatmap.append([
                lat + rng.uniform(-0.012, 0.012),
                lng + rng.uniform(-0.012, 0.012),
                round(rng.uniform(0.2, 1.0), 2)
            ])

    lang = get_lang()
    task_data = [{
        'id': t.id,
        'title': t.title(lang),
        'lat': t.lat, 'lng': t.lng,
        'points': t.points,
        'location': t.location_name or 'Burgas',
        'done': t.verified,
    } for t in my_tasks if t.lat is not None and t.lng is not None]

    return jsonify({'tasks': task_data, 'heatmap': heatmap})


# ─── Routing & geocoding (interactive map) ──────────────────────────────────────

MAX_VIA_WAYPOINTS = 6
DAILY_ROUTE_POINTS_CAP = 200
POINTS_PER_KM = {'walk': 5, 'bike': 3}  # walking is harder, awarded more per km


def _parse_latlng(v):
    """Accept [lat,lng] or {'lat':..,'lng':..} from JSON; return (float,float) or None."""
    try:
        if isinstance(v, dict):
            return float(v['lat']), float(v['lng'])
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return float(v[0]), float(v[1])
    except (KeyError, TypeError, ValueError):
        return None
    return None


@app.route('/api/route', methods=['POST'])
@login_required
def api_route():
    """Plan a route. Accepts optional `via_tasks=true` to weave the request
    through the user's pending tasks-with-locations for today."""
    data = request.get_json(silent=True) or {}
    origin = _parse_latlng(data.get('from'))
    destination = _parse_latlng(data.get('to'))
    if not origin or not destination:
        return jsonify({'ok': False, 'error': 'Provide valid `from` and `to` as [lat, lng].'}), 400

    mode = (data.get('mode') or 'walk').lower()
    prefer = (data.get('prefer') or 'fast').lower()
    via_tasks = bool(data.get('via_tasks'))

    waypoints = []
    if via_tasks:
        pending = (AssignedTask.query
                   .filter_by(user_id=current_user.id, date=date.today(), verified=False)
                   .filter(AssignedTask.lat.isnot(None), AssignedTask.lng.isnot(None))
                   .order_by(AssignedTask.id).all())
        # Cap to keep ORS calls fast and within free-tier complexity limits.
        for t in pending[:MAX_VIA_WAYPOINTS]:
            waypoints.append((t.lat, t.lng))

    result = routing_service.route(origin, destination, mode=mode,
                                   prefer=prefer, waypoints=waypoints)
    if not result.get('ok'):
        return jsonify(result), 200  # frontend wants JSON, not a Flask error page

    # Flag any visible (non-hidden) hazards that fall close to the route geometry.
    visible_hazards = Hazard.query.filter_by(hidden=False).all()
    nearby = []
    for h in visible_hazards:
        d = routing_service.closest_in_geometry(result['geometry'], (h.lat, h.lng), threshold_m=60)
        if d is not None:
            nearby.append({'id': h.id, 'lat': h.lat, 'lng': h.lng, 'type': h.hazard_type,
                           'description': h.description, 'distance_m': round(d, 1)})
    result['hazards_near'] = nearby

    # Lightweight safety score: 100 minus penalties from hazards + (placeholder) lit %.
    score = 100 - min(len(nearby) * 8, 40)
    result['safety_score'] = score

    # Pass through the waypoint list so the UI can render labels on the map.
    result['waypoints'] = [{'lat': lat, 'lng': lng} for lat, lng in waypoints]

    return jsonify(result)


@app.route('/api/geocode')
@login_required
def api_geocode():
    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({'ok': False, 'error': 'Provide ?q='}), 400
    return jsonify(routing_service.geocode(q, limit=6))


@app.route('/api/reverse-geocode')
@login_required
def api_reverse_geocode():
    try:
        lat = float(request.args.get('lat')); lng = float(request.args.get('lng'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'lat & lng required'}), 400
    return jsonify(routing_service.reverse_geocode(lat, lng))


@app.route('/api/nearest')
@login_required
def api_nearest():
    """Find the closest pending task / nature spot / partner reward to a point."""
    try:
        lat = float(request.args.get('lat')); lng = float(request.args.get('lng'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'lat & lng required'}), 400
    here = (lat, lng)

    def nearest(rows, get_pos):
        best, best_d = None, None
        for r in rows:
            pos = get_pos(r)
            if pos is None: continue
            d = routing_service.haversine_m(here, pos)
            if best_d is None or d < best_d:
                best, best_d = r, d
        return best, best_d

    today = date.today()
    pending = (AssignedTask.query
               .filter_by(user_id=current_user.id, date=today, verified=False)
               .filter(AssignedTask.lat.isnot(None), AssignedTask.lng.isnot(None))
               .all())
    task, td = nearest(pending, lambda t: (t.lat, t.lng))

    # Burgas hand-curated nature spots (kept in sync with map.html overlays).
    nature_spots = [
        {'name_en': 'Primorski Park',     'name_bg': 'Приморски парк',     'lat': 42.4947, 'lng': 27.4731},
        {'name_en': 'Atanasovsko Ezero',  'name_bg': 'Атанасовско езеро',  'lat': 42.5576, 'lng': 27.5011},
        {'name_en': 'Ezerata Lakes',      'name_bg': 'Езерата',            'lat': 42.5150, 'lng': 27.4700},
        {'name_en': 'Sea Garden Beach',   'name_bg': 'Морска градина',     'lat': 42.4880, 'lng': 27.4790},
    ]
    spot, sd = nearest(nature_spots, lambda s: (s['lat'], s['lng']))

    lang = get_lang()
    payload = {'ok': True}
    if task:
        payload['task'] = {
            'id': task.id,
            'title': task.title(lang),
            'location': task.location_name or ('Burgas' if lang == 'en' else 'Бургас'),
            'points': task.points,
            'lat': task.lat, 'lng': task.lng,
            'distance_m': round(td, 1) if td is not None else None,
        }
    if spot:
        payload['nature'] = {
            'name': spot['name_en'] if lang == 'en' else spot['name_bg'],
            'lat': spot['lat'], 'lng': spot['lng'],
            'distance_m': round(sd, 1) if sd is not None else None,
        }
    return jsonify(payload)


# ─── Hazard reports ─────────────────────────────────────────────────────────────

ALLOWED_HAZARD_TYPES = {'lighting', 'sidewalk', 'traffic', 'flooding', 'other'}


@app.route('/api/hazards')
@login_required
def api_hazards():
    """Return all visible hazards. Optional bbox: ?minLat=..&minLng=..&maxLat=..&maxLng=.."""
    q = Hazard.query.filter_by(hidden=False)
    try:
        if all(k in request.args for k in ('minLat', 'minLng', 'maxLat', 'maxLng')):
            mnLa = float(request.args['minLat']); mxLa = float(request.args['maxLat'])
            mnLn = float(request.args['minLng']); mxLn = float(request.args['maxLng'])
            q = q.filter(Hazard.lat.between(mnLa, mxLa),
                         Hazard.lng.between(mnLn, mxLn))
    except (TypeError, ValueError):
        pass
    return jsonify({'ok': True, 'hazards': [
        {'id': h.id, 'lat': h.lat, 'lng': h.lng, 'type': h.hazard_type,
         'description': h.description,
         'photo': (url_for('static', filename=h.photo_path) if h.photo_path else None),
         'created_at': h.created_at.isoformat() if h.created_at else None}
        for h in q.order_by(Hazard.created_at.desc()).limit(500).all()
    ]})


@app.route('/api/hazard/report', methods=['POST'])
@login_required
def api_hazard_report():
    """Submit a new hazard. Auto-published; admin can hide later."""
    # Accept JSON or multipart (when a photo is attached).
    if request.content_type and request.content_type.startswith('multipart/'):
        form = request.form
        photo = request.files.get('photo')
    else:
        form = request.get_json(silent=True) or {}
        photo = None

    try:
        lat = float(form.get('lat')); lng = float(form.get('lng'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'lat & lng required'}), 400

    hazard_type = (form.get('type') or 'other').strip().lower()
    if hazard_type not in ALLOWED_HAZARD_TYPES:
        hazard_type = 'other'

    description = (form.get('description') or '').strip()[:500] or None

    photo_path = None
    if photo and photo.filename and allowed_file(photo.filename):
        filename = f"hazard_{uuid.uuid4().hex}_{secure_filename(photo.filename)}"
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        photo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        photo_path = f"uploads/{filename}"

    h = Hazard(user_id=current_user.id, lat=lat, lng=lng,
               hazard_type=hazard_type, description=description,
               photo_path=photo_path)
    db.session.add(h)
    db.session.commit()
    return jsonify({'ok': True, 'id': h.id})


@app.route('/admin/hazard/<int:hazard_id>/hide', methods=['POST'])
def admin_hide_hazard(hazard_id):
    # `admin_required` is defined later in the file; inline the same check here
    # so we don't depend on forward-declared decorators.
    if not session.get('admin_authenticated'):
        return redirect(url_for('admin'))
    h = db.session.get(Hazard, hazard_id)
    if h:
        h.hidden = True
        db.session.commit()
        flash('Hazard hidden.', 'success')
    return redirect(url_for('admin'))


# ─── Green route completion (points for walking/cycling) ────────────────────────

@app.route('/api/route/complete', methods=['POST'])
@login_required
def api_route_complete():
    """Award eco-points for a completed walking/cycling route. Caps per-day total
    from routing at DAILY_ROUTE_POINTS_CAP to prevent farming."""
    form = request.form if (request.content_type or '').startswith('multipart/') \
           else (request.get_json(silent=True) or {})
    try:
        distance_m = float(form.get('distance_m', 0))
    except (TypeError, ValueError):
        distance_m = 0.0
    mode = (form.get('mode') or 'walk').lower()
    if mode not in POINTS_PER_KM or distance_m < 200:
        return jsonify({'ok': False, 'error': 'Route too short or invalid mode.'}), 400

    try:
        duration_s = float(form.get('duration_s', 0)) or None
    except (TypeError, ValueError):
        duration_s = None

    # Cap remaining points for today from this user's prior route completions.
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_total = db.session.query(db.func.coalesce(db.func.sum(RouteCompletion.points_awarded), 0)) \
                            .filter(RouteCompletion.user_id == current_user.id,
                                    RouteCompletion.created_at >= today_start).scalar() or 0
    remaining = max(0, DAILY_ROUTE_POINTS_CAP - int(today_total))
    if remaining <= 0:
        return jsonify({'ok': False, 'error': 'Daily route reward cap reached. Try again tomorrow.'}), 200

    earned = min(remaining, int(round(distance_m / 1000.0 * POINTS_PER_KM[mode])))
    if earned <= 0:
        return jsonify({'ok': False, 'error': 'Route too short to earn points.'}), 200

    photo_path = None
    photo = request.files.get('photo') if request.files else None
    if photo and photo.filename and allowed_file(photo.filename):
        filename = f"route_{uuid.uuid4().hex}_{secure_filename(photo.filename)}"
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        photo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        photo_path = f"uploads/{filename}"

    rc = RouteCompletion(user_id=current_user.id, mode=mode,
                         distance_m=distance_m, duration_s=duration_s,
                         points_awarded=earned, photo_path=photo_path)
    db.session.add(rc)
    current_user.points += earned
    update_streak(current_user)
    new_badges = check_and_award_badges(current_user)
    db.session.commit()

    return jsonify({'ok': True, 'points_awarded': earned,
                    'total_points': current_user.points,
                    'remaining_today': max(0, DAILY_ROUTE_POINTS_CAP - int(today_total) - earned),
                    'new_badges': [{'name': b.name, 'icon': b.icon} for b in new_badges]})


@app.route('/leaderboard')
@login_required
def leaderboard():
    top_users = User.query.order_by(User.points.desc()).limit(20).all()
    user_rank = User.query.filter(User.points > current_user.points).count() + 1
    return render_template('leaderboard.html', users=top_users, user_rank=user_rank)


@app.route('/profile')
@login_required
def profile():
    all_badges = Badge.query.all()
    earned_ids = {ub.badge_id for ub in UserBadge.query.filter_by(user_id=current_user.id).all()}
    completions = (AssignedTask.query
                   .filter_by(user_id=current_user.id, verified=True)
                   .order_by(AssignedTask.completed_at.desc())
                   .limit(10).all())
    user_rank = User.query.filter(User.points > current_user.points).count() + 1
    tasks_count = AssignedTask.query.filter_by(user_id=current_user.id, verified=True).count()
    return render_template('profile.html', all_badges=all_badges, earned_ids=earned_ids,
                           completions=completions, user_rank=user_rank, tasks_count=tasks_count)


@app.route('/shop')
@login_required
def shop():
    prizes = Prize.query.all()
    redeemed_ids = {r.prize_id for r in Redemption.query.filter_by(user_id=current_user.id).all()}
    return render_template('shop.html', prizes=prizes, redeemed_ids=redeemed_ids)


@app.route('/shop/redeem/<int:prize_id>', methods=['POST'])
@login_required
def redeem_prize(prize_id):
    prize = db.session.get(Prize, prize_id)
    if not prize:
        return jsonify({'success': False, 'message': 'Prize not found.'})
    if current_user.points < prize.points_cost:
        diff = prize.points_cost - current_user.points
        return jsonify({'success': False, 'message': f'You need {diff} more points to redeem this prize.'})
    if prize.stock <= 0:
        return jsonify({'success': False, 'message': 'Sorry, this prize is out of stock.'})

    code = f"T365-{uuid.uuid4().hex[:8].upper()}"
    db.session.add(Redemption(user_id=current_user.id, prize_id=prize_id, code=code))
    current_user.points -= prize.points_cost
    prize.stock -= 1
    db.session.commit()

    lang = get_lang()
    return jsonify({
        'success': True, 'code': code,
        'prize_title': prize.title_en if lang == 'en' else prize.title_bg,
        'remaining_points': current_user.points
    })


# ─── ThriveAI assistant routes ─────────────────────────────────────────────────

def build_thriveai_context():
    user = current_user
    today = date.today()
    lang = get_lang()

    today_tasks = [{
        'title': at.title(lang),
        'points': at.points,
        'location': at.location_name or 'Burgas',
        'done': at.verified,
    } for at in AssignedTask.query.filter_by(user_id=user.id, date=today).order_by(AssignedTask.id).all()]

    earned_ids = {ub.badge_id for ub in UserBadge.query.filter_by(user_id=user.id).all()}
    earned_badges = [b.name for b in Badge.query.all() if b.id in earned_ids]

    next_prize = (Prize.query
                  .filter(Prize.points_cost <= user.points, Prize.stock > 0)
                  .order_by(Prize.points_cost.desc()).first())
    if not next_prize:
        next_prize = Prize.query.filter(Prize.stock > 0).order_by(Prize.points_cost).first()

    rank = User.query.filter(User.points > user.points).count() + 1

    return {
        'name': user.name,
        'points': user.points,
        'streak': user.streak,
        'rank': rank,
        'today_tasks': today_tasks,
        'earned_badges': earned_badges,
        'next_prize': ({'title': next_prize.title_en if lang == 'en' else next_prize.title_bg,
                        'cost': next_prize.points_cost} if next_prize else None),
    }


@app.route('/thriveai')
@login_required
def thriveai_page():
    return render_template('thriveai.html', engine=thriveai.active_engine())


@app.route('/api/thriveai/chat', methods=['POST'])
@login_required
def thriveai_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'success': False, 'message': 'Empty message.'}), 400
    if len(message) > 2000:
        message = message[:2000]

    history = data.get('history') or []
    clean_history = [
        {'role': h.get('role'), 'content': str(h.get('content', ''))}
        for h in history
        if isinstance(h, dict) and h.get('role') in ('user', 'assistant')
    ]

    result = thriveai.chat(message, history=clean_history, lang=get_lang(),
                           context=build_thriveai_context())
    return jsonify({'success': True, 'reply': result['reply'], 'engine': result['engine']})


# ─── Admin routes (manage the curated task library) ─────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_authenticated'):
            flash('Please log in to access the admin panel.', 'warning')
            return redirect(url_for('admin'))
        return f(*args, **kwargs)
    return decorated


@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_authenticated'] = True
            return redirect(url_for('admin'))
        flash('Incorrect password.', 'error')

    if not session.get('admin_authenticated'):
        return render_template('admin.html', authenticated=False)

    users = User.query.order_by(User.points.desc()).all()
    templates = TaskTemplate.query.order_by(TaskTemplate.category, TaskTemplate.id).all()
    completions = (AssignedTask.query.filter_by(verified=True)
                   .order_by(AssignedTask.completed_at.desc()).limit(50).all())
    hazards = (Hazard.query.filter_by(hidden=False)
               .order_by(Hazard.created_at.desc()).limit(100).all())
    return render_template('admin.html', authenticated=True, users=users,
                           templates=templates, completions=completions, hazards=hazards)


@app.route('/admin/template/add', methods=['POST'])
@admin_required
def admin_add_template():
    try:
        db.session.add(TaskTemplate(
            title_en=request.form['title_en'],
            title_bg=request.form['title_bg'],
            description_en=request.form['description_en'],
            description_bg=request.form['description_bg'],
            points=int(request.form['points']),
            category=request.form.get('category', 'nature'),
            effort=request.form.get('effort', 'medium'),
            tags=request.form.get('tags', '').strip(),
            neighborhood=(request.form.get('neighborhood') or '').strip() or None,
            location_name=(request.form.get('location_name') or '').strip() or None,
            lat=float(request.form['lat']) if request.form.get('lat') else None,
            lng=float(request.form['lng']) if request.form.get('lng') else None,
            active=True,
        ))
        db.session.commit()
        flash('Task template added!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'error')
    return redirect(url_for('admin'))


@app.route('/admin/template/<int:template_id>/delete', methods=['POST'])
@admin_required
def admin_delete_template(template_id):
    t = db.session.get(TaskTemplate, template_id)
    if t:
        db.session.delete(t)
        db.session.commit()
        flash('Template deleted.', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/template/<int:template_id>/toggle', methods=['POST'])
@admin_required
def admin_toggle_template(template_id):
    t = db.session.get(TaskTemplate, template_id)
    if t:
        t.active = not t.active
        db.session.commit()
    return redirect(url_for('admin'))


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_authenticated', None)
    return redirect(url_for('admin'))


# ─── Bootstrap ─────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()
    seed_db()
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
