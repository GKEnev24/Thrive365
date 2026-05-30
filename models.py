from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(100), unique=True, nullable=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    avatar = db.Column(db.String(500), nullable=True)
    points = db.Column(db.Integer, default=0)
    streak = db.Column(db.Integer, default=0)
    last_active_date = db.Column(db.Date, nullable=True)
    language_preference = db.Column(db.String(2), default='en')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    completions = db.relationship('TaskCompletion', backref='user', lazy=True, cascade='all, delete-orphan')
    redemptions = db.relationship('Redemption', backref='user', lazy=True, cascade='all, delete-orphan')
    user_badges = db.relationship('UserBadge', backref='user', lazy=True, cascade='all, delete-orphan')


class Task(db.Model):
    __tablename__ = 'tasks'
    id = db.Column(db.Integer, primary_key=True)
    title_en = db.Column(db.String(200), nullable=False)
    title_bg = db.Column(db.String(200), nullable=False)
    description_en = db.Column(db.Text, nullable=False)
    description_bg = db.Column(db.Text, nullable=False)
    points = db.Column(db.Integer, nullable=False, default=50)
    location_name = db.Column(db.String(200), nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, nullable=False)

    completions = db.relationship('TaskCompletion', backref='task', lazy=True, cascade='all, delete-orphan')


class TaskCompletion(db.Model):
    __tablename__ = 'task_completions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    photo_path = db.Column(db.String(500), nullable=True)
    verified = db.Column(db.Boolean, default=False)
    ai_feedback = db.Column(db.Text, nullable=True)
    completed_at = db.Column(db.DateTime, default=datetime.utcnow)


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
