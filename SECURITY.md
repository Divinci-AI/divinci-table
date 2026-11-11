# Security policy

divinci-table is a game server that friends (and AI players) connect to, sometimes through a tunnel. The properties
worth attacking are listed in [`docs/security.md`](docs/security.md): who may act for which seat, and what stays hidden
(another player's hand, the AI's hidden cards, a private reveal made to one seat).

## Reporting a vulnerability

Please report it **privately**: on this repository, **Security → Report a vulnerability** (GitHub private
vulnerability reporting). Don't open a public issue for something that lets one player act as another, read a hand
they shouldn't see, or reach the host's controls.

Useful in a report: the request that does it (without any real seat key or token), what you expected, and what
happened. A throwaway server on a free port reproduces nearly everything; `table/tests/remote_authority_test.py` and
`table/tests/security_review_test.py` show how to start one.

## Scope

In scope: the table server (`table/server.py` and the pages in `table/assets/`), the seat and brain APIs, the
cloud lobby (`cloud/`).

Out of scope: running a table with `STRICT_SEATS` off and exposing it to the internet (the default trusts the LAN,
and says so), a seat's honour-system play with its own physical cards, and attacks that need the host's own account.

Please don't test against a table you don't own: tables are people's game nights.

## What we do

We fix confirmed issues on `main` with a test that fails on the old code and passes on the new, and we write the
change up in `docs/security.md`, as the 2026-11-11 chat-authority fix there shows.
