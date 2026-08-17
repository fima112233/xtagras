import functools
import json
import os
import re
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

from flask import (
    Flask, Response, flash, g, redirect, render_template, request, session,
    url_for, abort, jsonify, send_from_directory
)
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash, generate_password_hash

import db as database

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

SITE_NAME = "XTagras"
ADMIN_DISPLAY_NAME = "Администратор XTagras"
REACTIONS = ["❤️", "🔥", "😂", "😮", "😢"]
ALLOWED_AVATAR_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR") or (Path(__file__).parent / "static" / "uploads"))


def avatar_url(user):
    if not user:
        return ""
    path = user.get("avatar_path")
    if not path:
        return ""
    return url_for("uploaded_file", filename=path)


app.jinja_env.globals["avatar_url"] = avatar_url


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


def display_name(user):
    if not user:
        return ""
    return user.get("display_name") or user.get("username") or ""


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


app.jinja_env.globals["display_name"] = display_name
app.jinja_env.globals["REACTIONS"] = REACTIONS


@app.context_processor
def inject_globals():
    data = {
        "site_name": SITE_NAME,
        "unread_messages": getattr(g, "unread_messages", 0),
        "unread_notifications": getattr(g, "unread_notifications", 0),
        "trending": getattr(g, "trending", []),
        "trending_posts": getattr(g, "trending_posts", []),
        "suggested": getattr(g, "suggested", []),
        "announcement": getattr(g, "announcement", None),
    }
    return data


@app.before_request
def load_user():
    user_id = session.get("user_id")
    g.user = None
    g.unread_messages = 0
    g.unread_notifications = 0
    g.trending = []
    g.trending_posts = []
    g.suggested = []
    g.announcement = None
    if user_id is not None:
        conn = database.get_db()
        g.user = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if g.user is not None:
            g.user = dict(g.user)
            last = parse_ts(g.user.get("last_seen"))
            if last is None or (datetime.now() - last).total_seconds() > 60:
                conn.execute(
                    "UPDATE users SET last_seen = ? WHERE id = ?",
                    (database.now(), user_id),
                )
                conn.commit()
                g.user["last_seen"] = database.now()
            g.unread_messages = conn.execute(
                "SELECT COUNT(*) c FROM messages WHERE recipient_id = ? AND is_read = 0",
                (user_id,),
            ).fetchone()["c"]
            g.unread_notifications = conn.execute(
                "SELECT COUNT(*) c FROM notifications WHERE user_id = ? AND is_read = 0",
                (user_id,),
            ).fetchone()["c"]
            g.trending = compute_trending(conn)
            g.trending_posts = trending_posts(conn)
            g.suggested = suggested_users(conn, user_id)
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'announcement'"
            ).fetchone()
            if row and row["value"]:
                g.announcement = row["value"]
        conn.close()


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Сначала войдите в аккаунт.", "error")
            return redirect(url_for("login"))
        if g.user["is_banned"]:
            session.clear()
            flash("Ваш аккаунт заблокирован.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None or not g.user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def get_user_by_username(username):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_post(post_id):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_blocked_ids(conn, user_id):
    return {r["blocked_id"] for r in conn.execute(
        "SELECT blocked_id FROM blocks WHERE blocker_id = ?", (user_id,)
    )}


def get_blocker_ids(conn, user_id):
    return {r["blocker_id"] for r in conn.execute(
        "SELECT blocker_id FROM blocks WHERE blocked_id = ?", (user_id,)
    )}


def get_muted_ids(conn, user_id):
    return {r["muted_id"] for r in conn.execute(
        "SELECT muted_id FROM mutes WHERE muter_id = ?", (user_id,)
    )}


def add_notification(conn, user_id, actor_id, ntype, post_id=None):
    if user_id == actor_id:
        return
    conn.execute(
        "INSERT INTO notifications (user_id, actor_id, type, post_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, actor_id, ntype, post_id, database.now()),
    )


def compute_trending(conn):
    c = Counter()
    for row in conn.execute("SELECT content FROM posts"):
        for m in re.findall(r"#([\w]+)", row["content"]):
            c[m.lower()] += 1
    return [{"tag": t, "count": n} for t, n in c.most_common(5)]


def trending_posts(conn):
    rows = conn.execute(
        """
        SELECT p.id, p.content, u.username, u.display_name,
               (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) AS lc
        FROM posts p JOIN users u ON u.id = p.user_id
        WHERE u.is_banned = 0 AND p.privacy = 'public'
        ORDER BY lc DESC, p.created_at DESC LIMIT 5
        """
    ).fetchall()
    return [dict(r) for r in rows if r["lc"] > 0]


def suggested_users(conn, user_id):
    return [dict(r) for r in conn.execute(
        """
        SELECT * FROM users
        WHERE is_banned = 0 AND id != ?
          AND id NOT IN (SELECT following_id FROM follows WHERE follower_id = ?)
          AND id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = ?)
          AND id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id = ?)
          AND id NOT IN (SELECT muted_id FROM mutes WHERE muter_id = ?)
        ORDER BY created_at DESC LIMIT 5
        """,
        (user_id, user_id, user_id, user_id, user_id),
    )]


def decorate_post(conn, p, uid):
    reactions = {}
    for row in conn.execute(
        "SELECT reaction, COUNT(*) c FROM likes WHERE post_id = ? GROUP BY reaction",
        (p["id"],),
    ):
        reactions[row["reaction"]] = row["c"]
    p["reactions"] = reactions
    p["likes"] = sum(reactions.values())
    p["comments"] = conn.execute(
        "SELECT COUNT(*) c FROM comments WHERE post_id = ?", (p["id"],)
    ).fetchone()["c"]
    p["reposts"] = conn.execute(
        "SELECT COUNT(*) c FROM reposts WHERE post_id = ?", (p["id"],)
    ).fetchone()["c"]
    my = conn.execute(
        "SELECT reaction FROM likes WHERE post_id = ? AND user_id = ?", (p["id"], uid)
    ).fetchone()
    p["my_reaction"] = my["reaction"] if my else None
    p["reposted_by_me"] = conn.execute(
        "SELECT 1 FROM reposts WHERE post_id = ? AND user_id = ?", (p["id"], uid)
    ).fetchone() is not None
    p["bookmarked_by_me"] = conn.execute(
        "SELECT 1 FROM bookmarks WHERE post_id = ? AND user_id = ?", (p["id"], uid)
    ).fetchone() is not None
    return p


def build_feed(scope):
    conn = database.get_db()
    uid = g.user["id"]
    users = {
        u["id"]: {k: v for k, v in dict(u).items() if k != "id"} for u in conn.execute(
            "SELECT id, username, display_name, avatar_color, avatar_path, is_verified, is_admin, is_banned FROM users"
        )
    }
    followed = {r["following_id"] for r in conn.execute(
        "SELECT following_id FROM follows WHERE follower_id = ?", (uid,)
    )}
    blocked = get_blocked_ids(conn, uid)
    blocked_me = get_blocker_ids(conn, uid)
    muted = get_muted_ids(conn, uid)
    allowed = (followed | {uid}) if scope == "following" else None

    posts = [dict(r) for r in conn.execute("SELECT * FROM posts ORDER BY created_at DESC")]
    reposts = [dict(r) for r in conn.execute("SELECT * FROM reposts ORDER BY created_at DESC")]
    post_map = {p["id"]: p for p in posts}

    feed = []
    for p in posts:
        a = users.get(p["user_id"])
        if not a or a["is_banned"]:
            continue
        if p["user_id"] in blocked or p["user_id"] in blocked_me:
            continue
        if p["user_id"] in muted and p["user_id"] != uid:
            continue
        if p["privacy"] == "followers" and p["user_id"] != uid and p["user_id"] not in followed:
            continue
        if allowed is not None and p["user_id"] not in allowed:
            continue
        item = dict(p)
        item.update(a)
        item["kind"] = "post"
        item["reposted_by"] = None
        item["sort_key"] = p["created_at"]
        decorate_post(conn, item, uid)
        feed.append(item)

    for r in reposts:
        rp = users.get(r["user_id"])
        if not rp or rp["is_banned"]:
            continue
        if r["user_id"] in blocked or r["user_id"] in blocked_me or r["user_id"] in muted:
            continue
        if allowed is not None and r["user_id"] not in allowed:
            continue
        post = post_map.get(r["post_id"])
        if not post:
            continue
        a = users.get(post["user_id"])
        if not a or a["is_banned"] or post["user_id"] in blocked or post["user_id"] in blocked_me:
            continue
        if post["user_id"] in muted and post["user_id"] != uid:
            continue
        if post["privacy"] == "followers" and post["user_id"] != uid and post["user_id"] not in followed:
            continue
        inner = dict(post)
        inner.update(a)
        decorate_post(conn, inner, uid)
        feed.append({
            "kind": "repost",
            "post": inner,
            "reposted_by": {"username": rp["username"], "display_name": rp["display_name"]},
            "sort_key": r["created_at"],
        })
    conn.close()
    feed.sort(key=lambda x: x["sort_key"], reverse=True)
    return feed[:200]


@app.template_filter("time_ago")
def time_ago(ts):
    dt = parse_ts(ts)
    if dt is None:
        return ""
    diff = datetime.now() - dt
    secs = int(diff.total_seconds())
    if secs < 60:
        return "только что"
    mins = secs // 60
    if mins < 60:
        return f"{mins} мин. назад"
    hours = mins // 60
    if hours < 24:
        return f"{hours} ч. назад"
    days = hours // 24
    if days < 30:
        return f"{days} дн. назад"
    return dt.strftime("%d.%m.%Y")


@app.template_filter("rich_text")
def rich_text(text):
    esc = escape(text or "")
    esc = re.sub(
        r"#([\w]+)",
        r'<a class="tag-link" href="/hashtag/\1">#\1</a>',
        esc,
    )
    esc = re.sub(
        r"@([\w]+)",
        r'<a class="mention" href="/user/\1">@\1</a>',
        esc,
    )
    esc = esc.replace("\n", "<br>")
    return Markup(esc)


# ---------- Auth ----------

@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        bio = request.form.get("bio", "").strip()

        if not username or not password:
            flash("Заполните все обязательные поля.", "error")
        elif len(username) < 3 or len(username) > 20:
            flash("Логин должен быть от 3 до 20 символов.", "error")
        elif len(display_name) > 40:
            flash("Отображаемое имя — максимум 40 символов.", "error")
        elif len(password) < 6:
            flash("Пароль должен быть не короче 6 символов.", "error")
        elif password != confirm:
            flash("Пароли не совпадают.", "error")
        else:
            conn = database.get_db()
            exists = conn.execute(
                "SELECT id FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if exists:
                flash("Такой логин уже занят.", "error")
            else:
                colors = ["#6c5ce7", "#0984e3", "#00b894", "#e17055", "#d63031", "#e84393"]
                color = colors[len(username) % len(colors)]
                conn.execute(
                    "INSERT INTO users (username, display_name, password_hash, bio, avatar_color, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (username, display_name, generate_password_hash(password), bio, color, database.now()),
                )
                user_id = conn.execute(
                    "SELECT id FROM users WHERE username = ?", (username,)
                ).fetchone()["id"]
                admin = conn.execute(
                    "SELECT id FROM users WHERE is_admin = 1 LIMIT 1"
                ).fetchone()
                if admin:
                    conn.execute(
                        "INSERT OR IGNORE INTO follows (follower_id, following_id) VALUES (?, ?)",
                        (user_id, admin["id"]),
                    )
                conn.commit()
                conn.close()
                session["user_id"] = user_id
                flash("Аккаунт создан. Добро пожаловать!", "success")
                return redirect(url_for("index"))
            conn.close()
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        login_name = request.form.get("login", "").strip()
        password = request.form.get("password", "")
        conn = database.get_db()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (login_name,),
        ).fetchone()
        conn.close()
        if row and check_password_hash(row["password_hash"], password):
            if row["is_banned"]:
                flash("Ваш аккаунт заблокирован администратором.", "error")
            else:
                session["user_id"] = row["id"]
                flash("Вы вошли в систему.", "success")
                return redirect(url_for("index"))
        else:
            flash("Неверный логин или пароль.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Вы вышли из аккаунта.", "success")
    return redirect(url_for("login"))


# ---------- Feed & posts ----------

@app.route("/")
def index():
    if g.user is None:
        return render_template("landing.html")
    scope = "following" if request.args.get("feed") == "following" else "all"
    feed = build_feed(scope)
    return render_template("index.html", feed=feed, scope=scope)


@app.route("/post/new", methods=["POST"])
@login_required
def create_post():
    content = request.form.get("content", "").strip()
    privacy = request.form.get("privacy", "public")
    if privacy not in ("public", "followers"):
        privacy = "public"
    if not content:
        flash("Пост не может быть пустым.", "error")
    elif len(content) > 2000:
        flash("Пост слишком длинный (максимум 2000 символов).", "error")
    else:
        conn = database.get_db()
        conn.execute(
            "INSERT INTO posts (user_id, content, privacy, created_at) VALUES (?, ?, ?, ?)",
            (g.user["id"], content, privacy, database.now()),
        )
        conn.commit()
        conn.close()
        flash("Пост опубликован.", "success")
    return redirect(request.referrer or url_for("index"))


@app.route("/post/<int:post_id>")
@login_required
def view_post(post_id):
    post = get_post(post_id)
    if not post:
        abort(404)
    conn = database.get_db()
    author = dict(conn.execute(
        "SELECT id, username, display_name, avatar_color, avatar_path, is_verified, is_admin, bio, created_at, last_seen "
        "FROM users WHERE id = ?",
        (post["user_id"],),
    ).fetchone() or {})
    comments = [dict(c) for c in conn.execute(
        """
        SELECT c.*, u.username, u.display_name, u.avatar_color, u.avatar_path, u.is_verified, u.is_admin
        FROM comments c JOIN users u ON u.id = c.user_id
        WHERE c.post_id = ? ORDER BY c.created_at ASC
        """,
        (post_id,),
    ).fetchall()]
    by_id = {c["id"]: c for c in comments}
    for c in comments:
        c["replies"] = []
    roots = []
    for c in comments:
        if c["parent_id"] and c["parent_id"] in by_id:
            by_id[c["parent_id"]]["replies"].append(c)
        else:
            roots.append(c)
    decorate_post(conn, post, g.user["id"])
    post["views"] = conn.execute(
        "SELECT COUNT(*) c FROM post_views WHERE post_id = ?", (post_id,)
    ).fetchone()["c"]
    if post["user_id"] != g.user["id"]:
        conn.execute(
            "INSERT OR REPLACE INTO post_views (post_id, viewer_id, viewed_at) VALUES (?, ?, ?)",
            (post_id, g.user["id"], database.now()),
        )
        conn.commit()
    post["can_edit"] = post["user_id"] == g.user["id"]
    post["can_pin"] = post["user_id"] == g.user["id"]
    conn.close()
    return render_template("post.html", post=post, author=author, comments=roots)


@app.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    post = get_post(post_id)
    if not post:
        abort(404)
    if post["user_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403)
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        privacy = request.form.get("privacy", post.get("privacy", "public"))
        if privacy not in ("public", "followers"):
            privacy = "public"
        if not content:
            flash("Пост не может быть пустым.", "error")
        else:
            conn = database.get_db()
            conn.execute(
                "UPDATE posts SET content = ?, privacy = ?, edited_at = ? WHERE id = ?",
                (content, privacy, database.now(), post_id),
            )
            conn.commit()
            conn.close()
            flash("Пост обновлён.", "success")
            return redirect(url_for("view_post", post_id=post_id))
    return render_template("edit_post.html", post=post)


@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    post = get_post(post_id)
    if not post:
        abort(404)
    if post["user_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403)
    conn = database.get_db()
    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()
    flash("Пост удалён.", "success")
    return redirect(url_for("index"))


@app.route("/post/<int:post_id>/react", methods=["POST"])
@login_required
def toggle_reaction(post_id):
    post = get_post(post_id)
    if not post:
        abort(404)
    emoji = request.form.get("emoji", "❤️")
    if emoji not in REACTIONS:
        emoji = "❤️"
    conn = database.get_db()
    existing = conn.execute(
        "SELECT reaction FROM likes WHERE post_id = ? AND user_id = ?",
        (post_id, g.user["id"]),
    ).fetchone()
    if existing and existing["reaction"] == emoji:
        conn.execute(
            "DELETE FROM likes WHERE post_id = ? AND user_id = ?",
            (post_id, g.user["id"]),
        )
    else:
        conn.execute(
            "INSERT OR REPLACE INTO likes (user_id, post_id, reaction) VALUES (?, ?, ?)",
            (g.user["id"], post_id, emoji),
        )
        if not existing:
            add_notification(conn, post["user_id"], g.user["id"], "like", post_id)
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("view_post", post_id=post_id))


@app.route("/post/<int:post_id>/repost", methods=["POST"])
@login_required
def toggle_repost(post_id):
    post = get_post(post_id)
    if not post:
        abort(404)
    conn = database.get_db()
    existing = conn.execute(
        "SELECT 1 FROM reposts WHERE post_id = ? AND user_id = ?",
        (post_id, g.user["id"]),
    ).fetchone()
    if existing:
        conn.execute(
            "DELETE FROM reposts WHERE post_id = ? AND user_id = ?",
            (post_id, g.user["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO reposts (post_id, user_id, created_at) VALUES (?, ?, ?)",
            (post_id, g.user["id"], database.now()),
        )
        add_notification(conn, post["user_id"], g.user["id"], "repost", post_id)
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("view_post", post_id=post_id))


@app.route("/post/<int:post_id>/bookmark", methods=["POST"])
@login_required
def toggle_bookmark(post_id):
    post = get_post(post_id)
    if not post:
        abort(404)
    conn = database.get_db()
    existing = conn.execute(
        "SELECT 1 FROM bookmarks WHERE post_id = ? AND user_id = ?",
        (post_id, g.user["id"]),
    ).fetchone()
    if existing:
        conn.execute(
            "DELETE FROM bookmarks WHERE post_id = ? AND user_id = ?",
            (post_id, g.user["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO bookmarks (post_id, user_id, created_at) VALUES (?, ?, ?)",
            (post_id, g.user["id"], database.now()),
        )
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("view_post", post_id=post_id))


@app.route("/post/<int:post_id>/pin", methods=["POST"])
@login_required
def toggle_pin(post_id):
    post = get_post(post_id)
    if not post:
        abort(404)
    if post["user_id"] != g.user["id"]:
        abort(403)
    conn = database.get_db()
    current = g.user.get("pinned_post_id")
    conn.execute(
        "UPDATE users SET pinned_post_id = ? WHERE id = ?",
        (None if current == post_id else post_id, g.user["id"]),
    )
    conn.commit()
    conn.close()
    flash("Закреплено." if current != post_id else "Откреплено.", "success")
    return redirect(request.referrer or url_for("index"))


@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def add_comment(post_id):
    post = get_post(post_id)
    if not post:
        abort(404)
    content = request.form.get("content", "").strip()
    parent_id = request.form.get("parent_id", "").strip()
    parent_id = int(parent_id) if parent_id.isdigit() else None
    if content:
        conn = database.get_db()
        if parent_id is not None:
            parent = conn.execute(
                "SELECT id FROM comments WHERE id = ? AND post_id = ?",
                (parent_id, post_id),
            ).fetchone()
            if not parent:
                parent_id = None
        conn.execute(
            "INSERT INTO comments (post_id, user_id, parent_id, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (post_id, g.user["id"], parent_id, content, database.now()),
        )
        add_notification(conn, post["user_id"], g.user["id"], "comment", post_id)
        if parent_id is not None:
            parent_owner = conn.execute(
                "SELECT user_id FROM comments WHERE id = ?", (parent_id,)
            ).fetchone()
            if parent_owner and parent_owner["user_id"] != g.user["id"]:
                add_notification(conn, parent_owner["user_id"], g.user["id"], "reply", post_id)
        conn.commit()
        conn.close()
    return redirect(url_for("view_post", post_id=post_id))


@app.route("/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
    if row and (row["user_id"] == g.user["id"] or g.user["is_admin"]):
        conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        conn.commit()
    post_id = row["post_id"] if row else None
    conn.close()
    return redirect(url_for("view_post", post_id=post_id) if post_id else url_for("index"))


# ---------- Reports ----------

@app.route("/post/<int:post_id>/report", methods=["POST"])
@login_required
def report_post(post_id):
    post = get_post(post_id)
    if not post:
        abort(404)
    reason = request.form.get("reason", "").strip()
    conn = database.get_db()
    conn.execute(
        "INSERT INTO reports (reporter_id, target_type, target_id, reason, created_at) "
        "VALUES (?, 'post', ?, ?, ?)",
        (g.user["id"], post_id, reason, database.now()),
    )
    conn.commit()
    conn.close()
    flash("Жалоба отправлена модераторам.", "success")
    return redirect(request.referrer or url_for("index"))


@app.route("/user/<username>/report", methods=["POST"])
@login_required
def report_user(username):
    user = get_user_by_username(username)
    if not user or user["id"] == g.user["id"]:
        abort(404)
    reason = request.form.get("reason", "").strip()
    conn = database.get_db()
    conn.execute(
        "INSERT INTO reports (reporter_id, target_type, target_id, reason, created_at) "
        "VALUES (?, 'user', ?, ?, ?)",
        (g.user["id"], user["id"], reason, database.now()),
    )
    conn.commit()
    conn.close()
    flash("Жалоба отправлена модераторам.", "success")
    return redirect(request.referrer or url_for("profile", username=username))


# ---------- Hashtags & bookmarks ----------

@app.route("/hashtag/<tag>")
@login_required
def hashtag(tag):
    conn = database.get_db()
    rows = conn.execute(
        """
        SELECT p.*, u.username, u.display_name, u.avatar_color, u.avatar_path, u.is_verified, u.is_admin
        FROM posts p JOIN users u ON u.id = p.user_id
        WHERE u.is_banned = 0 AND p.privacy = 'public' AND p.content LIKE ?
        ORDER BY p.created_at DESC LIMIT 100
        """,
        (f"%#{tag}%",),
    ).fetchall()
    posts = [dict(r) for r in rows]
    for p in posts:
        decorate_post(conn, p, g.user["id"])
    conn.close()
    return render_template("hashtag.html", tag=tag, posts=posts)


@app.route("/bookmarks")
@login_required
def bookmarks():
    conn = database.get_db()
    rows = conn.execute(
        """
        SELECT p.*, u.username, u.display_name, u.avatar_color, u.avatar_path, u.is_verified, u.is_admin
        FROM bookmarks b
        JOIN posts p ON p.id = b.post_id
        JOIN users u ON u.id = p.user_id
        WHERE b.user_id = ? AND u.is_banned = 0
        ORDER BY b.created_at DESC
        """,
        (g.user["id"],),
    ).fetchall()
    posts = [dict(r) for r in rows]
    for p in posts:
        decorate_post(conn, p, g.user["id"])
    conn.close()
    return render_template("bookmarks.html", posts=posts)


# ---------- Notifications ----------

@app.route("/notifications")
@login_required
def notifications():
    conn = database.get_db()
    conn.execute(
        "UPDATE notifications SET is_read = 1 WHERE user_id = ?", (g.user["id"],)
    )
    conn.commit()
    rows = conn.execute(
        """
        SELECT n.*, u.username, u.display_name, u.avatar_color, u.avatar_path
        FROM notifications n JOIN users u ON u.id = n.actor_id
        WHERE n.user_id = ? ORDER BY n.created_at DESC LIMIT 100
        """,
        (g.user["id"],),
    ).fetchall()
    notifs = [dict(r) for r in rows]
    for n in notifs:
        text = {
            "like": "отреагировал(а) на ваш пост",
            "comment": "прокомментировал(а) ваш пост",
            "reply": "ответил(а) на ваш комментарий",
            "follow": "подписался(ась) на вас",
            "repost": "сделал(а) репост вашего поста",
            "message": "отправил(а) вам сообщение",
        }.get(n["type"], "")
        n["text"] = text
    conn.close()
    return render_template("notifications.html", notifs=notifs)


# ---------- Users & follows ----------

@app.route("/user/<username>")
@login_required
def profile(username):
    user = get_user_by_username(username)
    if not user:
        abort(404)
    conn = database.get_db()
    uid = g.user["id"]
    followed = {r["following_id"] for r in conn.execute(
        "SELECT following_id FROM follows WHERE follower_id = ?", (uid,)
    )}
    rows = conn.execute(
        """
        SELECT p.*, u.username, u.display_name, u.avatar_color, u.avatar_path, u.is_verified, u.is_admin
        FROM posts p JOIN users u ON u.id = p.user_id
        WHERE p.user_id = ? ORDER BY p.created_at DESC
        """,
        (user["id"],),
    ).fetchall()
    posts = []
    for r in rows:
        p = dict(r)
        if p["privacy"] == "followers" and p["user_id"] != uid and p["user_id"] not in followed and not g.user["is_admin"]:
            continue
        decorate_post(conn, p, uid)
        posts.append(p)
    pinned = None
    if user.get("pinned_post_id"):
        for p in posts:
            if p["id"] == user["pinned_post_id"]:
                pinned = p
                break
    followers = conn.execute(
        "SELECT COUNT(*) c FROM follows WHERE following_id = ?", (user["id"],)
    ).fetchone()["c"]
    following = conn.execute(
        "SELECT COUNT(*) c FROM follows WHERE follower_id = ?", (user["id"],)
    ).fetchone()["c"]
    is_following = conn.execute(
        "SELECT 1 FROM follows WHERE follower_id = ? AND following_id = ?",
        (uid, user["id"]),
    ).fetchone() is not None
    they_follow_me = conn.execute(
        "SELECT 1 FROM follows WHERE follower_id = ? AND following_id = ?",
        (user["id"], uid),
    ).fetchone() is not None
    is_blocked = conn.execute(
        "SELECT 1 FROM blocks WHERE blocker_id = ? AND blocked_id = ?",
        (uid, user["id"]),
    ).fetchone() is not None
    is_muted = conn.execute(
        "SELECT 1 FROM mutes WHERE muter_id = ? AND muted_id = ?",
        (uid, user["id"]),
    ).fetchone() is not None
    profile_views = conn.execute(
        "SELECT COUNT(*) c FROM profile_views WHERE viewed_id = ?", (user["id"],)
    ).fetchone()["c"]
    if user["id"] != uid:
        conn.execute(
            "INSERT OR REPLACE INTO profile_views (viewed_id, viewer_id, viewed_at) VALUES (?, ?, ?)",
            (user["id"], uid, database.now()),
        )
        conn.commit()
    last = parse_ts(user.get("last_seen"))
    is_online = last is not None and (datetime.now() - last).total_seconds() < 300
    conn.close()
    return render_template(
        "profile.html",
        user=user,
        posts=posts,
        pinned=pinned,
        posts_count=len(posts),
        followers=followers,
        following=following,
        is_following=is_following,
        mutual=is_following and they_follow_me,
        is_blocked=is_blocked,
        is_muted=is_muted,
        profile_views=profile_views,
        is_online=is_online,
        is_me=user["id"] == g.user["id"],
    )


@app.route("/user/<username>/follow", methods=["POST"])
@login_required
def follow(username):
    user = get_user_by_username(username)
    if not user or user["id"] == g.user["id"]:
        abort(404)
    if user["is_admin"]:
        flash(f"От официального аккаунта {SITE_NAME} нельзя отписаться.", "error")
        return redirect(request.referrer or url_for("profile", username=username))
    conn = database.get_db()
    existing = conn.execute(
        "SELECT 1 FROM follows WHERE follower_id = ? AND following_id = ?",
        (g.user["id"], user["id"]),
    ).fetchone()
    if existing:
        conn.execute(
            "DELETE FROM follows WHERE follower_id = ? AND following_id = ?",
            (g.user["id"], user["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO follows (follower_id, following_id) VALUES (?, ?)",
            (g.user["id"], user["id"]),
        )
        add_notification(conn, user["id"], g.user["id"], "follow")
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("profile", username=username))


@app.route("/user/<username>/block", methods=["POST"])
@login_required
def block(username):
    user = get_user_by_username(username)
    if not user or user["id"] == g.user["id"] or user["is_admin"]:
        abort(404)
    conn = database.get_db()
    conn.execute(
        "INSERT OR IGNORE INTO blocks (blocker_id, blocked_id) VALUES (?, ?)",
        (g.user["id"], user["id"]),
    )
    conn.execute(
        "DELETE FROM follows WHERE (follower_id = ? AND following_id = ?) OR (follower_id = ? AND following_id = ?)",
        (g.user["id"], user["id"], user["id"], g.user["id"]),
    )
    conn.commit()
    conn.close()
    flash(f"Пользователь @{user['username']} заблокирован.", "success")
    return redirect(url_for("profile", username=username))


@app.route("/user/<username>/unblock", methods=["POST"])
@login_required
def unblock(username):
    user = get_user_by_username(username)
    if not user:
        abort(404)
    conn = database.get_db()
    conn.execute(
        "DELETE FROM blocks WHERE blocker_id = ? AND blocked_id = ?",
        (g.user["id"], user["id"]),
    )
    conn.commit()
    conn.close()
    flash(f"Пользователь @{user['username']} разблокирован.", "success")
    return redirect(url_for("profile", username=username))


@app.route("/user/<username>/mute", methods=["POST"])
@login_required
def mute(username):
    user = get_user_by_username(username)
    if not user or user["id"] == g.user["id"] or user["is_admin"]:
        abort(404)
    conn = database.get_db()
    conn.execute(
        "INSERT OR IGNORE INTO mutes (muter_id, muted_id) VALUES (?, ?)",
        (g.user["id"], user["id"]),
    )
    conn.commit()
    conn.close()
    flash(f"Посты @{user['username']} больше не показываются в ленте.", "success")
    return redirect(url_for("profile", username=username))


@app.route("/user/<username>/unmute", methods=["POST"])
@login_required
def unmute(username):
    user = get_user_by_username(username)
    if not user:
        abort(404)
    conn = database.get_db()
    conn.execute(
        "DELETE FROM mutes WHERE muter_id = ? AND muted_id = ?",
        (g.user["id"], user["id"]),
    )
    conn.commit()
    conn.close()
    flash(f"Посты @{user['username']} снова в ленте.", "success")
    return redirect(url_for("profile", username=username))


@app.route("/user/<username>/followers")
@login_required
def followers(username):
    user = get_user_by_username(username)
    if not user:
        abort(404)
    conn = database.get_db()
    rows = conn.execute(
        """
        SELECT u.* FROM follows f JOIN users u ON u.id = f.follower_id
        WHERE f.following_id = ? ORDER BY u.username
        """,
        (user["id"],),
    ).fetchall()
    conn.close()
    return render_template("followers.html", user=user, people=[dict(r) for r in rows], kind="подписчики")


@app.route("/user/<username>/following")
@login_required
def following(username):
    user = get_user_by_username(username)
    if not user:
        abort(404)
    conn = database.get_db()
    rows = conn.execute(
        """
        SELECT u.* FROM follows f JOIN users u ON u.id = f.following_id
        WHERE f.follower_id = ? ORDER BY u.username
        """,
        (user["id"],),
    ).fetchall()
    conn.close()
    return render_template("followers.html", user=user, people=[dict(r) for r in rows], kind="подписки")


@app.route("/profile/edit")
@login_required
def edit_profile():
    return redirect(url_for("settings"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        username = request.form.get("username", "").strip()
        bio = request.form.get("bio", "").strip()
        avatar_color = request.form.get("avatar_color", "").strip() or "#6c5ce7"
        allow_dms = request.form.get("allow_dms") == "on"
        dark_mode = request.form.get("dark_mode") == "on"
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        remove_avatar = request.form.get("remove_avatar") == "on"
        avatar_file = request.files.get("avatar")

        errors = []
        if not username:
            errors.append("Логин не может быть пустым.")
        elif len(username) < 3 or len(username) > 20:
            errors.append("Логин должен быть от 3 до 20 символов.")
        if len(display_name) > 40:
            errors.append("Отображаемое имя — максимум 40 символов.")
        if len(bio) > 300:
            errors.append("О себе — максимум 300 символов.")
        if not re.match(r"^#[0-9a-fA-F]{6}$", avatar_color):
            errors.append("Некорректный цвет аватара.")

        new_avatar_path = None
        if remove_avatar:
            new_avatar_path = ""
        elif avatar_file and avatar_file.filename:
            ext = avatar_file.filename.rsplit(".", 1)[-1].lower() if "." in avatar_file.filename else ""
            if ext not in ALLOWED_AVATAR_EXT:
                errors.append("Недопустимый формат файла. Разрешены: png, jpg, jpeg, gif, webp.")
            else:
                data = avatar_file.read()
                if len(data) > 5 * 1024 * 1024:
                    errors.append("Файл слишком большой (максимум 5 МБ).")
                elif not data:
                    errors.append("Пустой файл.")
                else:
                    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
                    new_avatar_path = uuid.uuid4().hex + "." + ext
                    (UPLOAD_DIR / new_avatar_path).write_bytes(data)

        conn = database.get_db()
        exists = conn.execute(
            "SELECT id FROM users WHERE username = ? AND id != ?",
            (username, g.user["id"]),
        ).fetchone()
        if exists:
            errors.append("Такой логин уже занят.")

        password_hash = g.user["password_hash"]
        if new_password or confirm_password:
            if not check_password_hash(g.user["password_hash"], current_password):
                errors.append("Текущий пароль указан неверно.")
            elif len(new_password) < 6:
                errors.append("Новый пароль должен быть не короче 6 символов.")
            elif new_password != confirm_password:
                errors.append("Новые пароли не совпадают.")
            else:
                password_hash = generate_password_hash(new_password)

        if errors:
            for e in errors:
                flash(e, "error")
        else:
            old_avatar = g.user.get("avatar_path")
            conn.execute(
                "UPDATE users SET display_name = ?, username = ?, bio = ?, "
                "avatar_color = ?, allow_dms = ?, dark_mode = ?, password_hash = ?"
                + (", avatar_path = ?" if new_avatar_path is not None else "")
                + " WHERE id = ?",
                (display_name, username, bio, avatar_color,
                 1 if allow_dms else 0, 1 if dark_mode else 0, password_hash)
                + ((new_avatar_path,) if new_avatar_path is not None else ())
                + (g.user["id"],),
            )
            conn.commit()
            if new_avatar_path is not None and old_avatar and old_avatar != new_avatar_path:
                old_file = UPLOAD_DIR / old_avatar
                if old_file.exists():
                    old_file.unlink()
            flash("Настройки сохранены.", "success")
            conn.close()
            return redirect(url_for("settings"))
        conn.close()
    return render_template("settings.html")


@app.route("/toggle_theme", methods=["POST"])
@login_required
def toggle_theme():
    conn = database.get_db()
    conn.execute(
        "UPDATE users SET dark_mode = ? WHERE id = ?",
        (0 if g.user["dark_mode"] else 1, g.user["id"]),
    )
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("index"))


@app.route("/export")
@login_required
def export_data():
    conn = database.get_db()
    uid = g.user["id"]
    data = {
        "user": dict(conn.execute(
            "SELECT id, username, display_name, bio, avatar_color, avatar_path, created_at FROM users WHERE id = ?",
            (uid,),
        ).fetchone() or {}),
        "posts": [dict(r) for r in conn.execute("SELECT id, content, privacy, created_at, edited_at FROM posts WHERE user_id = ?", (uid,))],
        "comments": [dict(r) for r in conn.execute("SELECT id, post_id, content, created_at FROM comments WHERE user_id = ?", (uid,))],
        "reactions": [dict(r) for r in conn.execute("SELECT post_id, reaction FROM likes WHERE user_id = ?", (uid,))],
        "reposts": [dict(r) for r in conn.execute("SELECT post_id, created_at FROM reposts WHERE user_id = ?", (uid,))],
        "bookmarks": [dict(r) for r in conn.execute("SELECT post_id, created_at FROM bookmarks WHERE user_id = ?", (uid,))],
        "follows": [dict(r) for r in conn.execute("SELECT following_id FROM follows WHERE follower_id = ?", (uid,))],
        "followers": [dict(r) for r in conn.execute("SELECT follower_id FROM follows WHERE following_id = ?", (uid,))],
        "messages": [dict(r) for r in conn.execute("SELECT id, sender_id, recipient_id, content, created_at, is_read FROM messages WHERE sender_id = ? OR recipient_id = ?", (uid, uid))],
    }
    conn.close()
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=xtagras-export.json"},
    )


@app.route("/delete_account", methods=["POST"])
@login_required
def delete_account():
    if g.user["is_admin"]:
        flash("Администратор не может удалить свой аккаунт.", "error")
        return redirect(url_for("settings"))
    conn = database.get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (g.user["id"],))
    conn.commit()
    conn.close()
    session.clear()
    flash("Ваш аккаунт удалён.", "success")
    return redirect(url_for("index"))


@app.route("/users")
@login_required
def users():
    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM users WHERE is_banned = 0 ORDER BY username"
    ).fetchall()
    conn.close()
    return render_template("users.html", people=[dict(r) for r in rows])


@app.route("/api/users")
@login_required
def api_users():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    conn = database.get_db()
    rows = conn.execute(
        """
        SELECT username, display_name, avatar_color, avatar_path, is_verified, is_admin
        FROM users
        WHERE is_banned = 0 AND (username LIKE ? OR display_name LIKE ?)
        ORDER BY username LIMIT 8
        """,
        (f"%{q}%", f"%{q}%"),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    results = []
    posts = []
    if q:
        conn = database.get_db()
        rows = conn.execute(
            "SELECT * FROM users WHERE (username LIKE ? OR display_name LIKE ?) AND is_banned = 0 LIMIT 20",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
        results = [dict(r) for r in rows]
        post_rows = conn.execute(
            """
            SELECT p.*, u.username, u.display_name, u.avatar_color, u.avatar_path, u.is_verified, u.is_admin
            FROM posts p JOIN users u ON u.id = p.user_id
            WHERE p.content LIKE ? AND u.is_banned = 0 AND p.privacy = 'public'
            ORDER BY p.created_at DESC LIMIT 20
            """,
            (f"%{q}%",),
        ).fetchall()
        posts = [dict(r) for r in post_rows]
        for p in posts:
            decorate_post(conn, p, g.user["id"])
        conn.close()
    return render_template("search.html", q=q, results=results, posts=posts)


# ---------- Messages ----------

@app.route("/messages")
@login_required
def messages():
    conn = database.get_db()
    rows = conn.execute(
        """
        SELECT m.*,
               CASE WHEN m.sender_id = ? THEN m.recipient_id ELSE m.sender_id END AS partner_id
        FROM messages m
        WHERE m.sender_id = ? OR m.recipient_id = ?
        ORDER BY m.created_at DESC
        """,
        (g.user["id"], g.user["id"], g.user["id"]),
    ).fetchall()
    conversations = {}
    for m in rows:
        m = dict(m)
        pid = m["partner_id"]
        if pid not in conversations:
            partner = conn.execute(
                "SELECT id, username, display_name, avatar_color, avatar_path, is_verified, is_admin, allow_dms "
                "FROM users WHERE id = ?",
                (pid,),
            ).fetchone()
            if partner is None:
                continue
            conversations[pid] = {"partner": dict(partner), "last": m, "unread": 0}
    for pid in conversations:
        conversations[pid]["unread"] = conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE sender_id = ? AND recipient_id = ? AND is_read = 0",
            (pid, g.user["id"]),
        ).fetchone()["c"]
    conn.close()
    convos = sorted(conversations.values(), key=lambda c: c["last"]["created_at"], reverse=True)
    return render_template("messages.html", conversations=convos)


@app.route("/messages/<username>", methods=["GET", "POST"])
@login_required
def conversation(username):
    partner = get_user_by_username(username)
    if not partner or partner["id"] == g.user["id"]:
        abort(404)
    conn = database.get_db()
    i_block = conn.execute(
        "SELECT 1 FROM blocks WHERE blocker_id = ? AND blocked_id = ?",
        (g.user["id"], partner["id"]),
    ).fetchone()
    they_block = conn.execute(
        "SELECT 1 FROM blocks WHERE blocker_id = ? AND blocked_id = ?",
        (partner["id"], g.user["id"]),
    ).fetchone()
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if not content:
            flash("Сообщение не может быть пустым.", "error")
        elif i_block or they_block:
            flash("Нельзя писать этому пользователю.", "error")
        elif not partner["allow_dms"]:
            flash("Этот пользователь отключил личные сообщения.", "error")
        else:
            conn.execute(
                "INSERT INTO messages (sender_id, recipient_id, content, created_at) VALUES (?, ?, ?, ?)",
                (g.user["id"], partner["id"], content, database.now()),
            )
            add_notification(conn, partner["id"], g.user["id"], "message")
            conn.commit()
        return redirect(url_for("conversation", username=username))
    conn.execute(
        "UPDATE messages SET is_read = 1 WHERE sender_id = ? AND recipient_id = ?",
        (partner["id"], g.user["id"]),
    )
    conn.commit()
    msgs = [dict(m) for m in conn.execute(
        """
        SELECT m.*, u.username AS s_username, u.display_name AS s_display_name
        FROM messages m JOIN users u ON u.id = m.sender_id
        WHERE (m.sender_id = ? AND m.recipient_id = ?)
           OR (m.sender_id = ? AND m.recipient_id = ?)
        ORDER BY m.created_at ASC
        """,
        (g.user["id"], partner["id"], partner["id"], g.user["id"]),
    ).fetchall()]
    conn.close()
    return render_template(
        "conversation.html", partner=partner, msgs=msgs,
        can_send=not (i_block or they_block) and partner["allow_dms"],
    )


@app.route("/messages/delete/<int:msg_id>", methods=["POST"])
@login_required
def delete_message(msg_id):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
    if row and row["sender_id"] == g.user["id"]:
        conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("messages"))


# ---------- Admin ----------

@app.route("/admin")
@admin_required
def admin():
    conn = database.get_db()
    stats = {
        "users": conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
        "posts": conn.execute("SELECT COUNT(*) c FROM posts").fetchone()["c"],
        "comments": conn.execute("SELECT COUNT(*) c FROM comments").fetchone()["c"],
        "likes": conn.execute("SELECT COUNT(*) c FROM likes").fetchone()["c"],
        "reposts": conn.execute("SELECT COUNT(*) c FROM reposts").fetchone()["c"],
        "messages": conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"],
        "banned": conn.execute("SELECT COUNT(*) c FROM users WHERE is_banned = 1").fetchone()["c"],
        "verified": conn.execute("SELECT COUNT(*) c FROM users WHERE is_verified = 1").fetchone()["c"],
        "reports": conn.execute("SELECT COUNT(*) c FROM reports WHERE status = 'open'").fetchone()["c"],
    }
    users = [dict(u) for u in conn.execute(
        """
        SELECT u.*,
               (SELECT COUNT(*) FROM posts p WHERE p.user_id = u.id) AS post_count
        FROM users u ORDER BY u.created_at DESC
        """
    ).fetchall()]
    posts = [dict(p) for p in conn.execute(
        """
        SELECT p.*, u.username, u.display_name FROM posts p JOIN users u ON u.id = p.user_id
        ORDER BY p.created_at DESC LIMIT 100
        """
    ).fetchall()]
    reports = [dict(r) for r in conn.execute(
        """
        SELECT r.*, u.username AS reporter_name, u.display_name AS reporter_display
        FROM reports r JOIN users u ON u.id = r.reporter_id
        WHERE r.status = 'open' ORDER BY r.created_at DESC LIMIT 100
        """
    ).fetchall()]
    for r in reports:
        if r["target_type"] == "user":
            t = conn.execute("SELECT id, username, display_name FROM users WHERE id = ?", (r["target_id"],)).fetchone()
            r["target_label"] = f"@{t['username']} ({t['display_name'] or t['username']})" if t else "удалён"
        else:
            t = conn.execute("SELECT id, content FROM posts WHERE id = ?", (r["target_id"],)).fetchone()
            r["target_label"] = (t["content"] or "")[:80] if t else "удалён"
    conn.close()
    return render_template("admin.html", stats=stats, users=users, posts=posts, reports=reports)


@app.route("/admin/report/<int:report_id>/dismiss", methods=["POST"])
@admin_required
def admin_dismiss_report(report_id):
    conn = database.get_db()
    conn.execute("UPDATE reports SET status = 'dismissed' WHERE id = ?", (report_id,))
    conn.commit()
    conn.close()
    flash("Жалоба отклонена.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/report/<int:report_id>/delete_target", methods=["POST"])
@admin_required
def admin_report_delete_target(report_id):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if row:
        if row["target_type"] == "user":
            if row["target_id"] != g.user["id"]:
                conn.execute("DELETE FROM users WHERE id = ?", (row["target_id"],))
        else:
            conn.execute("DELETE FROM posts WHERE id = ?", (row["target_id"],))
        conn.execute("UPDATE reports SET status = 'dismissed' WHERE id = ?", (report_id,))
        conn.commit()
    conn.close()
    flash("Контент удалён, жалоба закрыта.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/announcement", methods=["POST"])
@admin_required
def admin_announcement():
    text = request.form.get("announcement", "").strip()
    conn = database.get_db()
    if text:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('announcement', ?)",
            (text,),
        )
    else:
        conn.execute("DELETE FROM app_settings WHERE key = 'announcement'")
    conn.commit()
    conn.close()
    flash("Объявление обновлено." if text else "Объявление убрано.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/ban", methods=["POST"])
@admin_required
def admin_ban(user_id):
    if user_id == g.user["id"]:
        flash("Нельзя заблокировать самого себя.", "error")
        return redirect(url_for("admin"))
    conn = database.get_db()
    conn.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash("Пользователь заблокирован.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/unban", methods=["POST"])
@admin_required
def admin_unban(user_id):
    conn = database.get_db()
    conn.execute("UPDATE users SET is_banned = 0 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash("Пользователь разблокирован.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/verify", methods=["POST"])
@admin_required
def admin_verify(user_id):
    conn = database.get_db()
    conn.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash("Галочка выдана.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/unverify", methods=["POST"])
@admin_required
def admin_unverify(user_id):
    conn = database.get_db()
    conn.execute("UPDATE users SET is_verified = 0 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash("Галочка снята.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    if user_id == g.user["id"]:
        flash("Нельзя удалить самого себя.", "error")
        return redirect(url_for("admin"))
    conn = database.get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash("Пользователь удалён вместе с его контентом.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/post/<int:post_id>/delete", methods=["POST"])
@admin_required
def admin_delete_post(post_id):
    conn = database.get_db()
    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()
    flash("Пост удалён.", "success")
    return redirect(url_for("admin"))


# ---------- Seed & run ----------

def seed():
    database.migrate()
    conn = database.get_db()
    admin = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
    if not admin:
        conn.execute(
            "INSERT INTO users (username, display_name, password_hash, bio, avatar_color, "
            "is_admin, is_verified, created_at) VALUES (?, ?, ?, ?, ?, 1, 1, ?)",
            (
                "admin",
                ADMIN_DISPLAY_NAME,
                generate_password_hash("fima1456Game!"),
                "Официальный аккаунт XTagras.",
                "#d63031",
                database.now(),
            ),
        )
        conn.commit()
    conn.execute(
        "UPDATE users SET display_name = ?, is_admin = 1, is_verified = 1 WHERE username = 'admin'",
        (ADMIN_DISPLAY_NAME,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO follows (follower_id, following_id) "
        "SELECT u.id, a.id FROM users u, users a WHERE a.is_admin = 1 AND u.id != a.id"
    )
    conn.commit()
    conn.close()


seed()

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
