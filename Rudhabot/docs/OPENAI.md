# OpenAI integration (`/ask` and the match Chronicle)

OpenAI is Rudhabot's **second** intelligence component. It handles open-ended language:
- general questions ("what's the mass of the sun?");
- explaining Rudhabot's features and the Cloudfall rules;
- narrating finished matches.

Game logic, rewards and the in-house Oracle never depend on it, and every Rudhabot feature works without it.

## Routing: who decides what

| Task | Handled by | Why |
|---|---|---|
| Legal moves, outcomes, rewards, ratings, balances, permissions | Deterministic code (`tcg/engine.py`, services) | Must be exact and server-authoritative |
| Win probability, AI moves, hints, Deck Doctor, similar cards | In-house Oracle (`docs/ML.md`) | Fast (sub-millisecond), private, owned, measurable |
| Finding Rudhabot's own docs, rules or cards | Local BM25 retrieval (`llm/knowledge.py`) | Grounding; also the offline answer |
| General knowledge; phrasing an answer, explaining a rule, writing a recap | OpenAI Responses API | Language quality; not authoritative about Rudhabot |

## Setting it up and checking it

1. Put `OPENAI_API_KEY=sk-...` in `.env` next to `main.py` (see `.env.example`), or export it, then restart the bot. The
   startup log should show `OpenAI assistant enabled: model ...`. If it's off, the log says why.
2. Run one real request to check the key and model. It prints the outcome, or the exact provider error:

   ```bash
   python -m rudhabot.llm.check "What's the mass of the sun?"
   ```
3. `/oracle status` (admin) shows whether the assistant is on, why it is off, its call counts and the last provider error.

When the assistant is off, `/ask` still answers questions about Rudhabot from the local docs. For general questions it
says that the AI is off, and why, instead of returning an unrelated document.

## How `/ask` works

1. **Limits.** The question is cut to 500 characters. Then three caps are checked:
   - a per-user token bucket (12 an hour);
   - a per-guild bucket (120 an hour);
   - a daily request budget (800).

   A global concurrency cap (3) applies to calls in flight. If any cap is hit, the user gets the local answer with a polite
   notice.
2. **Instructions.** General-knowledge questions are answered from the model's own knowledge. Facts *about Rudhabot*
   (commands, card text, rules, prices, balances, matches) must come from the `<context>` block or the tools, never be
   invented.
3. **Request.** Local retrieval adds Rudhabot's most relevant documents in a `<context>` block. The question goes in a
   `<question>` block. Both are declared untrusted data, so instructions inside them are ignored.
4. **Call.** It goes to `client.responses.create` with:
   - `store=False`, so OpenAI keeps no conversation state;
   - a hashed `safety_identifier`, never the Discord id;
   - strict JSON-schema structured output: `answer`, `suggested_commands`, `cards`, `confidence`;
   - three **read-only, strict** function tools:
     - `search_docs`
     - `get_card`
     - `my_cloudfall_status`, which is bound to the *caller*. It returns only their own profile and their current match's
       public state, plus their own hand.
5. **Reasoning models.** For gpt-5\* and o-series models, Rudhabot sends `reasoning: {effort: "low"}` by default and requests
   encrypted reasoning items, so the stateless tool loop works. Thinking tokens count against the output cap (2,500).
6. **Tool loop.** At most 3 tool rounds, 3 calls per round, and 2,500 characters per tool output.
7. **Validation.** An answer that stops at the token cap (`status: incomplete`) is treated as an error and never shown
   half-written. The output is schema-checked and capped at 1,200 characters. `@everyone`, `@here` and user mentions are
   neutralised, and replies are sent with `AllowedMentions.none()`. Suggested commands are kept only if they exist in
   Rudhabot, and are **shown, never executed**.
8. **Fallback.** Any error, timeout, invalid output or runaway tool loop produces the deterministic local answer instead,
   and `last_error` is recorded for `/oracle status`. The footer always says whether the answer was written by AI or taken
   from the built-in docs.

The **Chronicle** is a 2–3 sentence recap posted when a match ends. It is written only from engine facts (names, winner,
reason, rounds, final Resolve) and the Oracle's turning points, and falls back to a plain template.

## Configuration (environment variables or `.env`)

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | unset | Required to enable the LLM. Never stored in settings, code or logs |
| `RUDHABOT_LLM_ENABLED` | `1` | Set to `0` to force local-only answers |
| `RUDHABOT_LLM_MODEL` | `gpt-5.4-mini` | Any Responses-API model your key can use |
| `RUDHABOT_LLM_REASONING_EFFORT` | `low` for gpt-5\*/o\*; not sent otherwise | e.g. `minimal`, `low`, `medium` |
| `RUDHABOT_LLM_TIMEOUT_S` | `30` | Per-request timeout (one SDK retry) |
| `RUDHABOT_LLM_MAX_OUTPUT_TOKENS` | `2500` | Output cap per call, thinking included |
| `RUDHABOT_LLM_PER_USER_PER_HOUR` / `_PER_GUILD_PER_HOUR` / `_DAILY_BUDGET` | `12` / `120` / `800` | Cost and abuse limits |
| `RUDHABOT_CHRONICLE_ENABLED` | `1` | End-of-match recaps |

The default model name comes from the model list in the installed `openai` SDK (3.42.0). Its availability on your account,
and its price, weren't verified from here. `python -m rudhabot.llm.check` will tell you if it doesn't work for your key.

## Privacy

- Rudhabot stores no prompts or answers.
- Requests use `store=False`.
- Only the question, the retrieved public docs and the caller's own Cloudfall data are sent.
- Nothing is ever sent about other users.
- Without a key, nothing leaves the server.

## Tests

`tests/test_llm.py` covers the following with a scripted fake model and a mocked SDK client; no test makes a network call:
- the exact request sent (`store=False`, reasoning, structured output);
- incomplete responses;
- `.env` loading and the disabled-reason reporting;
- general-knowledge answers;
- schema validation and command allowlisting;
- mention neutralisation;
- tool scoping to the caller;
- invalid output, provider errors and runaway tool loops;
- rate limits;
- prompt-injection containment;
- the Chronicle fallback.
