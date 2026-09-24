# pkit — AI Engineering Guide

BusyBox-style personal CLI toolkit. Stable utilities in OCaml, fast workflow glue in Python/Bash. First tool: Wayback Machine save utility.

Values: durability, simplicity, borrowed standards over novelty.

---

## 1. Philosophy

**Borrow standards, never invent.** Unix CLI conventions, OCaml idioms (Result types, functors, module signatures), Python SDK conventions (exceptions for failure, return values for success), Click, pytest, PEP 8/257, stdlib first. No custom error taxonomies, no bespoke JSON error metadata, no hint systems, no unnecessary abstractions.

**80/20 pragmatism.** Solve the real problem cleanly. No plugin systems, no config files nobody requested, no retry engines, no DI frameworks, no ABCs for one implementation, no logging frameworks, no metrics, no machine-readable error classification in JSON, no excessive option flags. If not needed today, no scaffolding.

**Minimal simplicity.** Best code is code that doesn't exist. Functions over classes when stateless. Dataclasses over custom objects. Explicit over magic. Small modules, one responsibility. Clear boring names. Fewer deps, fewer public options, fewer layers. Every abstraction earns its place.

**Unix philosophy.** stdout = data. stderr = diagnostics. exit codes = scripts. stdin = piping. Predictable flags. Useful help. No chatty output unless asked. Success → print result to stdout, exit 0. Failure → short diagnostic to stderr, exit non-zero.

**Longevity.** Stable language features. Mature libraries. Obvious boundaries. Simple data structures. Explicit contracts. Small public APIs. Easy deletion. Avoid: trendy deps, excessive generics, over-clever typing, framework-imposed structure, internal DSLs, complicated state machines.

**Hybrid by design.** OCaml for complex, stable, rarely-changing utilities where type safety and single-binary distribution matter. Python/Bash for fast workflow glue, prototyping, and ephemeral scripts where velocity matters more than compile-time guarantees. Each language handles the 80% case it was designed for. Glue is disposable; promote to OCaml only when proven stable.

---

## 2. Architecture

pkit/
├── bin/
│   └── pkit.ml              # BusyBox dispatcher + compiled subcommands
├── lib/
│   ├── core/                # Shared CLI helpers (stdin, exit codes)
│   └── wayback/             # SPN2 client (functor-tested, stable)
│       ├── http_sig.ml      # HTTP contract (module signature)
│       ├── http_ezcurl.ml   # Production HTTP adapter
│       ├── types.ml         # Data structures + PPX
│       ├── client.ml        # Business logic (functor over Http_sig.S)
│       └── cli.ml           # Cmdliner interface
├── python/                  # Python SDK & ephemeral glue scripts
├── bash/                    # Shell glue & batch wrappers
├── tests/                   # OCaml tests (Alcotest + functor mocks)
└── Makefile                 # Unified polyglot dev workflow

### Language Boundaries

| Layer | Language | Purpose |
|---|---|---|
| Complex, stable utilities | OCaml | Type-safe, functor-tested, single binary |
| Fast workflow glue | Python / Bash | Low ceremony, rapid iteration, easy to delete |
| Dispatch / entrypoint | OCaml | Native symlink resolution, instant startup |

### OCaml Boundaries

| Rule | |
|---|---|
| `client.ml` | Functor over `Http_sig.S`. Must NOT depend on `ezcurl` directly |
| `http_sig.ml` | Defines HTTP contract. All backends satisfy this signature |
| `cli.ml` | Thin Cmdliner layer. Maps polymorphic variants to exit codes |
| `types.ml` | Pure data structures with `[@@deriving yojson]` |
| `lib/core/` | Generic CLI helpers ONLY. No Wayback-specific logic |

### Python Boundaries

| Rule | |
|---|---|
| `python/pkit/wayback/client.py` | Must NOT import Click, print, or know stdout/stderr/exit codes |
| `python/pkit/common/cli_helpers.py` | Must NOT contain Wayback-specific logic |
| Glue scripts (`python/scripts/`) | Disposable. Delete when stale or promote to OCaml when stable |

### SDK Layer (Library-Only, Both Languages)

- Success returns structured records/dataclasses.
- Failure: OCaml uses `Result.t` with polymorphic variants. Python raises typed exceptions.
- HTTP via functor-injected backend (OCaml) or `requests.Session` (Python).
- No printing, no CLI concerns, no exit calls.

### CLI Layer (Thin, Both Languages)

Parse args → read stdin if appropriate → call SDK → print success to stdout → print errors to stderr → exit with correct code. No deep business logic.

### Command Design

One primary action per command. Predictable args and flags. Useful `--help`. Minimal required options. stdin support where natural. `--json` where useful. Avoid: many positional args, flag explosion, mode flags that radically change behavior, interactive prompts in pipeable tools, required config files.

---

## 3. Contracts

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Unknown / unexpected failure (retry not assumed safe) |
| 2 | Usage / input / auth / config error (user can fix) |
| 75 | Temporary failure, retry later (BSD `EX_TEMPFAIL`) |

Collapsing 75 → 1 is acceptable if simplicity wins, but never silently mix usage errors with unknown failures. Exit codes are identical across OCaml and Python implementations.

### stdout / stderr

- **stdout**: data only. Plain = one useful value (e.g. archive URL). JSON = structured success object.
- **stderr**: short human-readable diagnostics. `Error: No target URL provided.`
- Never print errors to stdout.

### stdin

- If URL argument is explicitly provided, stdin is ignored.
- If URL is omitted or `"-"`, read stdin.
- Interactive TTY stdin with no URL → treat as no input.
- Plain mode: first non-empty line, ignore trailing whitespace.
- JSON mode (`--json`): parse stdin as JSON. Object → extract primary key (`url`) + allow override of selected CLI flags. Scalar → use as primary value. Invalid JSON → treat entire payload as plain text.
- Do not require JSON for simple piping.

### JSON Output

`--json` affects successful output only. No custom error metadata (`error_class`, `error_hint`, `error_type`, `error_code`, `retryable`) unless a real consumer requires them. Scripts rely on exit code + stderr.

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `INTERNET_ARCHIVE_ACCESS_KEY` | S3 access key for SPN2 |
| `INTERNET_ARCHIVE_SECRET_KEY` | S3 secret key for SPN2 |
| `PKIT_WB_PROXY_PREFIX` | Proxy prefix prepended to `https://web.archive.org` |

---

## 4. Error Handling

### OCaml: Polymorphic Variants + Result

type error = [
  | `Input_error of string
  | `Auth_error of string
  | `Rate_limit_error of string
  | `Job_timeout_error of string
  | `Job_failed_error of string
  | `Wayback_error of string
]

All SDK functions return `(success, error) result`. The CLI layer pattern-matches exhaustively to map variants to exit codes. Adding a new error variant causes a compile error at every unhandled match site.

### Python: Typed Exceptions

WaybackError      base SDK error
InputError        invalid user input
AuthError         authentication / authorization failure
RateLimitError    rate limiting / queue pressure
JobTimeoutError   queue wait / job poll timeout
JobFailedError    remote job failed

### CLI Mapping (Both Languages)

| Error | Exit Code |
|-------|-----------|
| InputError / `Input_error` | 2 |
| AuthError / `Auth_error` | 2 |
| RateLimitError / `Rate_limit_error` | 75 |
| JobTimeoutError / `Job_timeout_error` | 75 |
| JobFailedError / `Job_failed_error` | 1 |
| WaybackError / `Wayback_error` | 1 |
| Unexpected exception | 1 |

Do not expose exception class names or variant tags to users unless useful for debugging. Do not invent error hint strings.

---

## 5. Style

### OCaml

- OCaml 5.1+. Use `Result.t` and polymorphic variants for errors.
- Functors for dependency injection (HTTP backends, test mocks).
- `Fun.protect` for resource cleanup. Never bare `try/finally`.
- Module signatures (`.mli`) for all public interfaces.
- Short clear names. Small functions. Comments explain **why**, not what.
- Avoid: unnecessary modules, deep functor nesting, PPX overuse, mutable state.

### Python

- Python 3.13+. `from __future__ import annotations`.
- Type hints on all public interfaces. Simple explicit return types.
- Frozen dataclasses for result values when practical.
- Short clear names. Small functions. Module + public function docstrings.
- Comments explain **why**, not what.
- Avoid: clever one-liners, deep inheritance, excessive typing machinery, runtime decorators unless clearly useful, mutable global state, hidden side effects.

### Naming (Both Languages)

Clear, boring: `save_url`, `archive_url`, `job_id`, `lock_file`, `proxy_prefix`, `read_stdin_payload`, `emit_result`, `fail`.

Avoid: non-obvious abbreviations, cute names, generic names (`manager`, `handler`, `processor`), redundant prefixes, implementation-detail names.

---

## 6. Dependencies

Prefer: stdlib, Click (CLI), requests (HTTP), pytest, requests-mock (Python). ezcurl, cmdliner, yojson, bos (OCaml).

Do NOT add deps for: logging, configuration, validation, retry, schema enforcement, CLI generation, DI, rich output, progress bars, metrics.

A dependency is accepted only if it removes significant code **and** is stable.

---

## 7. Testing

pytest (Python). Alcotest + functor mocks (OCaml). Fixtures for shared setup. Small focused unit tests. A few integration tests. requests-mock for HTTP. Click CliRunner for CLI.

**Test:** stdin behavior, option coercion, CLI success output, CLI exit codes, stderr messages, SDK exception mapping, SDK success result shape, HTTP error mapping, JSON success output.

**Do NOT test:** real Wayback availability, real queue capacity, real rate-limit thresholds, real email delivery, real upstream semantics, real network instability, true distributed lock contention.

A few meaningful tests > many brittle tests.

---

## 8. Documentation

Short and practical. One-line summaries. Concrete examples. Contract descriptions. Exit-code tables. Env-var docs. Clear error examples.

Avoid: marketing language, excessive narrative, duplicated implementation details, speculative roadmaps. Docstrings explain intent and contract, not restate code.

---

## 9. Migration & Refactoring

**Target contract:** SDK raises typed exceptions (Python) or returns Result (OCaml). CLI prints success/stdout, errors/stderr, stable exit codes. `--json` affects success output only. No custom JSON error metadata.

Old code using `result.error` or JSON error output → migrate in small steps when touching it. Do not expand old patterns. Do not add features on deprecated patterns.

**Refactoring:** preserve behavior unless explicitly changing contract. Keep diffs small. No renaming public interfaces without reason. No reformatting unrelated code. No new dependencies. No new abstractions while fixing local issues. Prefer removing code over adding. If a refactor grows large, stop and simplify scope.

---

## 10. Review Checklist

- Borrows an existing standard?
- Solves the current problem only?
- Simplest version that works?
- SDK avoids CLI concerns? CLI avoids business logic?
- stdout = data only? stderr = diagnostics only?
- Exit codes correct? Errors simple and scriptable?
- Tests focused on behavior we control?
- No unnecessary abstraction? No new dependencies?
- Makes sense in five years? Easy to delete?

**When in doubt: smaller, simpler, more boring.**
