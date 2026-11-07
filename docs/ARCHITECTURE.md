# Architecture

## Starting point

`agent.py` is one class, `Kyo`, with 119 methods sharing about 90 fields. It works, but
every concern can read and change every other concern's state, so no part can be tested,
replaced, or run on its own.

## Target

A team of independent agents shopping through one coordinator:

```
                       shopper request
                             |
                             v
                       +-------------+
      +--------------->| coordinator |<----------------+
      |                +-------------+                 |
      |        messages only |  no shared memory       |
      v                      v                         v
   request    catalog   interview   selection   cart   review   process   notes
      |          |          |                     |               |         |
      v          +----------+---------------------+---------------+         |
    models              store tools  <----------- observations -------------+
```

The rules that make the agents independent:

1. **Private state.** An agent's data is reachable only by sending it a message.
2. **Serialisable messages.** Payloads are plain JSON values, so an agent can move to
   another thread or process without changing its code.
3. **One mailbox per agent.** Each agent handles one message at a time, in order, on its
   own worker.
4. **Failure stays contained.** A crash in a handler becomes an error reply to the sender.
   The other agents keep working, and the coordinator decides what happens next.
5. **Own budgets.** Each agent spends from its own allowance of model calls.
6. **Works without a model.** Every agent has a deterministic path, so the whole team can
   run and be tested offline. A model improves judgement but is never required.

## Agents

| Agent | Job | Owns | Replaces in `Kyo` |
|---|---|---|---|
| Request | Turn the shopper's words into a brief | Nothing after replying | `read_request`, `unmix`, `shared_shelf` |
| Catalog | Search, page, open and remember listings | Listing memory, learned store shape | `absorb`, `ident`, `learn_rows`, `category_key`, `read_shape`, `paging`, `more_pages`, `fill_facts`, `widen`, `widen_singly` |
| Interview | Ask about unstated preferences | Questions asked, message allowance | `interview`, `plan_lanes`, `lane_keys`, `next_ask`, `send`, `hear`, `take_heard` |
| Selection | Choose the listing for each item | Rejection reasons | The fit rules in `GUIDE`, and the model's choices in `work` |
| Cart | Change the cart and detect drift | Cart lines | `note_cart`, `cart_calls`, `line_faults`, `count_fixes`, `fix_counts`, `held_units` |
| Review | Judge carts and declines independently | Verdicts | `check_cart`, `audit`, `audit_call`, `replacements` |
| Process | Carry out steps the request demands | Proof that steps happened | `gate`, `gate_due` |
| Notes | Read free-text changes from the store | Notes already seen | `loose_lines`, `take_note` |
| Coordinator | Decide the next step and apply limits | Episode progress | `run`, `work`, `run_calls`, `finish`, `status`, `offered` |

Only the coordinator knows the overall workflow. It asks agents for results and never
reaches into their state.

## What the team does not do yet

The team replaces most of `Kyo`, but not all of it:

- **Requirements that point at another listing** ("the same colour as the kettle Ovarno
  sells") are not followed. They are returned in the result's `unverified` list and are not
  checked by the rules. `trail` and `find_link` in `agent.py` have no equivalent yet.
- **The conversational buying loop is gone.** `GUIDE` is unused: the coordinator's workflow
  chooses every step, and models only help with specific decisions. This makes runs
  predictable and testable, but a model cannot improvise around an unusual store.
- **The model paths are tested with scripted replies only.** They have not been run against
  a live model.

## The application around the team

```
browser --> web pages --+
                        +--> services --> database
agent team --> tools ---+
```

Shoppers and agents go through the same services, so a cart rule or stock check cannot be
skipped by either. The team never holds payment details, and a purchase made by the
assistant requires the shopper's approval of the exact cart and total.

## The baseline stays

`agent.py` remains unchanged as a standalone agent and as the behaviour the team is
compared with. New code lives in the `kyo` package.
