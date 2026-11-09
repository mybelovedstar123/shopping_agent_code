# Kyo Shop

[![CI](https://github.com/mybelovedstar123/shopping_agent_code/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mybelovedstar123/shopping_agent_code/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)](compose.yaml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A complete shopping application in which a team of independent AI agents can shop for you.
Browse and buy like in any online store, or describe what you want and let the agents search,
compare, ask you questions and fill a cart. **They cannot buy anything without your approval
of the exact cart and total, and they never see your card number.**

It runs entirely on your own machine with no accounts or API keys. Payments are simulated, and
no real money moves.

```bash
docker compose up --build        # then open http://127.0.0.1:8000
```

Or without Docker, on Python 3.11 or newer:

```bash
pip install -e .
python -m kyo                    # seeds 144 products on first run
```

If port 8000 is taken, set `KYO_PUBLISH_PORT` for Docker, or `KYO_PORT` without it. At checkout,
use the test card `4242 4242 4242 4242`. The card `4000 0000 0000 0002` is always declined.

## Contents

- [Using the assistant](#using-the-assistant)
- [The agent team](#the-agent-team)
- [How a purchase stays safe](#how-a-purchase-stays-safe)
- [How well it works](#how-well-it-works)
- [The store](#the-store)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [Development](#development)
- [Known limitations](#known-limitations)
- [More documents](#more-documents)

## Using the assistant

Log in, open **Assistant**, and describe what you want:

> Buy the cheapest red mug with a capacity of at least 300 ml, under $20.

The page shows each agent's progress as it works. The team may ask about things you did not
mention, such as a mug's material. When it has a cart, it shows you the exact items and total,
and buys only if you approve and pay. If nothing fits, it says so and buys nothing. The **Full
timeline** link shows every step and tool a run used.

Without a model, requests are read by rules that understand simple wording: products, quantities,
colours, materials, measurements, budgets, "cheapest" and "largest". Words it does not recognise
count as requirements, so an unusual request is more likely to end with "nothing fits" than with
a wrong purchase. To give the agents a model's judgement, see [Configuration](#configuration).

## The agent team

Agents share no memory. Each has its own mailbox and worker, and they exchange only JSON messages,
so any agent can also run in its own process, and one agent failing does not stop the others.

| Agent | Job | With a model |
|---|---|---|
| `coordinator` | Runs the workflow by asking the others for results | |
| `request` | Turns the shopper's words into a structured brief | Reads the request |
| `gateway` | The only door to the store: counts steps and validates arguments | |
| `catalog` | Searches, pages, opens and remembers listings, learning the store's format | Reads unusual result layouts |
| `interview` | Asks about preferences the request left unstated | Plans questions, reads answers |
| `selection` | Chooses the listing for each item, and says why others failed | |
| `cart` | The only agent that changes the cart, and notices when it drifts | |
| `review` | Independently judges carts before checkout and declines before they end | Gives a second opinion |
| `process` | Carries out steps the request demands, such as approval | Decides what each step needs |
| `notes` | Reads free text in store replies for changed budgets, requirements and stock | Reads notes |

A model is always optional, and each agent spends from its own call allowance within a team limit.
If a model fails or runs out, the agent falls back to its rules. The review agent reports what
became of the model's opinion: `passed`, `failed`, `unavailable` or `none`.

The team can also be used on its own, against any store with tools:

```python
from kyo.scenarios.store import Product, ScenarioStore
from kyo.team import run_team

store = ScenarioStore([Product("L1", "Desk lamp", "Desk Lamps", 20.0, {"colour": "red"})])
result = run_team("Buy a red desk lamp.", store)
print(result["outcome"], result["lines"], result["total"])  # purchased {'L1': 1} 20.0
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## How a purchase stays safe

- **Approval places the order.** The agents' `checkout` tool only asks. The shopper's own approval
  request charges the card and creates the order, and it fails if the cart's total has changed.
- **Agents never see card numbers.** Anything shaped like a card number is also scrubbed from
  stored requests, answers and logs, and only the last four digits of a payment are kept.
- **Each run has its own cart,** so an agent can never buy what is in the shopper's own cart.
- **Checkout is atomic.** The charge, stock reduction and order happen together or not at all.
  Concurrent checkouts cannot oversell, and a repeated request cannot buy twice.
- **The review agent re-checks everything** from the listing records before checkout, trusting no
  other agent's conclusion.
- **It declines rather than guess.** A requirement it cannot check ends without a purchase.
- **Web protections:** scrypt password hashes, revocable sessions, CSRF tokens, a content security
  policy, and login throttling.

## How well it works

`python -m kyo.evaluate` runs the team through 36 shopping scenarios with no model. Currently it
passes all 30 core scenarios and 3 of 6 harder ones, and makes **no wrong purchases**. The 3
failures are safe declines. A wrong purchase in any scenario fails the test suite.

These scenarios were written alongside the code and show that the workflow behaves as designed,
not how well it shops in general. No live model has been evaluated. See
[docs/EVALUATION.md](docs/EVALUATION.md) for the results and what they do not prove.

## The store

Accounts, a catalog of 144 sample products in 9 categories with search and filters, carts, orders
and simulated payments, all available in the browser and through a JSON API.

| Endpoint | Purpose |
|---|---|
| `GET /api/products`, `/api/products/{sku}`, `/api/categories` | Search and read the catalog |
| `POST /api/register`, `/api/login`, `/api/logout` | Accounts |
| `GET /api/cart`, `PUT` and `DELETE /api/cart/{sku}` | The shopper's cart |
| `POST /api/checkout`, `GET /api/orders` | Buying, with an optional `Idempotency-Key` header |
| `POST /api/runs`, `GET /api/runs/{id}` | Start and follow an assistant run |
| `POST /api/runs/{id}/questions/{q}/answer` and `/decision` | Answer it, and approve or refuse |

Interactive API documentation is at `/docs`.

## Configuration

Everything is set through environment variables.

| Setting | Default | Meaning |
|---|---|---|
| `KYO_DATABASE_URL` | Local SQLite file | Where the store keeps its data |
| `KYO_HOST`, `KYO_PORT` | `127.0.0.1`, `8000` | Where `python -m kyo` listens |
| `KYO_PUBLISH_PORT` | `8000` | The port `docker compose` publishes on this machine |
| `KYO_SEED` | `1` | Set to `0` to skip adding the sample catalog |
| `KYO_SECURE_COOKIES` | `0` | Set to `1` when serving over HTTPS |
| `KYO_LOG_LEVEL` | `INFO` | Level of the JSON logs written to stderr |
| `KYO_MODEL_BASE_URL`, `KYO_MODEL_NAME`, `KYO_MODEL_API_KEY` | Unset | An OpenAI-compatible model for the agents. Without one, they use rules |

## Project layout

| Path | Contents |
|---|---|
| `kyo/runtime/` | Messages, the bus, the trace, and running an agent in its own process |
| `kyo/models/` | The model interface, budgets, the HTTP client and a scripted test model |
| `kyo/agents/` | The agents |
| `kyo/team.py` | Assembles a team and runs a request |
| `kyo/fit.py`, `kyo/schema.py`, `kyo/compat.py` | Requirement rules, tool-schema checks and part compatibility |
| `kyo/store/` | Database, services, the agents' tools and background runs |
| `kyo/api/`, `kyo/web/` | The JSON API and the shopper-facing pages |
| `kyo/scenarios/`, `kyo/evaluate.py` | The scenario store and the evaluation |
| `agent.py` | The original single agent, unchanged |

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
python -m kyo.evaluate --out docs/EVALUATION.md    # a test checks this report is up to date
```

CI runs lint and tests on every pull request and on pushes to `main`, and builds the Docker image
and checks that it answers. `scripts/browser_check.py` walks through the application in a real
browser and saves screenshots. It needs Selenium and Chrome, and is run by hand.

## Known limitations

- **No live model has been tested.** The model paths have only been run with scripted replies.
- **The rule parser handles simple wording only.** A request that describes a use rather than a
  product ("something to drink my coffee from") ends with nothing bought.
- **Requirements pointing at another listing** ("the same colour as the Harbor mug") are declined,
  not followed. `agent.py` can follow them, and the team cannot yet.
- **Part compatibility** ("a battery that fits it") needs a model to read the request.
- **The team has not been compared with `agent.py`,** which needs a live model and its original
  test environment.
- **Runs do not survive a restart.** They run on threads in the web process and are marked as
  failed when the server starts again.
- **A demonstration store.** Payments are simulated, emails are not verified, and there is no
  password reset, returns process or administration page. Before any real use, serve it over HTTPS
  with `KYO_SECURE_COOKIES=1`, and run one web process: the login throttle lives in memory.
- **SQLite by default.** The code avoids SQLite-only features, but it has only been run on SQLite.

## More documents

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the design, and what the team does not do yet
- [docs/EVALUATION.md](docs/EVALUATION.md): evaluation results and their limits
- [docs/ROADMAP.md](docs/ROADMAP.md): how the project was scheduled
- [docs/BASELINE_AGENT.md](docs/BASELINE_AGENT.md): the original single agent in `agent.py`
- [CHANGELOG.md](CHANGELOG.md): what changed
