# Changelog

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
