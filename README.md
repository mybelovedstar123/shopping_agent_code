# shopping_agent_code

**Kyo** is an LLM agent that shops for one customer in a tool-driven online store. It
gets the shopper's request and the store's tool list, and works through the episode
step by step: it searches, opens listings, asks the shopper questions, fills the cart,
and then checks out or ends without buying.

An episode scores 1 only when the finish is exactly right:

- **A purchase:** every requested item, at the requested quantity, meeting every
  requirement in force at checkout, within budget.
- **No purchase:** when nothing in the store fits, the episode must end with the
  no-purchase tool, in the way the request asks.

Most of the code exists to make that final call reliable under a limited step budget.

## At a glance

| | |
|---|---|
| Entry point | `agent_main(problem_data) -> list[dict]` (returns the episode trace) |
| Language | Python 3.9+ (single module, `agent.py`, ~3k lines) |
| Dependencies | Standard library, plus `src.agent.proxy_client.ProxyClient` from the host harness |
| Models | `anthropic/claude-haiku-5.5` (light), `anthropic/claude-sonnet-5.5` (heavy), `openai/gpt-6-luna` (spare) |
| Budget per episode | ≤ 42 agent-loop model calls, ≤ 60 model calls in total, spend ≤ $0.078 |

## Repository layout

| File | Purpose |
|---|---|
| `agent.py` | The whole agent: configuration, prompts, helpers, the `Item` model, the `Kyo` class and `agent_main` |

The module is ordered bottom-up: constants and prompts, then pure helpers, then `Kyo`.
The methods of `Kyo` are grouped by concern, in the same order as the sections below.

## Technology stack

- **Python standard library only:** `json`, `re`, `random`, `time`, `typing`.
- **`ProxyClient`** (`src.agent.proxy_client`, supplied by the harness, not part of
  this repo). It makes the agent's two HTTP calls:
  - `_llm` → `POST /inference/chat/completions`: OpenAI-style chat completions with
    tool calling. Timeout 50 s, 1 retry.
  - `_env` → `POST /environment/call`: one store action per call. Timeout 95 s,
    3 retries.
- **Anthropic prompt caching:** for `anthropic/*` models, the first and last messages
  are sent as `cache_control: ephemeral` blocks.

## How an episode runs

```
agent_main(problem)
 └─ Kyo(problem).run()
     ├─ read_request()     brief: items, store roles, budget, pick rule, process steps (BRIEF prompt)
     ├─ work()             main loop: the light model chooses tool calls, Kyo executes them
     │    ├─ interview()   asks the shopper about unstated preferences (PLAN / HEAR prompts)
     │    └─ run_calls()   guards every call; may hold a checkout and send the model back
     └─ finish()           fallback checkout or decline if the loop ends without a finish
```

### 1. Reading the request
`read_request` sends the request and the full tool list to the light model. It falls
back to the heavy model when the brief is incomplete, or when two items share one
store shelf. The brief contains:

- **Items:** name, shelf, quantity, firm requirements. Items on the same shelf with
  compatible requirements are merged.
- **Roles:** which store tool does `search`, `open`, `offer`, `compare`, `cart`,
  `cart_view`, `buy`, `refuse` and `ask`. When one tool both buys and declines, the
  model is asked for the arguments that select each mode.
- **Rules:** budget and its scope (total or per item), the pick rule (`cheapest` /
  `most` / `least` / any fit), the message allowance, a step limit, and any steps the
  request mandates before ordering or ending (`process`).
- **Cross-listing requirements:** requirements whose value lives on another listing
  (`lookups`), and part-to-device fit pairs (`fits`). `unmix` moves words that
  describe the *other* listing out of the product's own requirements.

### 2. Learning the store from what it returns
The agent has no fixed schema for the store. It infers one from the results:

- **Listing ids:** `_learn_id` picks the id key from the result rows. `absorb` walks
  every observation and keeps a row and, where present, a full attribute record
  (`_fact_block`) for each listing.
- **Category key:** `category_key` picks the row key whose values match the enum
  labels in the tool schemas.
- **Attribute table:** `key_table` lists attribute keys per category. Keys that
  carry the same values are merged (`twins`).
- **Result layout:** `read_shape` asks the light model, once per search tool, which
  keys hold the rows, the total count, the next-page token, the price and the stock.
  `more_pages` then reads up to `AUTO_PAGES` further pages itself when the pick rule
  needs every match.
- **Tool capabilities:** `facts_from` records which tools return attributes and
  which return bare rows. The `open` and `offer` roles are reassigned when the brief
  got them wrong.

### 3. Interviewing the shopper
Shoppers hold firm preferences they did not write down. `interview` runs when enough
listings are open, or a quarter of the steps are used. It:

1. Ranks the attribute keys each item's listings carry (`lane_keys`). Keys that are
   already settled, keys that are not properties (`NOT_PROPERTY`) and catch-all keys
   (`CATCH_ALL`) are skipped or asked last.
2. Has the heavy model choose question lanes (`PLAN`). Guard keys (`GUARD_*`) keep
   high-coverage attributes from being crowded out.
3. Asks one property per question, and re-asks with example values when an answer is
   vague. Answers are parsed into new, dropped and "don't mind" requirements (`HEAR`).
4. Searches on the newly learned values right away (`value_search`).

### 4. Guarded tool execution
`run_calls` runs each model tool call only after a series of checks. A call that fails
a check is answered "Not run (no step used)" with the reason, so a mistake costs no
step:

- **Arguments:** coerced to the tool's JSON schema (`_shape`: types, enums, bounds,
  required keys). Listing titles are resolved to ids (`resolve_ids`).
- **Avoiding wasted steps:** a repeated call, a cached read whose result has not
  changed, or a view that already showed everything is answered from memory. Several
  single opens are merged into one `compare` call (`join_opens`), trimmed to the
  store's side-by-side limit.
- **Protecting the ending:** the last steps are kept for checking out, and the
  available tools narrow as the steps run out (`offered`).

### 5. Checkout holds
When the model tries to **check out**, Kyo first verifies the cart, in order. It
returns the first problem it finds instead of buying:

1. **Wider search:** for cheapest-pick requests, a wider search is required while
   steps remain (`wider_hold`).
2. **Offers and stock:** carted offers are re-read for price or stock changes
   (`reread_offers`), and the cart view is refreshed.
3. **Sold-out lines:** replacements are proposed for them (`swap_hint`, `SWAP`).
4. **Requirement chains:** every listing a requirement points at must be opened,
   link by link (`trail`, `TRAIL`).
5. **Part fit:** each part must name the carted device's model code (`fit_faults`).
6. **Lines and quantities:** one line per item, the right quantity, no unrequested
   kinds, within budget (`line_faults`). Wrong quantities are fixed automatically
   (`fix_counts`).
7. **Judge review:** a final review of the cart records against the ledger (`CHECK`),
   comparing them with other listings seen.

When it tries to **end without buying**, the judge audits the listings seen for missed
fits (`audit`, `AUDIT`). If a candidate is confirmed, Kyo sets the cart and checks out
itself (`complete_cart`). It also holds a late decline when a complete cart has
already been built.

Before either ending, `gate` performs any steps the request mandates, such as
confirming the basket with the shopper. It re-checks them whenever the cart changes
(`GATE`).

### 6. Store notes
Free-text notes in observations, such as a budget change, a sold-out item or a
changed requirement, are classified by `take_note` (`NOTE`) and applied to the
requirements. The ledger version is bumped, which invalidates cached reads and
earlier verdicts.

### 7. Budgets and failure handling
- **Model choice:** the light model drives the loop, with the spare model as backup.
  The heavy model is used for judging only while spend stays under `SPEND_SOFT` and
  leaves `HEAVY_ROOM` before `SPEND_HARD` (`judges`).
- **Retries:** HTTP 402/408/425/429/5xx from the model are retried with jittered
  backoff (`WAITS`), capped at `WAIT_TOTAL` seconds per episode.
- **Credit and store errors:** a 402 ends model use for the episode. Store 409
  replies resynchronise the step counter (`expected N`), or mark the episode as
  finished.
- **History size:** the chat history is kept under `HISTORY_LIMIT` characters by
  shortening older tool results (`trim`).
- **Crashes:** `agent_main` catches exceptions, tries a final `finish()`, and always
  returns the trace.

## Interface

```python
from agent import agent_main

trace = agent_main({
    "environment": {
        "binding": {...},              # echoed into every /environment/call payload
        "policy_view": {
            "query": "...",            # the shopper's request
            "tools": [...],            # OpenAI-style function tools of the store
            "max_steps": 30,
            "opening": {...},          # optional first observation
            "finished": False,
        },
    },
})
```

Each store action is posted as `{**binding, "step": n, "action": {"name": ..., "args": {...}}}`.
The returned trace is a list of events (`brief`, `turn`, `check`, `audit`, `gate`,
`swap`, `end`, ...) that records what the agent decided and why.

## Configuration

All tuning lives in module constants at the top of `agent.py`:

| Constant | Value | Meaning |
|---|---|---|
| `LOOP_CALLS` / `ALL_CALLS` | 42 / 60 | Agent-loop model calls / all model calls per episode |
| `SPEND_SOFT` / `SPEND_HARD` / `HEAVY_ROOM` | 0.055 / 0.078 / 0.02 | Spend thresholds for heavy-model use and loop exit |
| `WAITS` / `WAIT_TOTAL` | (4, 10, 20, 35) / 45 | Backoff schedule and total wait budget (s) |
| `SHOW_CHARS` / `OBS_LIMIT` / `HISTORY_LIMIT` | 450 / 15000 / 150000 | String clip, observation and history size caps |
| `GATE_ROUNDS` | 3 | Rounds for performing request-mandated steps |
| `AUTO_PAGES` / `CHEAPER_OPENS` / `WIDEN_EACH` | 6 / 3 / 6 | Pages read automatically / cheaper listings opened / listings opened per item |
| `GUARD_KEYS` / `GUARD_SHARE` / `GUARD_GAP` | 3 / 0.0 / 0.34 | Interview guard keys |
| `FIT_CODE` / `PART_CODE` | regex | Model and part code patterns for fit checks |

## Known limitations

- **`ProxyClient` is external.** `agent.py` imports `src.agent.proxy_client`, which
  the harness provides. Running the agent outside the harness needs a client with
  `post_verbose(path, body)` that returns an object with `.data` and `.error`.
- **No tests in this repository.** The behaviour depends on live models and the
  store, so it is evaluated by running episodes in the harness.
- **Single module.** The agent is one ~3k-line file. The class is long, but its
  methods are grouped by concern as described above.
