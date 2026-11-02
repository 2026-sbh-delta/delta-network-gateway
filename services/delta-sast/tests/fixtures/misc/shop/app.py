"""SAST 테스트용 예제 앱. 일부러 취약하게 만든 부분이 있다."""

import os
import sqlite3
import subprocess

from flask import Flask, request
from markupsafe import escape

from .filters import clean

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-value-1234"
DB_PATH = "shop.db"


def db():
    return sqlite3.connect(DB_PATH)


@app.get("/search")
def search():
    q = request.args.get("q", "")
    conn = db()
    rows = conn.execute(f"SELECT name FROM items WHERE name LIKE '%{q}%'").fetchall()
    exact = conn.execute("SELECT name FROM items WHERE name = ?", (q,)).fetchall()
    return f"<p>{escape(q)}</p><p>{clean(q)}</p><p>{len(rows) + len(exact)}</p>"


@app.post("/ping")
def ping():
    host = request.form.get("host", "")
    os.system("ping -c 1 " + host)
    subprocess.run(["ping", "-c", "1", host], check=False)
    count = int(request.form.get("count", "1"))
    return f"<p>{count}</p>"


@app.post("/notes")
def add_note():
    conn = db()
    conn.execute("INSERT INTO notes (title, body) VALUES (?, ?)",
                 (request.form["title"], request.form["body"]))
    conn.commit()
    return "", 204


@app.get("/notes")
def list_notes():
    rows = db().execute("SELECT title, body FROM notes").fetchall()
    items = "".join(f"<li>{escape(r['title'])}: {r['body']}</li>" for r in rows)
    return f"<ul>{items}</ul>"


@app.get("/go")
def go():
    nxt = request.args.get("next", "/")
    return f'<a href="{nxt}">이동</a>'
