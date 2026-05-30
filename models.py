from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)

    # ── Identity / auth ──
    google_id = db.Column(db.String(100), unique=True, nullable=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)  # null for Google-only users
    avatar = db.Column(db.String(500), nullable=True)

    # ── Gamification ──
    points = db.Column(db.Integer, default=0)
    streak = db.Column(db.Integer, default=0)
    last_active_date = db.Column(db.Date, nullable=True)
    language_preference = db.Column(db.String(2), default='en')
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ── Onboarding profile (drives personalised task selection) ──
    onboarded = db.Column(db.Boolean, default=False)
    neighborhood = db.Column(db.String(80), nullable=True)      # Burgas quarter
    transport = db.Column(db.String(20), nullable=True)         # car / bike / walk / transit / mixed
    home_type = db.Column(db.String(20), nullable=True)         # apartment / house
    garden_access = db.Column(db.String(20), nullable=True)     # none / balcony / yard / garden
    household_size = db.Column(db.Integer, nullable=True)
    interests = db.Column(db.String(255), nullable=True)        # csv: recycling,nature,cycling,energy,water,community,food,waste
    time_commitment = db.Column(db.String(20), nullable=True)   # low / medium / high
    activity_level = db.Column(db.String(20), nullable=True)    # low / medium / high
    age_range = db.Column(db.String(20), nullable=True)         # teen / adult / senior

    # ── Relationships ──
    assigned_tasks = db.relationship('AssignedTask', backref='user', lazy=True,
                                     cascade='all, delete-orphan')
    redemptions = db.relationship('Redemption', backref='user', lazy=True,
                                  cascade='all, delete-orphan')
    user_badges = db.relationship('UserBadge', backref='user', lazy=True,
                                  cascade='all, delete-orphan')

    # ── Password helpers ──
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def interest_list(self):
        return [i for i in (self.interests or '').split(',') if i]


class TaskTemplate(db.Model):
    """The curated, reusable library of legitimate Burgas eco-tasks.

    Templates are undated and tagged so the engine can pick the right ones for
    each user's profile. They are NOT shown directly — they are instantiated
    into per-user AssignedTask rows."""
    __tablename__ = 'task_templates'
    id = db.Column(db.Integer, primary_key=True)
    title_en = db.Column(db.String(200), nullable=False)
    title_bg = db.Column(db.String(200), nullable=False)
    description_en = db.Column(db.Text, nullable=False)
    description_bg = db.Column(db.Text, nullable=False)
    points = db.Column(db.Integer, nullable=False, default=50)

    category = db.Column(db.String(30), nullable=False, default='nature')
    effort = db.Column(db.String(10), nullable=False, default='medium')   # low / medium / high
    tags = db.Column(db.String(255), nullable=True)        # csv requirement/context tags
    neighborhood = db.Column(db.String(80), nullable=True)  # null = anywhere in Burgas

    location_name = db.Column(db.String(200), nullable=True)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    active = db.Column(db.Boolean, default=True)

    @property
    def tag_list(self):
        return [t for t in (self.tags or '').split(',') if t]


class AssignedTask(db.Model):
    """A specific task given to a specific user for a specific day. Carries a
    snapshot of the template text (optionally AI-tailored) plus completion state."""
    __tablename__ = 'assigned_tasks'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    template_id = db.Column(db.Integer, db.ForeignKey('task_templates.id', ondelete='SET NULL'),
                            nullable=True)
    date = db.Column(db.Date, nullable=False)

    # snapshot (may be AI-personalised) so historic tasks stay stable
    title_en = db.Column(db.String(200), nullable=False)
    title_bg = db.Column(db.String(200), nullable=False)
    description_en = db.Column(db.Text, nullable=False)
    description_bg = db.Column(db.Text, nullable=False)
    points = db.Column(db.Integer, nullable=False, default=50)
    category = db.Column(db.String(30), nullable=True)
    location_name = db.Column(db.String(200), nullable=True)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)

    # completion state
    verified = db.Column(db.Boolean, default=False)
    photo_path = db.Column(db.String(500), nullable=True)
    ai_feedback = db.Column(db.Text, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    template = db.relationship('TaskTemplate', lazy=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'template_id', 'date', name='uq_user_template_date'),
    )

    def title(self, lang):
        return self.title_en if lang == 'en' else self.title_bg

    def description(self, lang):
        return self.description_en if lang == 'en' else self.description_bg


class Prize(db.Model):
    __tablename__ = 'prizes'
    id = db.Column(db.Integer, primary_key=True)
    title_en = db.Column(db.String(200), nullable=False)
    title_bg = db.Column(db.String(200), nullable=False)
    description_en = db.Column(db.Text, nullable=False)
    description_bg = db.Column(db.Text, nullable=False)
    points_cost = db.Column(db.Integer, nullable=False)
    stock = db.Column(db.Integer, default=100)
    image = db.Column(db.String(10), nullable=True)

    redemptions = db.relationship('Redemption', backref='prize', lazy=True)


class Redemption(db.Model):
    __tablename__ = 'redemptions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    prize_id = db.Column(db.Integer, db.ForeignKey('prizes.id'), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)
    redeemed_at = db.Column(db.DateTime, default=datetime.utcnow)


class Badge(db.Model):
    __tablename__ = 'badges'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    icon = db.Column(db.String(10), nullable=False)
    description_en = db.Column(db.String(300), nullable=False)
    description_bg = db.Column(db.String(300), nullable=False)
    condition_type = db.Column(db.String(50), nullable=False)
    condition_value = db.Column(db.Integer, nullable=False)

    user_badges = db.relationship('UserBadge', backref='badge', lazy=True)


class UserBadge(db.Model):
    __tablename__ = 'user_badges'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    badge_id = db.Column(db.Integer, db.ForeignKey('badges.id'), nullable=False)
    earned_at = db.Column(db.DateTime, default=datetime.utcnow)
