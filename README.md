# shopping_agent_code

**Kyo** is an LLM agent that shops for one customer in a tool-driven online store. It
receives the shopper's request and the store's tool list, then works through the
episode step by step: it searches, opens listings, asks the shopper questions, fills
the cart, and either checks out or ends without buying.

An episode scores 1 only when the finish is exactly right, and 0 otherwise:

- **A purchase** must contain every requested item at the requested quantity, each
  meeting every requirement in force at checkout, within budget.
- **No purchase** is correct only when nothing in the store fits, and the episode must
  then end with the no-purchase tool in the way the request asks.

The store is not known in advance: tool names, arguments and result shapes differ
between stores. Steps, model calls and spend are all limited. Most of the code exists
to make the final decision reliable under those conditions.

## Project status

This repository is evolving from the single agent described below into a team of
independent agents inside a complete, self-contained shopping application. See the
[roadmap](docs/ROADMAP.md) for the plan and schedule, and the
[architecture](docs/ARCHITECTURE.md) for the design.

The agent team now shops end to end. The store and web application are still to come, so
most of this README still describes `agent.py`, the single agent the team grew from.

## The agent team

```python
from kyo.scenarios.store import Product, ScenarioStore
from kyo.team import run_team

store = ScenarioStore([Product("L1", "Desk lamp", "Desk Lamps", 20.0, {"colour": "red"})])
result = run_team("Buy a red desk lamp.", store)
print(result["outcome"], result["lines"], result["total"])  # purchased {'L1': 1} 20.0
```

| Agent | Job |
|---|---|
| `coordinator` | Runs the workflow by asking the others for results |
| `request` | Turns the shopper's words into a structured brief |
| `gateway` | The only door to the store: counts steps and validates arguments |
| `catalog` | Searches, pages, opens and remembers listings |
| `interview` | Asks about preferences the request left unstated |
| `selection` | Chooses the listing for each item, and says why others failed |
| `cart` | The only agent that changes the cart, and notices when it drifts |
| `review` | Independently judges carts before checkout and declines before they end |
| `process` | Carries out steps the request demands, such as approval |
| `notes` | Reads free text in store replies for changes to the task |

Each agent has its own mailbox and thread, and agents share no memory. Everything they
exchange is a JSON message, so any agent can also run in its own process. The team works
with no model at all, using rules. Set `KYO_MODEL_BASE_URL`, `KYO_MODEL_NAME` and
`KYO_MODEL_API_KEY`, then pass `model_from_env()` to `run_team`, to add a model's judgement.

## At a glance

| | |
|---|---|
| Entry point | `agent_main(problem_data) -> list[dict]`, which returns the episode trace |
| Code | One module, `agent.py`: about 3,000 lines, with 119 methods on the `Kyo` class |
| Runtime | Python standard library only, plus a `ProxyClient` supplied by the host harness |
| Python | Run on 3.12. Annotations are deferred, so newer type syntax is not evaluated at runtime |
| Models | `anthropic/claude-haiku-5.5` (light), `anthropic/claude-sonnet-5.5` (heavy), `openai/gpt-6-luna` (spare) |
| Default limits | 30 steps unless the store sets `max_steps`; 42 loop calls and 60 model calls; loop stops at 0.078 spend |

## Contents

- [Project status](#project-status)
- [The agent team](#the-agent-team)
- [Architecture](#architecture)
- [How an episode runs](#how-an-episode-runs)
- [Prompts and models](#prompts-and-models)
- [Limits and failure handling](#limits-and-failure-handling)
- [Integration](#integration)
- [Running locally](#running-locally)
- [Configuration](#configuration)
- [Code map](#code-map)
- [Development](#development)
- [Known limitations](#known-limitations)

## Architecture

```
agent_main(problem)                     catches every exception and always returns the trace
 └─ Kyo(problem).run()
     ├─ read_request()                  request + tool list -> structured brief
     ├─ work()                          main loop: a model chooses tool calls
     │    ├─ interview()                asks the shopper about unstated preferences
     │    └─ run_calls()                checks and executes each call; can hold an ending
     └─ finish()                        fallback checkout or decline if the loop ends unfinished
```

Three design decisions shape the code:

**The model proposes, Kyo disposes.** The conversational model chooses tool calls, but
every call passes through `run_calls`. A call that is malformed, wasteful or premature
is answered with `Not run (no step used): <reason>`, so a mistake costs no store step
and the model gets a reason it can act on.

**Everything about the store is inferred.** Tool roles come from the brief. Listing id
keys, the category key, attribute tools and pagination are learned from real results.
If a role turns out to be wrong, Kyo reassigns it while running.

**Requirements live in one ledger.** Each `Item` holds its `musts` (from the request),
`told` (from the shopper or store notes), `dropped` and `free` lists. `ledger()` renders
them into the status text appended to the last tool result of each turn. A `version`
counter increases when requirements or store facts change, which invalidates cached
reads and earlier judge verdicts.

## How an episode runs

### 1. Reading the request

`read_request` sends the request and the tool list to the light model (`BRIEF`). It
retries on the heavy model when the brief lacks items or a buy role, or when two items
share one store shelf. The brief provides:

- **Items:** name, store shelf, quantity and firm requirements written as
  `property: constraint`. Items on the same shelf are merged unless their requirements
  conflict.
- **Roles:** which tool does `search`, `open`, `offer`, `compare`, `ask`, `cart`,
  `cart_view`, `buy` and `refuse`. If one tool both buys and declines, a further prompt
  finds the arguments that select each mode.
- **Rules:** budget, unit and scope (total or per item), pick rule (`cheapest`, `most`,
  `least` or `any`), message and step limits, wishes, and whether to decline quietly.
- **Process steps:** actions the request demands besides buying, such as getting
  approval. Each is due before choosing, before ordering, or before ending.
- **Cross-listing requirements:** requirements whose value lives on another listing
  (`lookups`) and part-to-device pairs (`fits`). `unmix` removes words that describe the
  other listing from the product's own requirements.

If the request does not state a message limit but an `ask` tool exists, the limit is 3.
A stated step limit lowers `max_steps` when it is at least 10.

### 2. Learning the store

| Fact | How it is learned |
|---|---|
| Listing id key | `_learn_id` scores row keys: unique, id-like values, preferring keys named `id` |
| Listing records | `absorb` walks every observation and keeps a row per listing, plus a full record once an attribute block appears (`_fact_block`) |
| Category key | `category_key` prefers row keys whose values match enum labels in the tool schemas |
| Attribute table | `key_table` lists keys per category and merges keys carrying the same values (`twins`) |
| Result layout | `read_shape` asks the model once per search tool for the keys holding rows, total, next page, price and stock (`SHAPE`) |
| Which tool shows attributes | `facts_from` records this per tool. `open` and `offer` are reassigned when a tool's results contradict the brief |

Search paging is automatic only for `cheapest`, `most` and `least` requests. `more_pages`
reads at most two further pages per search and `AUTO_PAGES` per episode, and it skips
paging when a cheapest-pick result is already sorted by price. For anything larger it
tells the model to narrow the search or page on.

### 3. Interviewing the shopper

Shoppers hold firm preferences they did not write down, and a question reveals one only
if it names that exact property. The interview runs once per episode, when both of these
hold:

- messages are free after reserving any needed by process steps, and more than 8 steps
  remain;
- enough is known (`3 × items + 1` listings opened, or `max(8, max_steps // 4)` steps
  used), or the model tries to touch the cart or end the episode first.

The interview then:

1. Opens more candidates so attribute keys are visible (`widen`, `widen_singly`).
2. Builds a ranked question list per item (`plan_lanes`). The heavy model ranks what the
   request asks to check, then properties hinted at by the story, then listed keys
   (`PLAN`). High-coverage keys can displace weak picks from the top three (`GUARD_*`).
   Identifier-like keys (`NOT_PROPERTY`) are excluded, and catch-all keys (`CATCH_ALL`)
   go last.
3. Asks one property per message, favouring items with no preference found yet.
4. Parses each reply into new, dropped and "don't mind" requirements (`HEAR`). If the
   shopper complains that a question asked too much, it asks again with a narrower label
   or with example values.
5. Searches on newly learned values and opens the first fitting rows (`value_search`).

The conversational model never messages the shopper: the `ask` tool is hidden from it,
and such a call is answered as not run.

### 4. Guarded tool execution

`run_calls` applies these checks before spending a step:

| Check | Effect |
|---|---|
| Unknown tool, tool not offered, or invalid JSON | Rejected with the reason |
| Schema mismatch | `_shape` coerces types and enum case and truncates long strings; remaining faults reject the call |
| Listing title used as an id | `resolve_ids` converts a unique match, or asks for one id |
| Sold-out listing opened again, or removing a line not in the cart | Rejected |
| Ending call sent alongside other calls | Rejected: it must go alone |
| Same call just made, cached read unchanged, or view already fully shown | Answered from memory |
| Several single opens in one turn | Merged into one `compare` call (`join_opens`) |
| Side-by-side call over the store's limit | Over a schema `maxItems`: rejected. Over a limit learned from an empty result: trimmed |

`offered` narrows the tools as steps run out, and `tail` reserves the closing steps
(one, or two when a message to the shopper is due at the order).

### 5. Ending holds

An ending call can be held so the agent can fix something. Each hold has a limit, so
the agent cannot loop forever, and checkout holds apply only while more than 2 steps
remain.

**Before checkout**, in this order:

| # | Check | Limit |
|---|---|---|
| 1 | `wider_hold`: run more searches for a cheaper fit (cheapest pick only) | Once, until store notes reset it; needs `14 + 2 × items` steps left |
| 2 | `reread_offers`: re-read carted listings for price or stock changes, and refresh the cart view | When stale. A change in the cart view holds; so do re-read changes once past the soft spend limit |
| 3 | `swap_hint`: sold-out lines, with replacements proposed by a judge (`SWAP`) | Each lost line once, then 2 reminders |
| 4 | `trail`: open every listing a requirement points at, link by link (`TRAIL`) | 3 holds, or 2 with 15 steps or fewer left |
| 5 | `fit_faults`: each part must name the carted device's model code | 2 holds |
| 6 | `line_faults`: one line per item, right quantity, no unrequested kinds, within budget. `fix_counts` repairs quantities itself | 2 holds |
| 7 | `check_cart`: judge review of the records against the ledger (`CHECK`), after reading missing attributes and cheaper same-kind rows | Once per cart and ledger version |

**Before ending without a purchase:**

| Check | Effect |
|---|---|
| `audit` (`AUDIT`) | A judge looks for missed fits once, and may open promising rows first |
| Leads found | The first decline is held. On the second, a judge checks the top lead and, if it passes, tells the model to buy it |
| `complete_cart` | If the model declines again after that, Kyo sets the cart and checks out itself |
| Complete cart already built, 5 steps or fewer left | Held once: running out of steps does not prove nothing fits |
| No leads and over a third of the steps left | Held once, with suggestions for other ways to search |
| `quiet` | Rewrites the decline message so it names no listing |

**Before either ending**, `gate` performs the process steps that are due, such as asking
for approval, and records proof (`GATE`). A step about the cart must have happened after
the cart last changed. A reply that changes requirements stops the ending. The result is
remembered per cart and ledger version.

### 6. Store notes

Free text in any observation can change the task. `loose_lines` collects unseen text,
and `take_note` classifies it (`NOTE`) and applies budget changes, added or dropped
requirements and sold-out listings. If a checkout is refused without naming the sold-out
line, `find_sold` works out which one by re-reading the cart's offers.

### What counts as a fit

These rules are stated in `GUIDE` and repeated in `CHECK` and `READING`, so the buyer and
the judges use the same standard:

- The listing's category label decides the kind of product.
- An attribute whose key names the required property decides the requirement. A key for
  a related but different property, such as a handle colour for a body colour, decides
  nothing.
- Values match on their words: case, plurals and plain synonyms are fine, and "deep teal"
  meets teal. Units are converted, limits include the edge, and ranges must lie wholly
  inside a limit.
- If no attribute names the property, the value stated elsewhere in the listing counts,
  but a bare figure or a number of another kind does not.
- `cheapest`, `most` and `least` picks compare the whole fitting set. Otherwise any fit
  is right. Wishes only break ties.

## Prompts and models

Prompts are module constants, apart from one short inline prompt in `read_request` that
finds the buy and decline arguments of a shared tool. All model calls use
`temperature: 0`. Models are listed in the order tried. `ask_json` moves to the next one
when the reply is not a JSON object.

| Prompt | Used by | Purpose | Models | Max tokens |
|---|---|---|---|---|
| `GUIDE` | `work` | System prompt for the main loop | light, spare | 3000 |
| `BRIEF` | `read_request` | Request and tools to structured brief | light; heavy on retry | 4000 / 2500 |
| `UNMIX` | `unmix` | Separate words describing another listing | heavy, light | 1500 |
| `SHAPE` | `read_shape` | Name a result's layout keys | light, heavy | 2500 |
| `PLAN` | `plan_lanes` | Choose interview questions | heavy, light | 3500 |
| `HEAR` | `hear` | Read answers into requirements | light, heavy in the interview | 2500 |
| `NOTE` | `take_note` | Classify store notes | light, spare | 2500 |
| `GATE` | `gate` | Check process steps, choose the next call | light, heavy; heavy first after a nudge | 3500 |
| `TRAIL` | `trail` | Follow requirement chains | light, then heavy if budget allows | 4500 |
| `CHECK` | `check_cart` | Final cart review | `judges()` | 3000 |
| `AUDIT` | `audit_call` | Look for missed fits before declining | `judges()` | 4000 |
| `SWAP` | `replacements` | Find replacements for sold-out lines | `judges()` | 3500 |

`READING` is not sent alone: it is the shared rule text inside `AUDIT` and `SWAP`.

`judges()` returns heavy then light while spend is within the soft limit and leaves
`HEAVY_ROOM` before the hard limit, then light alone up to the hard limit, then nothing.
With no judge available, the review is skipped.

For `anthropic/*` models, when a request has more than one message, the first and last
are sent as `cache_control: ephemeral` blocks.

## Limits and failure handling

The limits are working thresholds, not strict ceilings:

- **Spend** is checked between calls, so one call can pass a threshold. Spend is the sum
  of `usage.cost` from the proxy, in whatever unit the proxy reports.
- **`LOOP_CALLS`** bounds the first pass. If the fallback ending is refused, the loop gets
  10 more calls and 0.01 more spend to apply what the store said.
- **`ALL_CALLS`** counts one per model attempted. HTTP retries of that attempt are not
  counted.

| Failure | Handling |
|---|---|
| Model: no response or 408 | One retry, then the next model |
| Model: 402, 425, 429, 5xx | Up to 4 retries with jittered backoff (`WAITS`), within `WAIT_TOTAL` seconds per episode |
| Model: 402 after retries | Sets `no_credit`, which stops the main loop and leads to the fallback ending |
| Model: other errors or an empty reply | Next model |
| Store: 409 mentioning finish, quarantine or finalize | Episode marked finished |
| Store: 409 with `expected N` | Step counter resynchronised and the call retried |
| Store: 409 mentioning reuse or a different action | Step counter advanced and the call retried |
| Store: 400, 401, 403, 404, 422 | The call fails and the model is told it may try again |
| Store: 402 | Sets `broke`, which stops the loop with no fallback ending |
| Other store errors | Up to 4 attempts per action |
| Exception in `run` | `agent_main` records a `crash` event, tries `finish()`, and returns the trace |

When the loop ends unfinished, `finish` checks out if the cart holds anything, and
otherwise declines. The chat history stays under `HISTORY_LIMIT` characters because
`trim` shortens older tool results and keeps the listing ids they mention.

## Integration

### Input

```python
from agent import agent_main

trace = agent_main({
    "environment": {
        "binding": {...},            # copied into every store call
        "policy_view": {
            "query": "...",          # the shopper's request
            "tools": [...],          # OpenAI-style function tools
            "max_steps": 30,         # optional; 30 when absent or invalid
            "opening": {...},        # optional first observation
            "finished": False,       # optional; True returns immediately
        },
    },
})
```

### Client contract

`agent.py` imports `ProxyClient` from `src.agent.proxy_client` and creates two clients at
import time:

| Client | Settings | Call |
|---|---|---|
| `_llm` | `timeout=50, max_retries=1, retry_delay=1` | `POST /inference/chat/completions` |
| `_env` | `timeout=95, max_retries=3, retry_delay=1` | `POST /environment/call` |

`post_verbose(path, body)` must return an object with two attributes:

- `.data`: the parsed JSON response, or `None`.
- `.error`: `None`, or a dict with `status` and `body`.

### Store protocol

```jsonc
// request
{ ...binding, "step": 5, "action": { "name": "search", "args": { "q": "red lamp" } } }

// response
{ "step": 5, "finished": false, "observation": { /* any JSON */ } }
```

The step sent is always the current step plus one. `step` in the response is optional.

### Model protocol

OpenAI-style chat completions with `tools` and `tool_choice: "auto"`. Kyo reads
`choices[0].message` and `usage.cost`. An error can also arrive in the response body as
`{"error": {"code": ..., "message": ...}}`.

### Trace events

The return value is a list of dicts, each with a `kind`:

| Group | Kinds |
|---|---|
| Lifecycle | `brief`, `turn`, `end`, `crash` |
| Understanding the request | `items_merged`, `unmix`, `shape` |
| Interview | `plan`, `ask`, `value_search` |
| Reading listings | `widen`, `fill_facts`, `pages`, `joined`, `reread`, `trail`, `link_search`, `link_known` |
| Checks | `check`, `gate`, `gate_unproven`, `fit_hold`, `line_hold`, `count_fix`, `swap` |
| Declining | `audit`, `audit_open`, `refuse_blocked`, `late_decline_held`, `completed` |
| Fallback endings | `forced_buy`, `forced_refuse` |
| Service failures | `llm_fail`, `env_fail` |

The `end` event records `finished`, `steps`, `cost` and `calls`.

## Running locally

The harness is not in this repository, so running outside it needs a stand-in client.
This one fakes a store where the model always declines. It needs no network or API key.

Create `stub/src/agent/proxy_client.py` outside the repository:

```python
import json
from types import SimpleNamespace as Reply

BRIEF = {"items": [{"name": "lamp", "quantity": 1, "musts": ["colour: red"]}],
         "roles": {"search": "find", "buy": "buy", "refuse": "decline"}}

class ProxyClient:
    def __init__(self, **settings):
        pass

    def post_verbose(self, path, body):
        if path == "/environment/call":
            done = body["action"]["name"] in ("buy", "decline")
            return Reply(data={"step": body["step"], "finished": done, "observation": {}}, error=None)
        if "tools" in body:
            call = {"id": "c1", "function": {"name": "decline", "arguments": "{}"}}
            message = {"tool_calls": [call]}
        else:
            message = {"content": json.dumps(BRIEF)}
        return Reply(data={"choices": [{"message": message}], "usage": {"cost": 0.001}}, error=None)
```

Then create `smoke.py` next to the `stub` folder:

```python
from agent import agent_main

tool = lambda name: {"type": "function", "function": {
    "name": name, "parameters": {"type": "object", "properties": {}}}}

trace = agent_main({"environment": {"binding": {"episode": "demo"}, "policy_view": {
    "query": "Buy a red lamp.", "tools": [tool("find"), tool("buy"), tool("decline")]}}})
print([event["kind"] for event in trace])
print(trace[-1])
```

Run it with both folders on the path:

```bash
# Windows: use ; instead of : between the paths
PYTHONPATH="stub:/path/to/shopping_agent_code" python smoke.py
```

Expected output:

```
['brief', 'turn', 'turn', 'end']
{'kind': 'end', 'finished': True, 'steps': 1, 'cost': 0.003, 'calls': 3}
```

There are two `turn` events because the first decline is held: most of the steps are
still unused and no search has been tried.

## Configuration

All tuning lives in constants at the top of `agent.py`:

| Constant | Value | Meaning |
|---|---|---|
| `LIGHT` / `HEAVY` / `SPARE` | see [At a glance](#at-a-glance) | Model identifiers |
| `LOOP_CALLS` / `ALL_CALLS` | 42 / 60 | Main-loop model calls / all model attempts |
| `SPEND_SOFT` / `SPEND_HARD` | 0.055 / 0.078 | Heavy-judge limit / loop stop |
| `HEAVY_ROOM` | 0.02 | Spend that must remain before a heavy call |
| `WAITS` / `WAIT_TOTAL` | (4, 10, 20, 35) / 45 | Backoff schedule / total wait per episode, in seconds |
| `SHOW_CHARS` | 450 | Clip for each string in an observation shown to the model |
| `OBS_LIMIT` | 15000 | Maximum characters of one observation shown |
| `HISTORY_LIMIT` | 150000 | Chat size that triggers `trim` |
| `GATE_ROUNDS` | 3 | Rounds of process-step work per gate |
| `AUTO_PAGES` | 6 | Search pages read automatically per episode |
| `CHEAPER_OPENS` | 3 | Cheaper same-kind listings opened before the judge review |
| `WIDEN_EACH` | 6 | Listings opened per item before the interview (4 with more than two items) |
| `GUARD_KEYS` / `GUARD_SHARE` / `GUARD_GAP` | 3 / 0.0 / 0.34 | Keys considered, minimum coverage and coverage lead needed to displace a pick |
| `NOT_PROPERTY` / `CATCH_ALL` | regex | Attribute keys never asked about / asked last |
| `FIT_CODE` / `PART_CODE` | regex | Model and part code patterns for fit checks |

## Code map

The module reads top to bottom in this order:

| Area | Definitions |
|---|---|
| Configuration and prompts | Module constants, `_llm`, `_env` |
| Text and fit parsing | `_codes`, `_fit_marks`, `_words`, `_stem`, `_roots`, `_names` |
| JSON and schemas | `_dump`, `_clip`, `_json_obj`, `_kinds`, `_fits_kind`, `_shape`, `_items`, `_count` |
| Record inference | `_id_like`, `_learn_id`, `_in_rows`, `_fact_block` |
| Data model | `Item` |
| Episode state and small queries | `Kyo.__init__`, `left`, `msgs_free`, `cart_mark`, `page_tools`, `facts_tool`, `compare_facts`, `compare_bare`, `gate_due`, `tail` |
| Model and store calls | `judges`, `think`, `call_model`, `ask_json`, `act` |
| Request and ledger | `read_request`, `shared_shelf`, `unmix`, `described`, `ledger`, `status` |
| Listing knowledge | `ident`, `learn_rows`, `absorb`, `labels`, `category_key`, `key_table`, `not_stated`, `twins`, `item_groups`, `single_target`, `price_of`, `buyable`, `records_in`, `note_view`, `shown_already` |
| Search and reading | `read_shape`, `paging`, `price_sorted`, `more_pages`, `cap_ignored`, `cheapest_here`, `wider_hold`, `fill_facts`, `cheaper_rows`, `observe`, `widen`, `widen_singly` |
| Interview | `ready_to_ask`, `message_args`, `interview`, `search_template`, `value_words`, `value_search`, `next_ask`, `ask_text`, `lane_keys`, `owner_of`, `owns`, `records_of_item`, `plan_lanes`, `question`, `samples`, `send`, `take_heard`, `hear` |
| Process steps and notes | `gate`, `spoken`, `loose_lines`, `take_note`, `needs_now`, `evidence` |
| Cart state and checks | `cart_text`, `held_units`, `cart_ids`, `record_of`, `others`, `fit_index`, `fit_set`, `item_ids`, `fit_faults`, `fit_plan`, `line_faults`, `count_fixes`, `fix_counts`, `check_key`, `check_cart` |
| Chains and recovery | `trail`, `shop_rows`, `opened_shop`, `find_link`, `offer_tool`, `reread_offers`, `sold_out`, `swap_hint`, `find_sold`, `replacements` |
| Decline audit | `audit`, `audit_call`, `audit_fits`, `open_rows`, `cart_calls`, `complete_cart` |
| Call execution | `is_buy`, `is_refuse`, `offered`, `quiet`, `resolve_ids`, `cart_sets`, `note_cart`, `show`, `compare_target`, `compare_room`, `join_opens`, `run_calls` |
| Episode control | `finish`, `trim`, `run`, `work`, `agent_main` |

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

CI runs the same checks on every pull request and on pushes to `main`.

## Known limitations

- **`ProxyClient` is external.** The module cannot be imported without it.
- **Offline tests only.** `tests/legacy` covers the agent's control flow with a scripted
  model and store. No test measures its results against live models or real stores.
- **Spend tracking needs `usage.cost`.** If the proxy omits it, spend stays at 0 and the
  spend limits never trigger. The call limits still apply.
- **The final review fails open.** If a judge is unavailable or returns nothing,
  `check_cart` raises no objection and checkout proceeds.
- **Budget checks tolerate unit mismatches.** `line_faults` flags an over-budget cart only
  when the excess is within 10× the budget, treating anything larger as a likely unit
  difference. A truly huge overspend is therefore not flagged by this check.
- **Fit checks need parseable codes.** `fit_faults` only sees model and part codes matching
  `FIT_CODE` and `PART_CODE`, so other formats are left to the model and the judge.
- **The heuristics assume English.** Prompts and many regular expressions, such as those
  in `shop_rows` and `not_stated`, expect English text.
- **One large, uncommented module.** There are no comments or docstrings, so the prompts
  and method names carry the documentation.
- **No license file.** No license is declared, so default copyright applies.
