import sqlite3

from .config import settings


def _c():
    c = sqlite3.connect(settings.db_path)
    c.execute("create table if not exists watchlist(symbol text primary key)")
    return c


def items():
    with _c() as c:
        return [r[0] for r in c.execute("select symbol from watchlist order by symbol")]


def add(s):
    with _c() as c:
        c.execute("insert or ignore into watchlist values(?)", (s.upper(),))


def remove(s):
    with _c() as c:
        c.execute("delete from watchlist where symbol=?", (s.upper(),))
