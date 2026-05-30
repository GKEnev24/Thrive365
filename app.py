import os
import uuid
from datetime import datetime, date, timedelta
from functools import wraps

from dotenv import load_dotenv
load_dotenv()  # load .env (GEMINI_API_KEY, etc.) before anything reads os.environ

from flask import (Flask, render_template, redirect, url_for, session,
                   request, jsonify, flash)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from werkzeug.utils import secure_filename

from models import db, User, Task, TaskCompletion, Prize, Redemption, Badge, UserBadge
from database import seed_db
import thriveai

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'thrive365-dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///thrive365.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'index'

oauth = OAuth(app)

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

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


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


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
    """Cross-platform date/datetime formatting for templates.

    glibc no-padding tokens (%-d, %-m, %-H, ...) are NOT supported on Windows
    and raise `ValueError: Invalid format string`. We substitute them manually
    so the same templates render on every OS. Only touches the time attributes
    actually requested, so it works for both `date` and `datetime` values.
    """
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
    completed_count = TaskCompletion.query.filter_by(user_id=user.id, verified=True).count()

    for badge in Badge.query.all():
        if badge.id in earned_ids:
            continue
        earned = False
        if badge.condition_type == 'points' and user.points >= badge.condition_value:
            earned = True
        elif badge.condition_type == 'streak' and user.streak >= badge.condition_value:
            earned = True
        elif badge.condition_type == 'tasks' and completed_count >= badge.condition_value:
            earned = True

        if earned:
            db.session.add(UserBadge(user_id=user.id, badge_id=badge.id))
            new_badges.append(badge)

    return new_badges


def verify_task_with_ai(task, photo_path):
    """Verify a task photo via ThriveAI (Gemini → local Ollama → heuristic)."""
    return thriveai.verify_image(task, photo_path, lang=get_lang())


# ─── Auth routes ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('tasks'))
    return render_template('index.html')


@app.route('/login/google')
def login_google():
    if not google:
        flash('Google login is not configured. Use Demo Login instead.', 'warning')
        return redirect(url_for('index'))
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route('/auth/google/callback')
def google_callback():
    if not google:
        return redirect(url_for('index'))
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
        return redirect(url_for('tasks'))
    except Exception as e:
        print(f"OAuth error: {e}")
        flash('Login failed. Please try again.', 'error')
        return redirect(url_for('index'))


@app.route('/login/demo')
def login_demo():
    demo_email = 'demo@thrive365.bg'
    user = User.query.filter_by(email=demo_email).first()
    if not user:
        user = User(
            name='Иван Демов',
            email=demo_email,
            avatar='',
            points=325,
            streak=3,
            last_active_date=date.today() - timedelta(days=1),
            language_preference='en'
        )
        db.session.add(user)
        db.session.flush()
        for badge_id in [1, 2]:
            db.session.add(UserBadge(user_id=user.id, badge_id=badge_id))
        db.session.commit()
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


# ─── Main app routes ───────────────────────────────────────────────────────────

@app.route('/tasks')
@login_required
def tasks():
    today = date.today()
    today_tasks = Task.query.filter_by(date=today).order_by(Task.id).all()
    completed_ids = {
        tc.task_id for tc in TaskCompletion.query
        .filter_by(user_id=current_user.id, verified=True).all()
        if tc.task and tc.task.date == today
    }
    return render_template('tasks.html', tasks=today_tasks, completed_ids=completed_ids)


@app.route('/tasks/complete/<int:task_id>', methods=['POST'])
@login_required
def complete_task(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({'success': False, 'message': 'Task not found.'})
    if task.date != date.today():
        return jsonify({'success': False, 'message': 'This task is not available today.'})

    already_done = TaskCompletion.query.filter_by(
        user_id=current_user.id, task_id=task_id, verified=True
    ).first()
    if already_done:
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

    verified, feedback = verify_task_with_ai(task, photo_path)

    completion = TaskCompletion(
        user_id=current_user.id,
        task_id=task_id,
        photo_path=f"uploads/{filename}",
        verified=verified,
        ai_feedback=feedback
    )
    db.session.add(completion)

    if verified:
        current_user.points += task.points
        update_streak(current_user)
        new_badges = check_and_award_badges(current_user)
        db.session.commit()
        return jsonify({
            'success': True,
            'verified': True,
            'points_awarded': task.points,
            'total_points': current_user.points,
            'streak': current_user.streak,
            'feedback': feedback,
            'new_badges': [{'name': b.name, 'icon': b.icon} for b in new_badges]
        })
    else:
        try:
            os.remove(photo_path)
            completion.photo_path = None
        except OSError:
            pass
        db.session.commit()
        return jsonify({'success': False, 'verified': False, 'feedback': feedback})


@app.route('/map')
@login_required
def map_page():
    tasks = Task.query.filter_by(date=date.today()).all()
    return render_template('map.html', tasks=tasks)


@app.route('/api/map-data')
@login_required
def map_data():
    import random
    today = date.today()
    tasks = Task.query.filter_by(date=today).all()
    completions = TaskCompletion.query.filter_by(verified=True).join(Task).all()

    heatmap = []
    for c in completions:
        if c.task:
            heatmap.append([c.task.lat, c.task.lng, 0.6])

    random.seed(99)
    activity_centers = [
        (42.4947, 27.4731), (42.5048, 27.4580), (42.5048, 27.4626),
        (42.5150, 27.4700), (42.5576, 27.5011)
    ]
    for lat, lng in activity_centers:
        for _ in range(12):
            heatmap.append([
                lat + random.uniform(-0.012, 0.012),
                lng + random.uniform(-0.012, 0.012),
                round(random.uniform(0.2, 1.0), 2)
            ])

    lang = get_lang()
    task_data = [{
        'id': t.id,
        'title': t.title_en if lang == 'en' else t.title_bg,
        'lat': t.lat,
        'lng': t.lng,
        'points': t.points,
        'location': t.location_name
    } for t in tasks]

    return jsonify({'tasks': task_data, 'heatmap': heatmap})


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
    completions = (TaskCompletion.query
                   .filter_by(user_id=current_user.id, verified=True)
                   .order_by(TaskCompletion.completed_at.desc())
                   .limit(10).all())
    user_rank = User.query.filter(User.points > current_user.points).count() + 1
    tasks_count = TaskCompletion.query.filter_by(user_id=current_user.id, verified=True).count()
    return render_template('profile.html',
                           all_badges=all_badges,
                           earned_ids=earned_ids,
                           completions=completions,
                           user_rank=user_rank,
                           tasks_count=tasks_count)


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
        'success': True,
        'code': code,
        'prize_title': prize.title_en if lang == 'en' else prize.title_bg,
        'remaining_points': current_user.points
    })


# ─── ThriveAI assistant routes ─────────────────────────────────────────────────

def build_thriveai_context():
    """Snapshot of the current user's live state, fed to ThriveAI for grounded answers."""
    user = current_user
    today = date.today()
    lang = get_lang()

    completed_ids = {
        tc.task_id for tc in TaskCompletion.query
        .filter_by(user_id=user.id, verified=True).all()
        if tc.task and tc.task.date == today
    }
    today_tasks = []
    for t in Task.query.filter_by(date=today).order_by(Task.id).all():
        today_tasks.append({
            'title': t.title_en if lang == 'en' else t.title_bg,
            'points': t.points,
            'location': t.location_name,
            'done': t.id in completed_ids,
        })

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

    result = thriveai.chat(
        message,
        history=clean_history,
        lang=get_lang(),
        context=build_thriveai_context(),
    )
    return jsonify({'success': True, 'reply': result['reply'], 'engine': result['engine']})


# ─── Admin routes ──────────────────────────────────────────────────────────────

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
    tasks = Task.query.order_by(Task.date.desc(), Task.id).all()
    completions = (TaskCompletion.query
                   .order_by(TaskCompletion.completed_at.desc())
                   .limit(50).all())
    return render_template('admin.html', authenticated=True,
                           users=users, tasks=tasks, completions=completions)


@app.route('/admin/task/add', methods=['POST'])
@admin_required
def admin_add_task():
    try:
        db.session.add(Task(
            title_en=request.form['title_en'],
            title_bg=request.form['title_bg'],
            description_en=request.form['description_en'],
            description_bg=request.form['description_bg'],
            points=int(request.form['points']),
            location_name=request.form['location_name'],
            lat=float(request.form['lat']),
            lng=float(request.form['lng']),
            date=datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        ))
        db.session.commit()
        flash('Task added successfully!', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(url_for('admin'))


@app.route('/admin/task/<int:task_id>/edit', methods=['POST'])
@admin_required
def admin_edit_task(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        flash('Task not found.', 'error')
        return redirect(url_for('admin'))
    try:
        task.title_en = request.form['title_en']
        task.title_bg = request.form['title_bg']
        task.description_en = request.form['description_en']
        task.description_bg = request.form['description_bg']
        task.points = int(request.form['points'])
        task.location_name = request.form['location_name']
        task.lat = float(request.form['lat'])
        task.lng = float(request.form['lng'])
        task.date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        db.session.commit()
        flash('Task updated!', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    return redirect(url_for('admin'))


@app.route('/admin/task/<int:task_id>/delete', methods=['POST'])
@admin_required
def admin_delete_task(task_id):
    task = db.session.get(Task, task_id)
    if task:
        db.session.delete(task)
        db.session.commit()
        flash('Task deleted.', 'success')
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
