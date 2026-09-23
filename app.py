import os
import random
import threading
import time
from datetime import datetime
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

DEFAULT_PRICES = {
    "EUR/USD": 1.08500,
    "GBP/USD": 1.26500,
    "USD/JPY": 149.500,
    "AUD/USD": 0.65500,
    "USD/KES": 129.000,
    "EUR/GBP": 0.85800,
}

VOLATILITY = {
    "EUR/USD": 0.00020,
    "GBP/USD": 0.00030,
    "USD/JPY": 0.00025,
    "AUD/USD": 0.00030,
    "USD/KES": 0.00008,
    "EUR/GBP": 0.00018,
}

state = {
    "prices": dict(DEFAULT_PRICES),
    "balance": 10000.0,
    "positions": [],
    "history": [],
    "next_id": 1,
}


def try_fetch_rates():
    try:
        import requests
        r = requests.get(
            "https://api.frankfurter.app/latest?from=USD&to=EUR,GBP,JPY,AUD,KES",
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json().get("rates", {})
            if "EUR" in data: state["prices"]["EUR/USD"] = round(1 / data["EUR"], 5)
            if "GBP" in data: state["prices"]["GBP/USD"] = round(1 / data["GBP"], 5)
            if "JPY" in data: state["prices"]["USD/JPY"] = round(data["JPY"], 3)
            if "AUD" in data: state["prices"]["AUD/USD"] = round(1 / data["AUD"], 5)
            if "KES" in data: state["prices"]["USD/KES"] = round(data["KES"], 3)
            print("Live daily rates loaded")
    except Exception as e:
        print("Using default prices (" + str(e) + ")")


try_fetch_rates()


def price_simulator():
    while True:
        time.sleep(1)
        for pair, price in list(state["prices"].items()):
            vol = VOLATILITY.get(pair, 0.0002)
            change = random.uniform(-vol, vol)
            new_price = price * (1 + change)
            state["prices"][pair] = round(new_price, 3 if ("JPY" in pair or "KES" in pair) else 5)


threading.Thread(target=price_simulator, daemon=True).start()


def calc_pnl(position, current_price):
    entry = position["entry"]
    lots = position["lots"]
    if position["direction"] == "BUY":
        return (current_price - entry) * 100000 * lots
    return (entry - current_price) * 100000 * lots


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/state")
def get_state():
    positions = []
    total_pnl = 0.0
    for p in state["positions"]:
        cur = state["prices"].get(p["pair"], p["entry"])
        pnl = calc_pnl(p, cur)
        total_pnl += pnl
        positions.append({**p, "current": cur, "pnl": round(pnl, 2)})
    return jsonify({
        "prices": state["prices"],
        "balance": round(state["balance"], 2),
        "equity": round(state["balance"] + total_pnl, 2),
        "floating_pnl": round(total_pnl, 2),
        "positions": positions,
        "history": state["history"][-20:][::-1],
    })


@app.route("/api/trade", methods=["POST"])
def trade():
    data = request.get_json() or {}
    pair = data.get("pair")
    direction = (data.get("direction") or "").upper()
    try:
        lots = float(data.get("lots", 0.1))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid lots"}), 400

    if pair not in state["prices"]:
        return jsonify({"error": "Unknown pair"}), 400
    if direction not in ("BUY", "SELL"):
        return jsonify({"error": "Direction must be BUY or SELL"}), 400
    if lots <= 0 or lots > 10:
        return jsonify({"error": "Lots must be 0.01 - 10"}), 400

    entry = state["prices"][pair]
    pos = {
        "id": state["next_id"],
        "pair": pair,
        "direction": direction,
        "lots": lots,
        "entry": entry,
        "opened": datetime.now().strftime("%H:%M:%S"),
    }
    state["next_id"] += 1
    state["positions"].append(pos)
    return jsonify({"ok": True, "position": pos})


@app.route("/api/close/<int:pos_id>", methods=["POST"])
def close(pos_id):
    for i, p in enumerate(state["positions"]):
        if p["id"] == pos_id:
            cur = state["prices"].get(p["pair"], p["entry"])
            pnl = calc_pnl(p, cur)
            state["balance"] += pnl
            state["history"].append({
                **p,
                "exit": cur,
                "pnl": round(pnl, 2),
                "closed": datetime.now().strftime("%H:%M:%S"),
            })
            state["positions"].pop(i)
            return jsonify({"ok": True, "pnl": round(pnl, 2)})
    return jsonify({"error": "Position not found"}), 404


@app.route("/api/reset", methods=["POST"])
def reset():
    state["balance"] = 10000.0
    state["positions"] = []
    state["history"] = []
    state["next_id"] = 1
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
