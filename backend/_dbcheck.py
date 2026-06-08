import sqlite3
c = sqlite3.connect("astragate.db")
cur = c.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("TABLES:", tables)
for t in ["users", "providers", "models", "markup_config"]:
    if t in tables:
        n = cur.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
        print(f"{t}: {n}")
    else:
        print(f"{t}: MISSING")
