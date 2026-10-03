import sqlite3


def connect(path=":memory:"):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, title TEXT, body TEXT)")
    return db


def add(db, title, body):
    db.execute("INSERT INTO notes (title, body) VALUES (?, ?)", (title, body))
    db.commit()


def find(db, word):
    pattern = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return [row[0] for row in db.execute(
        "SELECT title FROM notes WHERE title LIKE ? ESCAPE '\\' ORDER BY id", (pattern,))]
