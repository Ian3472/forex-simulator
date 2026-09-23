import os
import random
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import requests

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
elif db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login_page"

PAIRS = {
    "EUR/USD": 1.08500,
    "GBP/USD": 1.26500,
    "USD/JPY": 149.500,
    "AUD/USD": 0.65500,
    "USD/KES": 129.000,
    "EUR/GBP": 0.85800,
}
VOLATILITY = {
    "EUR/USD": 0.00020, "GBP/USD": 0.00030, "USD/JPY": 0.00025,
    "AUD/USD": 0.00030, "USD/KES": 0.00008, "EUR/GBP": 0.00018,
}
live_prices = dict(PAIRS)
price_lock = threading.Lock()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=10000.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Position(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    pair = db.Column(db.String(20), nullable=False)
    direction = db.Column(db.String(4), nullable=False)
    lots = db.Column(db.Float, nullable=False)
    entry = db.Column(db.Float, nullable=False)
    exit = db.Column(db.Float, nullable=True)
    pnl = db.Column(db.Float, nullable=True)
    opened_at = db.Column(db.DateTime, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime, nullable=True)
    is_open = db.Column(db.Boolean, default=True, nullable=False)


@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


with app.app_context():
    db.create_all()


def fetch_real_rates():
    try:
        r = requests.get(
            "https://api.frankfurter.app/latest?from=USD&to=EUR,GBP,JPY,AUD,KES",
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json().get("rates", {})
            with price_lock:
                if "EUR" in data: live_prices["EUR/USD"] = round(1 / data["EUR"], 5)
                if "GBP" in data: live_prices["GBP/USD"] = round(1 / data["GBP"], 5)
                if "JPY" in data: live_prices["USD/JPY"] = round(data["JPY"], 3)
                if "AUD" in data: live_prices["AUD/USD"] = round(1 / data["AUD"], 5)
                if "KES" in data: live_prices["USD/KES"] = round(data["KES"], 3)
            print("[prices] real rates loaded")
    except Exception as e:
        print("[prices] fetch failed: " + str(e))


def price_engine():
    fetch_real_rates()
    last_fetch = time.time()
    while True:
        time.sleep(1)
        with price_lock:
            for pair, price in list(live_prices.items()):
                vol = VOLATILITY.get(pair, 0.0002)
                change = random.uniform(-vol, vol)
                new_price = price * (1 + change)
                live_prices[pair] = round(new_price, 3 if ("JPY" in pair or "KES" in pair) else 5)
        if time.time() - last_fetch > 3600:
            fetch_real_rates()
            last_fetch = time.time()


threading.Thread(target=price_engine, daemon=True).start()


def calc_pnl(direction, entry, current, lots):
    if direction == "BUY":
        return (current - entry) * 100000 * lots
    return (entry - current) * 100000 * lots


@app.route("/register", methods=["GET", "POST"])
def register_page():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not username or not email or len(password) < 6:
            flash("All fields required, password at least 6 characters", "error")
        elif User.query.filter_by(username=username).first():
            flash("Username already taken", "error")
        elif User.query.filter_by(email=email).first():
            flash("Email already registered", "error")
        else:
            u = User(username=username, email=email)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            login_user(u)
            return redirect(url_for("dashboard"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        u = User.query.filter_by(username=username).first()
        if u and u.check_password(password):
            login_user(u)
            return redirect(url_for("dashboard"))
        flash("Invalid username or password", "error")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login_page"))


@app.route("/")
@login_required
def dashboard():
    return render_template("index.html")


@app.route("/api/state")
@login_required
def get_state():
    with price_lock:
        prices = dict(live_prices)

    positions = Position.query.filter_by(user_id=current_user.id, is_open=True).all()
    pos_data = []
    total_pnl = 0.0
    for p in positions:
        cur = prices.get(p.pair, p.entry)
        pnl = calc_pnl(p.direction, p.entry, cur, p.lots)
        total_pnl += pnl
        pos_data.append({
            "id": p.id, "pair": p.pair, "direction": p.direction,
            "lots": p.lots, "entry": p.entry, "current": cur,
            "pnl": round(pnl, 2),
            "opened": p.opened_at.strftime("%H:%M:%S"),
        })

    history = Position.query.filter_by(user_id=current_user.id, is_open=False)\
        .order_by(Position.closed_at.desc()).limit(20).all()
    hist_data = []
    for h in history:
        hist_data.append({
            "pair": h.pair, "direction": h.direction, "lots": h.lots,
            "entry": h.entry, "exit": h.exit, "pnl": h.pnl,
            "closed": h.closed_at.strftime("%H:%M:%S") if h.closed_at else "",
        })

    return jsonify({
        "username": current_user.username,
        "prices": prices,
        "balance": round(current_user.balance, 2),
        "equity": round(current_user.balance + total_pnl, 2),
        "floating_pnl": round(total_pnl, 2),
        "positions": pos_data,
        "history": hist_data,
    })


@app.route("/api/trade", methods=["POST"])
@login_required
def trade():
    data = request.get_json() or {}
    pair = data.get("pair")
    direction = (data.get("direction") or "").upper()
    try:
        lots = float(data.get("lots", 0.1))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid lots"}), 400

    with price_lock:
        prices = dict(live_prices)

    if pair not in prices:
        return jsonify({"error": "Unknown pair"}), 400
    if direction not in ("BUY", "SELL"):
        return jsonify({"error": "Direction must be BUY or SELL"}), 400
    if lots <= 0 or lots > 10:
        return jsonify({"error": "Lots must be 0.01 - 10"}), 400

    pos = Position(
        user_id=current_user.id, pair=pair, direction=direction,
        lots=lots, entry=prices[pair],
    )
    db.session.add(pos)
    db.session.commit()
    return jsonify({"ok": True, "position": {"id": pos.id, "entry": pos.entry}})


@app.route("/api/close/<int:pos_id>", methods=["POST"])
@login_required
def close(pos_id):
    pos = Position.query.filter_by(id=pos_id, user_id=current_user.id, is_open=True).first()
    if not pos:
        return jsonify({"error": "Position not found"}), 404

    with price_lock:
        prices = dict(live_prices)

    cur = prices.get(pos.pair, pos.entry)
    pnl = calc_pnl(pos.direction, pos.entry, cur, pos.lots)

    pos.exit = cur
    pos.pnl = round(pnl, 2)
    pos.closed_at = datetime.utcnow()
    pos.is_open = False

    u = db.session.get(User, current_user.id)
    u.balance += pnl
    db.session.commit()
    return jsonify({"ok": True, "pnl": round(pnl, 2)})


@app.route("/api/reset", methods=["POST"])
@login_required
def reset():
    u = db.session.get(User, current_user.id)
    u.balance = 10000.0
    Position.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
