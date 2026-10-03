import sqlite3


def connect(path=":memory:"):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, title TEXT, body TEXT)")
    return db


def add(db, title, body):
    db.execute(f"INSERT INTO notes (title, body) VALUES ('{title}', '{body}')")
    db.commit()


def find(db, word):
    return [row[0] for row in db.execute(
        f"SELECT title FROM notes WHERE title LIKE '%{word}%' ORDER BY id")]
