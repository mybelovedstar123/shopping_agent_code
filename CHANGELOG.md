# Changelog

## Unreleased

### Changed

- **The original single agent (`agent.py`)** keeps each episode inside its time limit, recovers
  from exceptions, reads store results in any shape, searches for every item before its first
  turn and on its own before declining, matches parts to devices by series, asks its questions
  as lists, and fills a short cart before a forced order. See
  [docs/BASELINE_AGENT.md](docs/BASELINE_AGENT.md).
- **"Add to cart"** on a product page adds to the quantity already in the cart.
- **Cart lines** carry a warning when stock no longer covers them.

### Fixed

- **Store:** a checkout could charge more than the page showed; an approval in the last moment of
  waiting could be charged while the run reported nothing bought; simultaneous adds of one
  product failed; more than three runs could be active; a checkout key could stand in for an
  approval; some card numbers were not masked; replies could be sent before their changes were
  saved.
- **Agent team:** two items met by one listing were bought as one unit; a total budget was read
  as per item; a price rise could lead to buying over budget; the process agent could change the
  reviewed cart; budgets in answers were ignored without a model; malformed model replies and
  store errors ended the run; step limits were ignored; "largest" and "smallest" picks guessed
  when no listing stated the measure; "Yes, no problem" was read as a refusal; "$1,200" was read
  as $1.
- **Request wording:** a price after "at" or "for", a size word before the product, and an
  approval request in the product's sentence all broke the product's name; question limits
  written in words were ignored.
- **Docker:** `docker compose` now passes `KYO_SEED` and `KYO_LOG_LEVEL` to the application.

## 1.0.0

The repository started as one agent in one file. It is now a shopping application whose
assistant is a team of independent agents.

### Added

- **Agent runtime:** JSON messages, one mailbox and worker per agent, an event trace, model
  budgets per agent and per team, and the option to run any agent in its own process.
- **Agent team:** coordinator, request, gateway, catalog, interview, selection, cart, review,
  process and notes agents. The team works with no model, using rules, and a model adds
  judgement where one is configured.
- **Store:** catalog with search and filters, accounts, carts, orders with atomic checkout,
  and simulated payments.
- **Web application:** browsing, search, accounts, cart, checkout, order history, and an
  assistant page with live progress, questions and purchase approval.
- **Security:** hashed passwords, revocable sessions, CSRF tokens, security headers and login
  throttling. Agents never see card numbers, and a purchase needs the shopper's approval of
  the exact cart and total.
- **Operations:** JSON logs with run ids, a run timeline page, a Docker image and a scenario
  evaluation.

### Fixed before release

- Two browser tabs could buy one cart twice in the worst timing. Each shopper's checkouts now
  run one at a time.
- The rule parser could ignore words it did not recognise, or a budget written in words, and
  buy the wrong thing. They now count, and any wrong purchase in the evaluation fails the tests.
- A request that depends on another listing was bought without checking it. The team now
  declines these.

### Known limitations

See [Known limitations](README.md#known-limitations) in the README.
