# Roadmap

This project evolves a single shopping agent into a team of independent agents
inside a complete, self-contained shopping application.

The work runs for 100 calendar hours: 41 issues, one merge every 2.5 hours, from
Wed Nov 04, 20:00 CST to Mon Nov 09, 00:00 CST. The times below are scheduled merge times.
The work is prepared ahead and released at these times, so they do not measure
the hours spent on each issue. A release waits if its checks fail.

With version 1.0.0, every issue below is merged.

## How the schedule went

32 of the 41 issues merged within 3 minutes of their scheduled time. The machine
releasing them restarted unexpectedly three times, and nothing merged until it was running again.
The other 9 then merged late, oldest first:

| # | Scheduled merge | Merged | Late by | Issue |
|---|---|---|---|---|
| 4 | Thu Nov 05, 03:30 CST | Thu Nov 05, 14:01 CST | 10 h 32 min | Add characterization tests for the existing agent |
| 5 | Thu Nov 05, 06:00 CST | Thu Nov 05, 14:03 CST | 8 h 03 min | Document the target architecture and extract shared prompts |
| 6 | Thu Nov 05, 08:30 CST | Thu Nov 05, 14:04 CST | 5 h 34 min | Define the message and agent contracts |
| 7 | Thu Nov 05, 11:00 CST | Thu Nov 05, 14:04 CST | 3 h 05 min | Add a message bus with one mailbox and worker per agent |
| 8 | Thu Nov 05, 13:30 CST | Thu Nov 05, 14:05 CST | 36 min | Record every agent message in a shared event trace |
| 15 | Fri Nov 06, 07:00 CST | Fri Nov 06, 08:40 CST | 1 h 41 min | Add the catalog agent |
| 22 | Sat Nov 07, 00:30 CST | Sat Nov 07, 06:15 CST | 5 h 45 min | Add the store database schema |
| 23 | Sat Nov 07, 03:00 CST | Sat Nov 07, 06:15 CST | 3 h 16 min | Seed a realistic product catalog |
| 24 | Sat Nov 07, 05:30 CST | Sat Nov 07, 06:16 CST | 47 min | Add the catalog service and API |

The tables below keep the original schedule.

## M1 Foundations

Packaging, CI, tests around the existing agent, and the target architecture. Due Thu Nov 05, 06:00 CST.

| # | Scheduled merge | Issue |
|---|---|---|
| 1 | Wed Nov 04, 20:00 CST | Publish the project roadmap and timeline |
| 2 | Wed Nov 04, 22:30 CST | Create the kyo package and developer tooling |
| 3 | Thu Nov 05, 01:00 CST | Run lint and tests in CI on every pull request |
| 4 | Thu Nov 05, 03:30 CST | Add characterization tests for the existing agent |
| 5 | Thu Nov 05, 06:00 CST | Document the target architecture and extract shared prompts |

## M2 Agent runtime

Messages, mailboxes, tracing, budgets and model access for independent agents. Due Thu Nov 05, 21:00 CST.

| # | Scheduled merge | Issue |
|---|---|---|
| 6 | Thu Nov 05, 08:30 CST | Define the message and agent contracts |
| 7 | Thu Nov 05, 11:00 CST | Add a message bus with one mailbox and worker per agent |
| 8 | Thu Nov 05, 13:30 CST | Record every agent message in a shared event trace |
| 9 | Thu Nov 05, 16:00 CST | Add the model interface, budgets and a scripted test model |
| 10 | Thu Nov 05, 18:30 CST | Add an OpenAI-compatible model client with retries |
| 11 | Thu Nov 05, 21:00 CST | Allow any agent to run in its own process |

## M3 Independent agents

One agent per job, coordinated only by messages, shopping end to end. Due Fri Nov 06, 22:00 CST.

| # | Scheduled merge | Issue |
|---|---|---|
| 12 | Thu Nov 05, 23:30 CST | Add the shared domain model and tool-schema helpers |
| 13 | Fri Nov 06, 02:00 CST | Implement the requirement fit rules as tested code |
| 14 | Fri Nov 06, 04:30 CST | Add the request agent |
| 15 | Fri Nov 06, 07:00 CST | Add the catalog agent |
| 16 | Fri Nov 06, 09:30 CST | Add the interview agent |
| 17 | Fri Nov 06, 12:00 CST | Add the selection agent |
| 18 | Fri Nov 06, 14:30 CST | Add the cart agent |
| 19 | Fri Nov 06, 17:00 CST | Add the review agent |
| 20 | Fri Nov 06, 19:30 CST | Add the process and store-notes agents |
| 21 | Fri Nov 06, 22:00 CST | Add the coordinator and run the team end to end |

## M4 Store backend

Database, catalog, accounts, cart, orders, simulated payments and agent tools. Due Sat Nov 07, 18:00 CST.

| # | Scheduled merge | Issue |
|---|---|---|
| 22 | Sat Nov 07, 00:30 CST | Add the store database schema |
| 23 | Sat Nov 07, 03:00 CST | Seed a realistic product catalog |
| 24 | Sat Nov 07, 05:30 CST | Add the catalog service and API |
| 25 | Sat Nov 07, 08:00 CST | Add accounts and session authentication |
| 26 | Sat Nov 07, 10:30 CST | Add the cart service and API |
| 27 | Sat Nov 07, 13:00 CST | Add orders, stock control and simulated payments |
| 28 | Sat Nov 07, 15:30 CST | Expose the store to the agent team as tools |
| 29 | Sat Nov 07, 18:00 CST | Run agent teams in the background and persist their progress |

## M5 Web application

Shopper-facing pages, including the assistant that runs the agent team. Due Sun Nov 08, 09:00 CST.

| # | Scheduled merge | Issue |
|---|---|---|
| 30 | Sat Nov 07, 20:30 CST | Add the web layout and catalog pages |
| 31 | Sat Nov 07, 23:00 CST | Add product pages and search filters |
| 32 | Sun Nov 08, 01:30 CST | Add registration and login pages |
| 33 | Sun Nov 08, 04:00 CST | Add cart, checkout and order pages |
| 34 | Sun Nov 08, 06:30 CST | Add the assistant page with live progress |
| 35 | Sun Nov 08, 09:00 CST | Let shoppers answer agents and approve purchases in the browser |

## M6 Hardening and release

Security, reliability, observability, evaluation, packaging and version 1.0. Due Mon Nov 09, 00:00 CST.

| # | Scheduled merge | Issue |
|---|---|---|
| 36 | Sun Nov 08, 11:30 CST | Add CSRF protection, security headers and login rate limiting |
| 37 | Sun Nov 08, 14:00 CST | Prove and enforce checkout under concurrency and retries |
| 38 | Sun Nov 08, 16:30 CST | Add structured logs and a run inspection page |
| 39 | Sun Nov 08, 19:00 CST | Add a scenario evaluation and publish its results |
| 40 | Sun Nov 08, 21:30 CST | Package the application with Docker |
| 41 | Mon Nov 09, 00:00 CST | Release version 1.0 |

