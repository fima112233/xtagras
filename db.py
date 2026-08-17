import os
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("DATABASE_PATH") or (Path(__file__).parent / "social.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT DEFAULT '',
    bio TEXT DEFAULT '',
    avatar_color TEXT DEFAULT '#6c5ce7',
    avatar_path TEXT DEFAULT '',
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_verified INTEGER NOT NULL DEFAULT 0,
    is_banned INTEGER NOT NULL DEFAULT 0,
    allow_dms INTEGER NOT NULL DEFAULT 1,
    dark_mode INTEGER NOT NULL DEFAULT 0,
    pinned_post_id INTEGER,
    last_seen TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    privacy TEXT NOT NULL DEFAULT 'public',
    created_at TEXT NOT NULL,
    edited_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    parent_id INTEGER,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES comments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS likes (
    user_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    reaction TEXT NOT NULL DEFAULT '❤️',
    PRIMARY KEY (user_id, post_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS follows (
    follower_id INTEGER NOT NULL,
    following_id INTEGER NOT NULL,
    PRIMARY KEY (follower_id, following_id),
    FOREIGN KEY (follower_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (following_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (recipient_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reposts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, post_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bookmarks (
    user_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, post_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS blocks (
    blocker_id INTEGER NOT NULL,
    blocked_id INTEGER NOT NULL,
    PRIMARY KEY (blocker_id, blocked_id),
    FOREIGN KEY (blocker_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (blocked_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mutes (
    muter_id INTEGER NOT NULL,
    muted_id INTEGER NOT NULL,
    PRIMARY KEY (muter_id, muted_id),
    FOREIGN KEY (muter_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (muted_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    actor_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    post_id INTEGER,
    created_at TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (actor_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    reason TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    FOREIGN KEY (reporter_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS profile_views (
    viewed_id INTEGER NOT NULL,
    viewer_id INTEGER NOT NULL,
    viewed_at TEXT NOT NULL,
    PRIMARY KEY (viewed_id, viewer_id),
    FOREIGN KEY (viewed_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (viewer_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS post_views (
    post_id INTEGER NOT NULL,
    viewer_id INTEGER NOT NULL,
    viewed_at TEXT NOT NULL,
    PRIMARY KEY (post_id, viewer_id),
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY (viewer_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate():
    conn = get_db()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "users" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "email" in cols:
            rebuild_users_without_email(conn)
        else:
            if "display_name" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT DEFAULT ''")
            if "allow_dms" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN allow_dms INTEGER NOT NULL DEFAULT 1")
            if "dark_mode" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN dark_mode INTEGER NOT NULL DEFAULT 0")
            if "pinned_post_id" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN pinned_post_id INTEGER")
            if "last_seen" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN last_seen TEXT")
            if "avatar_path" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT DEFAULT ''")
    if "posts" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(posts)").fetchall()}
        if "privacy" not in cols:
            conn.execute("ALTER TABLE posts ADD COLUMN privacy TEXT NOT NULL DEFAULT 'public'")
    if "comments" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(comments)").fetchall()}
        if "parent_id" not in cols:
            conn.execute("ALTER TABLE comments ADD COLUMN parent_id INTEGER")
    if "likes" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(likes)").fetchall()}
        if "reaction" not in cols:
            conn.execute("ALTER TABLE likes ADD COLUMN reaction TEXT NOT NULL DEFAULT '❤️'")
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def rebuild_users_without_email(conn):
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE users_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            bio TEXT DEFAULT '',
            avatar_color TEXT DEFAULT '#6c5ce7',
            avatar_path TEXT DEFAULT '',
            is_admin INTEGER NOT NULL DEFAULT 0,
            is_verified INTEGER NOT NULL DEFAULT 0,
            is_banned INTEGER NOT NULL DEFAULT 0,
            allow_dms INTEGER NOT NULL DEFAULT 1,
            dark_mode INTEGER NOT NULL DEFAULT 0,
            pinned_post_id INTEGER,
            last_seen TEXT,
            created_at TEXT NOT NULL
        );

        INSERT INTO users_new (id, username, password_hash, display_name, bio,
            avatar_color, avatar_path, is_admin, is_verified, is_banned, allow_dms,
            dark_mode, pinned_post_id, last_seen, created_at)
        SELECT id, username, password_hash, display_name, bio,
            avatar_color, '', is_admin, is_verified, is_banned, allow_dms,
            dark_mode, pinned_post_id, last_seen, created_at
        FROM users;

        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
        """
    )
    conn.execute("PRAGMA foreign_keys = ON")


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
