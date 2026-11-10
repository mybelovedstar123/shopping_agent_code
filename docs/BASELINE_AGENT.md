# The original single agent

`agent.py` is the agent this project grew from: one file, one class, and one conversational
model working through an episode. The [agent team](ARCHITECTURE.md) was extracted from it for
version 1.0.0. Since then it has been developed on its own as a standalone agent, and the team
has not taken over those changes. The rest of this repository does not import it.

It was built for an external harness: a store episode scores 1 only when the finish is exactly
right, and 0 otherwise. Each episode is also stopped after 300 seconds.

## At a glance

| | |
|---|---|
| Entry point | `agent_main(problem_data) -> list[dict]`, which returns the episode trace |
| Code | One module, `agent.py`: about 5,200 lines, with 186 methods on the `Kyo` class and 51 module functions |
| Runtime | Python standard library only, plus a `ProxyClient` supplied by the host harness |
| Python | Run on 3.12. Annotations are deferred, so newer type syntax is not evaluated at runtime |
| Models | `anthropic/claude-haiku-5.5` (light), `anthropic/claude-sonnet-5.5` (heavy), `openai/gpt-6-luna` (spare) |
| Default limits | 30 steps unless the store sets `max_steps`; 42 loop calls and 60 model calls; loop stops at 0.078 spend; work planned to end 20 s before the 300 s limit |

## Architecture

```
agent_main(problem)                     catches every exception and always returns the trace
 └─ Kyo(problem).run()
     ├─ read_request()                  request + tool list -> structured brief (retried for up to 120 s)
     ├─ first_moves()                   2+ items: search each item and read listings before any model turn
     ├─ work()                          runs turns(), recovering from up to 3 exceptions
     │    └─ turns()                    main loop: a model chooses tool calls, until time or limits run out
     │         ├─ interview()           asks the shopper about unstated preferences
     │         └─ run_calls()           checks and executes each call; can hold an ending and search on
     └─ finish()                        fallback checkout or decline if the loop ends unfinished
```

Four design decisions shape the code:

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

**A clock governs every phase.** An episode that runs out of time earns nothing, so each
phase has a deadline measured from the start of the episode (see [Time budget](#time-budget)),
model calls get a timeout that fits the time left, and optional reads are skipped once the
remaining time only covers the order itself.

## How an episode runs

### 1. Reading the request

`read_request` sends the request and the tool list to the light model (`BRIEF`). It
retries on the heavy model when the brief lacks items or a buy role, or when two items
share one store shelf. While no brief comes back at all, it tries again every 10 seconds for
up to 120 seconds from the start of the episode. The brief provides:

- **Items:** name, store shelf, quantity and firm requirements written as
  `property: constraint`. Items on the same shelf with the same quantity are merged unless
  their requirements conflict.
- **Roles:** which tool does `search`, `open`, `offer`, `compare`, `ask`, `cart`,
  `cart_view`, `buy` and `refuse`, plus `search_words`: the tool, field and fixed
  arguments that search listings by free words. If one tool both buys and declines, a
  further prompt finds the arguments that select each mode.
- **Rules:** budget, unit and scope (total or per item), pick rule (`cheapest`, `most`,
  `least` or `any`), message and step limits, wishes, and whether to decline quietly.
- **Process steps:** actions the request demands besides buying, such as getting
  approval. Each is due before choosing, before ordering, or before ending.
- **Cross-listing requirements:** requirements whose value lives on another listing
  (`lookups`) and part-to-device pairs (`fits`). `unmix` removes words that describe the
  other listing from the product's own requirements.

If the request does not state a message limit but an `ask` tool exists, the limit is 3.
A stated step limit lowers `max_steps` when it is at least 10.

### 2. First moves

For a request with two or more items and at least 20 steps, `first_moves` works before the
model's first turn, because those first turns would only search. For each item it searches
the item's short required values with its noun, then the noun alone when that finds nothing,
reading one further page when the first shows too few of the item's listings. When the
shopper can be asked, it then opens listings side by side until each item has six read in
full or a fifth of the steps are spent, so the interview has attribute keys to ask about.
The model starts with what was found and is told to search further only for what is missing.
Store calls in this phase are spaced at least 0.5 seconds apart, and 0.25 seconds afterwards.

### 3. Learning the store

| Fact | How it is learned |
|---|---|
| Result layout | `_restore` turns every result into one form before anything reads it: "name: value" text, text tables, rows keyed by listing code, columns named once, and attribute blocks nested in groups or written inside one field |
| Duplicate keys | `_fold_twins` keeps one line per property when a block spells a key twice, keeping both values when they differ |
| Listing id key | `_learn_id` scores row keys: unique, id-like values, preferring keys named like an id |
| Listing records | `absorb` walks every observation and keeps a row per listing, plus a full record once an attribute block appears (`_fact_block`) |
| Category key | `category_key` prefers row keys whose values match enum labels in the tool schemas |
| Attribute table | `key_table` lists up to 18 keys per category and merges keys carrying the same values (`twins`), comparing values in the spelling the listing gave first |
| Prices and stock | `price_of` reads figures such as "$1,299.00" or "45 EUR"; `buyable` treats stock words such as "sold out", "unavailable" or "no" as out of stock |
| Result layout keys | `read_shape` asks the model once per search tool for the keys holding rows, total, next page, price and stock (`SHAPE`) |
| Which tool shows attributes | `facts_from` records this per tool, and a listing view that showed no attributes is not opened through that view again. `open` and `offer` are reassigned when a tool's results contradict the brief |
| What each call showed | `note_read` keeps every search and listing result by call, whoever made it, so a repeated call is answered from memory |

Search paging is automatic only for `cheapest`, `most` and `least` requests. `more_pages`
reads at most two further pages per search and `AUTO_PAGES` per episode, and it skips
paging when a cheapest-pick result is already sorted by price. For anything larger it
tells the model to narrow the search or page on. When a compare tool shows attributes,
listings are read through it in batches that fit its limit instead of one at a time.

### 4. Interviewing the shopper

Shoppers hold firm preferences they did not write down, and a question reveals one only
if it names that exact property. The interview runs once per episode, when all of these
hold:

- messages are free after reserving any needed by process steps, more than 8 steps
  remain, and less than 165 seconds have passed;
- every item has listings read, or the model has had two turns;
- enough is known (`3 × items + 1` listings opened, or `max(8, max_steps // 4)` steps
  used), or the model tries to touch the cart or end the episode first.

The interview then:

1. Opens more candidates so attribute keys are visible (`widen`, `widen_singly`), taking
   turns between items so that each gets some.
2. Builds a ranked question list per item (`plan_lanes`). The heavy model ranks what the
   request asks to check, then properties hinted at by the story, then listed keys
   (`PLAN`). High-coverage keys can displace weak picks from the top three (`GUARD_*`).
   Identifier-like keys (`NOT_PROPERTY`) are excluded, and catch-all keys (`CATCH_ALL`)
   go last.
3. Asks its questions as lists: one message names several planned properties (up to 12
   for one item, 8 across items; the first lists are shorter), and when the items outnumber
   the free messages, each message covers a share of the items (`ask_group`). A property an
   answer already settled is not asked again, and an item named with "and" is asked about
   with "or" ("door handles or locks"). No new question starts after 210 seconds.
4. Parses each reply into new, dropped and "don't mind" requirements (`HEAR`), matching
   each new requirement back to the property it answers (`settle`). An answer that names
   no property, item, key, value or number is not sent to the model to read
   (`quiet_reply`), except the first answer of the episode. If the shopper complains that
   a question asked too much, it asks again with fewer properties or with example values.
5. Searches on newly learned values and opens the first fitting rows (`value_search`), and
   follows requirements that point at another listing once while there is room to act on
   them (`early_trail`).

The message text goes into whichever field the message tool takes: chosen by its name and
size, inside a nested or list field when there is no plain one, with safe values for other
required fields. The conversational model never messages the shopper: the `ask` tool is
hidden from it, and such a call is answered as not run.

Just before a checkout, `late_question` asks one more planned question when a message is
left that the order itself does not need, at least 8 steps remain, and less than 190
seconds have passed. When its answer adds a requirement, the checkout waits and the model
is told.

### 5. Guarded tool execution

`run_calls` applies these checks before spending a step:

| Check | Effect |
|---|---|
| Unknown tool, tool not offered, or invalid JSON | Rejected with the reason |
| Schema mismatch | `_shape` coerces types and enum case and truncates long strings; remaining faults reject the call |
| Listing title used as an id | `resolve_ids` converts a unique match, or asks for one id. A code that one seen row's title holds is sent as that row's id (`row_ids`) |
| Sold-out listing opened again, or removing a line not in the cart | Rejected |
| Removal of a held line sent without a count | Sent with the units held, since the store would take off only one (`whole_removal`) |
| Ending call sent alongside other calls | Rejected: it must go alone |
| Same call just made, cached read unchanged, or view already fully shown | Answered from memory |
| Listing opened again through a view that showed it no attributes | Answered from memory |
| New search the model starts on a later page | Sent for its first page instead (`first_look`); a later page that looks like the end of the results is checked (`last_page_first`) |
| Long words search that found nothing | Run again with its first words, without a model turn (`shorter_search`) |
| Model or part code search after two code searches found nothing | Rejected: search by words and values instead |
| Several single opens in one turn | Merged into one `compare` call (`join_opens`) |
| Side-by-side call over the store's limit | Over a schema `maxItems`: rejected. Over a limit learned from an empty result: trimmed |

`offered` narrows the tools as steps run out, and `tail` reserves the closing steps
(one, or two when a message to the shopper is due at the order). A cart change that leaves
a part and a device that do not work together is reported straight away.

### 6. Ending holds

An ending call can be held so the agent can fix something. Each hold has a limit, so
the agent cannot loop forever, and checkout holds apply only while more than 2 steps
remain.

**Before checkout**, in this order:

| # | Check | Limit |
|---|---|---|
| 1 | `late_question`: one more planned question, when a message the order does not need is left | Once per episode |
| 2 | `hunt_cheaper` then `wider_hold` (cheapest pick only): Kyo searches on by the items' values itself, one step per search and no model call, then the model may search further | `hunt_cheaper` runs at most twice, each time the requirements, budget or sold-out listings have changed, needs 3 spare steps and stops after 200 s; `wider_hold` once, until store notes reset it |
| 3 | `reread_offers`: re-read carted listings for price or stock changes, and refresh the cart view | When stale, and while time allows. A change in the cart view holds; so do re-read changes once past the soft spend limit |
| 4 | `swap_hint`: sold-out lines, with replacements proposed by a judge (`SWAP`) | Each lost line once, then 2 reminders |
| 5 | `trail`: open every listing a requirement points at, link by link (`TRAIL`). A page counts only when read through the view that shows it in full | 3 holds, or 2 with 15 steps or fewer left |
| 6 | `fit_faults`: each part must fit the carted device, by its model code, a series code it names, or the device's own series, and must not list the device after "except" | 2 holds |
| 7 | `line_faults`: one line per item, right quantity, no unrequested kinds, within budget, and taken from the store label that names the item in full when one exists. `fix_counts` repairs quantities itself | 2 holds |
| 8 | `check_cart`: judge review of the records against the ledger (`CHECK`), after reading missing attributes, the parts a line points at, and cheaper same-kind rows (for a cheapest pick, listed below each line's price through a price-capped search when the store has one) | Once per cart and ledger version |

**Before ending without a purchase:**

| Check | Effect |
|---|---|
| `refuse_trail` | Once, with 15 or more steps left: follow what a requirement points at and tell the model the values it found |
| `hunt_fits` | Once: Kyo reads further pages of earlier searches, searches each item by its values, series and words without the model, skipping searches already made, and opens the rows that name the most required values |
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

### 7. Store notes

Free text in any observation can change the task. `loose_lines` collects unseen text,
and `take_note` classifies it (`NOTE`) and applies budget changes, added or dropped
requirements and sold-out listings. If a checkout is refused without naming the sold-out
line, `find_sold` works out which one by re-reading the cart's offers.

### When a carted listing sells out

Stores often sell out a line soon after it is added, so `GUIDE` tells the model to cart each
item as soon as it has found a fit, leaving steps to replace it. A listing that sells out
after it was carted is handled in three places:

1. **Before checkout,** `reread_offers` re-reads the carted listings' offers when they are
   stale, so a price rise or a sold-out line is usually seen before the order is sent.
2. **When the checkout is refused,** the refusal is read as a store note: the line is marked
   gone, `find_sold` works out which line it was if the note does not say, and `swap_hint`
   holds the next checkout with replacements a judge proposes from the listings already
   read (`SWAP`). The model then fixes the cart and checks out again.
3. **When the loop ends unfinished,** `finish` checks out what the cart holds. If that order
   is refused, the model gets ten more turns to apply what the store said, and `finish`
   tries once more. For two or more items, a cart missing a line for some item is first
   completed with the cheapest opened listing that states all of that item's requirements
   (`fill_short_cart`); when no such listing exists and the store allows ending without a
   purchase, the episode declines rather than send a short order.

### Time budget

All times are seconds from the start of the episode, which the harness stops at 300.

| Time | What changes |
|---|---|
| 120 | Stop retrying the request reading |
| 150 | Stop waiting out a model outage: after three failed turns the loop waits 10 s and tries again until then |
| 165 | No interview starts |
| 190 | No question just before checkout |
| 200 | Kyo's own searches stop (with 25 s of grace to finish a page), and the status line tells the model to check out within two turns |
| 210 | No new question in a running interview |
| 255 | No new model turn while the cart holds anything |
| 280 | The planned end: model calls get a timeout that fits the time left before it, and a call is skipped when less than 6 s remain |

Store calls are timed. Once the remaining time only covers the order at this store's
measured speed (`rushed`), reads the order does not need are skipped.

### What counts as a fit

These rules are stated in `GUIDE` and repeated in `CHECK` and `READING`, so the buyer and
the judges use the same standard:

- The listing's category label decides the kind of product, even when its title or
  description names something else.
- An attribute whose key names the required property decides the requirement. A key for
  a related but different property, such as a handle colour for a body colour, decides
  nothing. A key that adds the item's own name to the property decides it, even against
  the bare key: a bag's "bag shape" counts over its "shape".
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
| `CHECK` | `check_cart` | Final cart review | light first; heavy only when it objects (see below) | 3000 |
| `AUDIT` | `audit_call` | Look for missed fits before declining | light; heavy when the light reply names no usable fit | 4000 |
| `SWAP` | `replacements` | Find replacements for sold-out lines | `judges()`, light first | 3500 |

`READING` is not sent alone: it is the shared rule text inside `AUDIT` and `SWAP`.

`check_cart` asks the light model alone first when both judges are available, the pick is
`any` and no lead or audit is involved. Only when the light model objects does the heavy
model check again. Other carts go to the judges in the usual order.

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
| Model: too little time left | The timeout shrinks to fit the time before the planned end, in 5 s steps; with less than 6 s left the call is skipped (`late_call_skipped`) |
| Model: no response or 408 | One retry, then the next model |
| Model: 402, 425, 429, 5xx | Up to 4 retries with jittered backoff (`WAITS`), within `WAIT_TOTAL` seconds per episode |
| Model: 402 after retries | Sets `no_credit`, which stops the main loop and leads to the fallback ending |
| Model: three failed turns in a row | The loop waits 10 s and tries again, until 150 s into the episode |
| Model: other errors or an empty reply | Next model |
| Store: 409 mentioning finish, quarantine or finalize | Episode marked finished |
| Store: 409 with `expected N` | Step counter resynchronised and the call retried |
| Store: 409 mentioning reuse or a different action | Step counter advanced and the call retried |
| Store: 400, 401, 403, 404, 422 | The call fails and the model is told it may try again |
| Store: 402 | Sets `broke`, which stops the loop with no fallback ending |
| Other store errors | Up to 4 attempts per action |
| Exception in a turn | `work` records a `crash` event, answers the model's open tool calls so the conversation stays valid, and runs the loop again, up to 3 times |
| Exception in an optional helper | Helpers marked `_soft` (Kyo's own searches, extra reads) record a `soft_crash` event and the episode goes on without them |
| Exception elsewhere in `run` | `agent_main` records a `crash` event, tries `finish()`, and returns the trace |

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

The return value is a list of dicts, each with a `kind` and `at`, the seconds since the
episode started:

| Group | Kinds |
|---|---|
| Lifecycle | `brief`, `first_moves`, `turn`, `end`, `crash`, `soft_crash` |
| Understanding the request | `items_merged`, `unmix`, `shape`, `one_product`, `shelf_parted`, `adopted` |
| Interview | `plan`, `ask`, `mixed_ask`, `apart`, `list_settled`, `lists_off`, `ask_too_long`, `quiet_reply`, `late_ask`, `value_search` |
| Reading listings | `widen`, `fill_facts`, `pages`, `joined`, `reread`, `trail`, `early_trail`, `link_search`, `link_known`, `show_link`, `row_id`, `page_arg`, `shorter` |
| Searching on | `hunt`, `hunt_fits`, `list_below` |
| Checks | `check`, `gate`, `gate_unproven`, `fit_hold`, `line_hold`, `count_fix`, `swap` |
| Declining | `audit`, `audit_open`, `refuse_blocked`, `late_decline_held`, `completed`, `short_cart_refused`, `filled_line` |
| Fallback endings | `forced_buy`, `forced_refuse` |
| Service failures | `llm_fail`, `env_fail`, `late_call_skipped` |

The `end` event records `finished`, `steps`, `cost` and `calls`. A `crash` event returned
because the problem itself could not be read has no `at`.

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
{'kind': 'end', 'finished': True, 'steps': 1, 'cost': 0.003, 'calls': 3, 'at': 0.0}
```

There are two `turn` events because the first decline is held: most of the steps are
still unused and no search has been tried. This stub's tools take no arguments, so Kyo's
own search before declining has nothing to send, and the decline goes through on the
second turn.

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
| `TASK_SECONDS` / `TIME_MARGIN` / `CALL_FLOOR` | 300 / 20 / 6 | Episode limit / time kept free at the end / shortest model call worth starting |
| `ASK_BY` / `QUESTION_BY` / `LATE_BY` / `WIND_DOWN` / `LAST_TURN` | 165 / 210 / 190 / 200 / 255 | Phase deadlines, in seconds (see [Time budget](#time-budget)) |
| `BRIEF_BY` / `BRIEF_GAP` / `OUTAGE_BY` | 120 / 10 / 150 | How long and how often to retry a failed request reading or model outage |
| `HUNT_BY` / `HUNT_GRACE` / `HUNT_STEPS` / `HUNT_ROUNDS` | 200 / 25 / 24 / 2 | Kyo's own searches: deadline, grace, steps per round, rounds for a cheapest pick |
| `ENV_GAP` / `OPEN_GAP` | 0.25 / 0.5 | Minimum seconds between store calls / during the first moves |
| `OPEN_MIN_STEPS` / `OPEN_ITEMS` / `OPEN_EACH` | 20 / 2 / 6 | First moves run with at least this many steps and items, and read this many listings per item |
| `LIST_SIZE` / `LIST_MIXED` | 12 / 8 | Most properties one question names, for one item / across items |
| `CRASH_TURNS` | 3 | Exceptions in the loop survived per episode |
| `TABLE_KEYS` / `TEXT_CHARS` | 18 / 3000 | Keys shown per category / clip for text-only results |

## Code map

The module reads top to bottom in this order:

| Area | Definitions |
|---|---|
| Configuration and prompts | Module constants, `_llm`, `_llm_short`, `_env` |
| Text and fit parsing | `_codes`, `_code_hit`, `_fit_marks`, `_series_marks` |
| Restoring results | `_failed`, `_plain`, `_pair`, `_split_fields`, `_attr_block`, `_record_text`, `_table_text`, `_records_text`, `_pairs_block`, `_column_rows`, `_keyed_rows`, `_flat_block`, `_restore`, `_fold_twins` |
| Words and keys | `_glued`, `_text_words`, `_value_tokens`, `_words`, `_snake`, `_stem`, `_roots`, `_spelled`, `_names` |
| JSON and schemas | `_dump`, `_clip`, `_json_obj`, `_kinds`, `_fits_kind`, `_shape`, `_items`, `_count`, `_filler`, `_strings`, `_says` |
| Record inference | `_id_like`, `_learn_id`, `_in_rows`, `_fact_block` |
| Time and safety | `_llm_within`, `_soft`, `_Stamped` |
| Data model | `Item` |
| Episode state and small queries | `Kyo.__init__`, `left`, `clock`, `model_room`, `rushed`, `msgs_free`, `cart_mark`, `row_ids`, `page_tools`, `facts_tool`, `compare_facts`, `compare_bare`, `gate_due`, `tail` |
| Model and store calls | `judges`, `think`, `call_model`, `ask_json`, `act` |
| Request and ledger | `read_request`, `shared_shelf`, `unmix`, `described`, `ledger`, `status` |
| Listing knowledge | `ident`, `learn_rows`, `absorb`, `labels`, `category_key`, `key_table`, `not_stated`, `twins`, `item_groups`, `single_target`, `price_of`, `buyable`, `records_in`, `note_view`, `shown_already` |
| Search and reading | `read_shape`, `paging`, `price_sorted`, `more_pages`, `cap_ignored`, `cheapest_here`, `wider_hold`, `fill_facts`, `show_links`, `cap_template`, `list_below`, `cheaper_rows`, `observe`, `widen`, `widen_singly` |
| Kyo's own searches | `hunt_words`, `hunt_cheaper`, `hunt_item`, `hunt_fits`, `unread_pages`, `call_mark`, `note_read`, `query_is_new`, `last_page_first`, `shorter_search`, `first_moves` |
| Interview | `ready_to_ask`, `message_args`, `interview`, `late_question`, `search_template`, `value_template`, `value_words`, `value_search`, `groups_for`, `ask_group`, `ask_next`, `next_ask`, `ask_text`, `settle`, `lane_keys`, `owner_of`, `owns`, `records_of_item`, `plan_lanes`, `question`, `item_said`, `samples`, `send`, `take_heard`, `quiet_reply`, `hear` |
| Process steps and notes | `gate`, `spoken`, `loose_lines`, `take_note`, `needs_now`, `evidence` |
| Cart state and checks | `cart_text`, `held_units`, `cart_ids`, `record_of`, `others`, `fit_index`, `fit_set`, `item_ids`, `link_system`, `fit_faults`, `fit_plan`, `cheapest_sets`, `fill_short_cart`, `line_faults`, `count_fixes`, `fix_counts`, `check_key`, `check_cart` |
| Chains and recovery | `early_trail`, `refuse_trail`, `trail`, `shop_rows`, `opened_shop`, `find_link`, `offer_tool`, `reread_offers`, `sold_out`, `swap_hint`, `find_sold`, `replacements` |
| Decline audit | `audit`, `audit_call`, `audit_fits`, `open_rows`, `cart_calls`, `complete_cart` |
| Call execution | `is_buy`, `is_refuse`, `offered`, `quiet`, `resolve_ids`, `cart_sets`, `whole_removal`, `note_cart`, `show`, `compare_target`, `compare_room`, `join_opens`, `run_calls` |
| Episode control | `finish`, `can_refuse`, `trim`, `run`, `work`, `answer_open_calls`, `turns`, `agent_main` |

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
  in `not_stated` and `quiet_reply`, expect English text.
- **The time budget assumes a 300 second episode.** The phase deadlines are constants, so
  a harness with another limit needs them changed.
- **One large module.** It has almost no comments. The newer methods carry docstrings,
  and the prompts and method names document the rest.
- **No license file.** No license is declared, so default copyright applies.
