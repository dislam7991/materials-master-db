# Contributing

This is a personal portfolio project — one person's tool for one company's
materials data, kept public so the work can be read. It isn't a library and
isn't looking for feature contributions.

That said, if you spot something wrong, an issue is welcome: a bug, a claim in
the README that doesn't hold, a piece of the design that would break on real
data. Being told the tool is dishonest about what it knows is the most useful
thing anyone could send.

If you do want to open a pull request, keep it small and read
[SPEC.md](SPEC.md) first — section 2 is the principles the code is held to and
section 6 lists what is deliberately not built, which is the usual reason a
change gets turned down.

## Running it

Quickstart is in the [README](README.md). The pipeline is stdlib-only, so
`python -m pytest` after `pip install -r requirements-dev.txt` is the whole
test setup. CI runs the generator, the ETL, the quality report and the suite on
every push.

The repo contains **synthetic data only**. Real sheet credentials live in a
gitignored `config.local.toml`; never commit a real material name, price,
supplier or client.

## The daily automation

Commits from the automation that advances SPEC.md land as pull requests like
any other change, and `docs/runs/` records what each run did. The rules it
follows are SPEC.md section 7.
