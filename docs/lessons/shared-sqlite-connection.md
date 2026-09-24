# Lesson: one database connection, many threads

*2026-09-24. Found as "Uncaught app execution" errors while creating a flavor.*

## The rule

**A connection belongs to one unit of work. Never share it between threads that
can run at the same time.**

## What happened

The app cached a single SQLite connection with `@st.cache_resource` and gave it
to every page run. Streamlit runs each rerun on its own thread, and reruns
overlap: clicking "Add flavor" starts a new run while the old one is still
querying. Python's `sqlite3` keeps a cache of prepared statements, keyed by SQL
text. Two threads running the same query reset each other's statement, so one
of them read an empty result:

```
SELECT COUNT(*) FROM staging_inventory_raw   -- always returns one row
fetchone()  ->  None                          -- "impossible", so: a race
```

The app looked fine because Streamlit throws away the output of a run a newer
run has replaced. The only sign was an error in the log.

## Why it matters beyond this crash

The same race can fail **silently**. If two runs call `get_lines(conn, 7)` and
`get_lines(conn, 9)` together, one of them can get the other flavor's lines back
with no error at all. A crash you can see beats wrong data you can't.

## The fix

One connection per page run, and the schema applied once:

```python
@st.cache_resource
def _apply_schema(db_path): init_db(db_path).close()   # cached: runs once

def get_conn(db_path):                                  # not cached: one per run
    _apply_schema(db_path)
    return connect(db_path, check_same_thread=False)
```

`tests/test_app_smoke.py::test_overlapping_runs_do_not_share_a_connection` fails
on the old code (6 threads, hundreds of errors) and passes on the new.

## How to recognise it next time

- **An error that "can't happen"** (a `COUNT(*)` with no row, a `None` from a
  query that always returns something) means shared state is being hit from
  two places at once. Suspect concurrency before suspecting the query.
- **Errors that come and go**, or only show up when you click fast, point the
  same way.
- **`check_same_thread=False` silences a warning; it doesn't make sharing
  safe.** It only says "I promise I'm handling threads myself." Check that the
  code actually is.
- **`@st.cache_resource` hands one object to every run and every user.** Only
  cache things that are safe to share: immutable data, or objects built for
  concurrent use (a connection pool, an ML model). A raw DB connection is
  neither.

## Where else this shows up

The same shape of bug appears whenever something with internal state is shared
across concurrent callers:
- a global `requests.Session` or DB cursor in a web server;
- a module-level list or dict that request handlers append to;
- one file handle written by several threads.

The fix is the same each time: give each unit of work its own copy, or put a
lock around the shared one.

## Check yourself

1. Why did the app keep working even though a run crashed?
2. Why is the silent version of this bug worse than the crash?
3. What does `check_same_thread=False` actually promise?
4. Name one thing that *is* safe to put in `@st.cache_resource`, and one that isn't.

<details><summary>Answers</summary>

1. The crashed run had already been replaced by a newer rerun, so Streamlit
   threw its output away. The error only reached the log.
2. A crash tells you something is wrong. Wrong rows (one flavor's lines shown
   under another) look normal and get trusted.
3. Nothing about safety. It turns off a guard, and you promise to keep threads
   from using the connection at the same time.
4. Safe: the result of applying the schema once, a loaded ML model, a
   connection pool. Not safe: a bare `sqlite3` connection or cursor, a mutable
   list that runs append to.

</details>
