# pkit AI Engineering Guide

You are working on pkit, a small BusyBox-style personal CLI toolkit. The first tool is a Wayback Machine save utility. The project values durability, simplicity, and borrowed standards over novelty.

When writing code, tests, documentation, refactors, or reviews for this project, follow this guide.

---

## 1. Core philosophy

### Borrow standards, do not invent

Prefer existing, proven conventions:

- Unix CLI conventions: stdout, stderr, exit codes.
- Python SDK conventions: exceptions for failure, return values for success.
- Click conventions for CLI apps.
- pytest conventions for tests.
- PEP 8 and PEP 257 style.
- Standard library first.

Do not invent custom error taxonomies, custom JSON error metadata, special hint systems, or bespoke output contracts unless there is an overwhelming reason.

If a boring standard exists, use it.

---

### 80/20 pragmatism

Solve the real problem cleanly. Do not overbuild.

Prefer the solution that covers the common cases well and stops before hypothetical complexity.

Do not add:

- plugin systems;
- configuration files nobody requested;
- complex retry/backoff engines;
- dependency injection;
- abstract base classes for one implementation;
- logging frameworks;
- metrics systems;
- error hint engines;
- machine-readable error classification in JSON output;
- excessive option flags.

If a feature is not needed today, do not add scaffolding for it.

---

### Minimal simplicity

The best code is code that does not need to exist.

Prefer:

- functions over classes when state is unnecessary;
- dataclasses over custom object structures;
- explicit code over magic;
- small modules with one clear responsibility;
- clear names over clever names;
- fewer dependencies;
- fewer public options;
- fewer layers.

Every abstraction must earn its place.

---

### Unix philosophy

Tools should behave like traditional Unix utilities.

- stdout is for primary output;
- stderr is for diagnostics and errors;
- exit codes are for scripts;
- stdin is for piping;
- flags should be predictable;
- help should be useful;
- no chatty output unless asked.

A successful command should print the useful result to stdout and exit 0.

A failed command should print a short diagnostic to stderr and exit non-zero.

---

### Longevity

The code should still make sense in five years.

Prefer:

- stable Python features;
- mature libraries;
- obvious module boundaries;
- simple data structures;
- explicit contracts;
- small public APIs;
- easy deletion.

Avoid:

- trendy dependencies;
- excessive generics;
- over-clever typing;
- frameworks that impose structure;
- internal DSLs;
- complicated state machines.

Longevity is more important than cleverness.

---

## 2. Project structure

The repository follows a small, explicit layout:

pkit/
  __init__.py
  cli.py
  common/
    __init__.py
    cli_helpers.py
  wayback/
    __init__.py
    cli.py
    client.py

Responsibilities:

- pkit/cli.py is the BusyBox-style top-level CLI.
- pkit/common/cli_helpers.py contains generic CLI helpers only.
- pkit/wayback/cli.py contains Wayback-specific CLI behavior.
- pkit/wayback/client.py contains the Wayback SDK client.

Rules:

- client.py must not import Click.
- client.py must not print.
- client.py must not know about stdout, stderr, or exit codes.
- cli_helpers.py must not contain Wayback-specific logic.
- Wayback option coercion belongs in pkit/wayback/cli.py.
- Shared Unix CLI helpers belong in pkit/common/cli_helpers.py.

---

## 3. Python style

Use:

- Python 3.13+.
- from __future__ import annotations.
- Type hints for public functions.
- Simple, explicit return types.
- Dataclasses for structured values.
- Frozen dataclasses for successful result values when practical.
- Short, clear names.
- Small functions.
- Module docstrings.
- Public function docstrings.
- Comments only where they explain why, not what.
- All public interface must use correct type hints.

Prefer clarity over brevity.

Avoid:

- clever one-liners;
- deep inheritance;
- excessive typing machinery;
- runtime decorators unless clearly useful;
- mutable global state;
- hidden side effects.

---

## 4. SDK rules

The SDK layer is library-only.

Good SDK behavior:

- success returns structured data;
- failure raises typed exceptions;
- HTTP uses requests.Session;
- clients are context managers;
- clients are closeable;
- no printing;
- no CLI concerns;
- no exit calls.

The Wayback SDK should expose clear exception types:

- WaybackError: base SDK error.
- InputError: invalid user input.
- AuthError: authentication or authorization failure.
- RateLimitError: rate limiting or queue pressure.
- JobTimeoutError: queue wait or job poll timeout.
- JobFailedError: remote job failed.

SDK errors should be typed exceptions, not error strings embedded in result objects.

Avoid SDK APIs like:

result = client.save_url(url)
if result.error:
    ...

Prefer:

result = client.save_url(url)

and let failures raise.

---

## 5. CLI rules

The CLI layer should be thin.

It should:

- parse arguments;
- read stdin when appropriate;
- call the SDK;
- print successful output to stdout;
- print errors to stderr;
- exit with the correct exit code.

It should not contain deep business logic.

### stdout

stdout is for data.

Plain output should usually be one useful value, for example the archive URL.

JSON output should be a structured object representing success.

### stderr

stderr is for diagnostics.

Error output should be short and human-readable.

Example:

Error: No target URL provided.

Do not print errors to stdout.

---

## 6. Exit-code contract

Use exit codes for scripting.

Preferred contract:

0   success
1   unknown or unexpected failure
2   usage, input, authentication, or configuration error
75  temporary failure, retry later

Meaning:

- Exit 0: success.
- Exit 1: unknown failure; script should not assume retry is safe.
- Exit 2: user can fix something; input, credentials, or usage problem.
- Exit 75: temporary condition; retry later may succeed.

Exit code 75 is borrowed from BSD sysexits EX_TEMPFAIL.

If simplicity is more important than retry classification, it is acceptable to collapse temporary failures into exit 1, but do not silently mix usage errors with unknown failures if a script needs to distinguish them.

---

## 7. stdin contract

Support Unix piping.

Plain mode:

- read one line from stdin;
- ignore trailing whitespace;
- use the first non-empty line.

JSON mode:

- attempt to parse stdin as JSON;
- if JSON is an object, extract the primary key, usually url;
- allow the JSON object to override selected CLI flags;
- if JSON is a scalar, use it as the primary value;
- if JSON is invalid, treat the whole stdin payload as plain text.

Rules:

- If a URL argument is explicitly provided, stdin is ignored.
- If URL is omitted or is "-", read stdin.
- Interactive TTY stdin with no URL is treated as no input.
- Do not require JSON for simple piping.

---

## 8. JSON output rules

Use --json for machine-readable success output.

Do not add custom error metadata unless absolutely necessary.

Avoid JSON error objects like:

error_class
error_hint
error_type
error_code
retryable

unless a real consumer requires them.

For this project, scripts should rely on:

- exit code;
- stderr message.

This keeps the contract simple and Unix-like.

---

## 9. Error handling policy

### SDK

SDK functions should raise typed exceptions.

The SDK should not return error strings inside result objects unless there is a very strong compatibility reason.

Typed exceptions allow callers to decide what to do.

### CLI

The CLI should map SDK exceptions to exit codes.

Suggested mapping:

InputError -> exit 2
AuthError -> exit 2
RateLimitError -> exit 75
JobTimeoutError -> exit 75
JobFailedError -> exit 1
WaybackError -> exit 1
unexpected Exception -> exit 1

The CLI should not expose exception class names to users unless useful for debugging.

The CLI should not invent error hint strings.

---

## 10. Testing rules

Use pytest.

Prefer:

- fixtures for shared setup;
- small focused unit tests;
- a few integration tests;
- requests-mock for HTTP mocking;
- Click CliRunner for CLI tests;
- tests that verify behavior we control.

Test:

- stdin behavior;
- option coercion;
- CLI success output;
- CLI exit codes;
- stderr error messages;
- SDK exception mapping;
- SDK success result shape;
- HTTP error mapping;
- JSON success output.

Do not waste tests on things we cannot control:

- real Wayback Machine availability;
- real queue capacity;
- real rate-limit thresholds;
- real email delivery;
- real upstream server semantics;
- real network instability;
- true distributed lock contention.

Use the 80/20 rule.

A few meaningful tests are better than many brittle tests.

---

## 11. Dependency policy

Prefer:

- Python standard library;
- Click for CLI;
- requests for HTTP;
- pytest for tests;
- requests-mock for HTTP tests.

Avoid adding dependencies for:

- logging;
- configuration;
- validation;
- retry;
- schema enforcement;
- CLI generation;
- dependency injection;
- rich output;
- progress bars;
- metrics.

A dependency is accepted only if it removes significant code and is stable.

---

## 12. Command design

Commands should be small and composable.

Good command behavior:

- one primary action per command;
- predictable arguments;
- predictable flags;
- useful --help;
- minimal required options;
- stdin support where natural;
- JSON output where useful.

Avoid:

- many positional arguments;
- flag explosion;
- mode flags that radically change behavior;
- interactive prompts in pipeable tools;
- required configuration files.

---

## 13. Naming rules

Use clear, boring names.

Prefer:

- save_url
- archive_url
- job_id
- lock_file
- proxy_prefix
- read_stdin_payload
- emit_result
- fail

Avoid:

- abbreviations that are not obvious;
- cute names;
- generic names like manager, handler, processor unless necessary;
- redundant prefixes;
- names that describe implementation details.

Public names should be obvious from the module purpose.

---

## 14. Documentation rules

Documentation should be short and practical.

Prefer:

- one-line summaries;
- concrete examples;
- contract descriptions;
- exit-code tables;
- environment-variable documentation;
- clear error examples.

Avoid:

- marketing language;
- excessive narrative;
- duplicated implementation details;
- speculative roadmap documentation.

Docstrings should explain intent and contract, not restate code line by line.

---

## 15. Migration policy

The long-term target is:

- SDK raises typed exceptions.
- CLI prints success to stdout.
- CLI prints errors to stderr.
- CLI uses stable exit codes.
- JSON mode affects successful output.
- No custom JSON error metadata.

Some older code may still use result.error fields or JSON error output. When touching such code, migrate it toward the target contract in small steps.

Do not expand old patterns.

Do not add new features on top of deprecated patterns.

---

## 16. Refactoring rules

When refactoring:

- preserve behavior unless explicitly changing the contract;
- keep diffs small;
- avoid renaming public interfaces without reason;
- do not reformat unrelated code;
- do not introduce new dependencies;
- do not add abstractions while fixing a local issue;
- prefer removing code over adding code.

If a refactor grows large, stop and simplify scope.

---

## 17. Review checklist

Before accepting a change, ask:

- Does this borrow an existing standard?
- Does this solve the current problem only?
- Is this the simplest version that works?
- Does the SDK avoid CLI concerns?
- Does the CLI avoid business logic?
- Is stdout used only for data?
- Is stderr used only for diagnostics?
- Are exit codes correct?
- Are errors simple and scriptable?
- Are tests focused on behavior we control?
- Did we avoid unnecessary abstraction?
- Did we avoid new dependencies?
- Will this still make sense in five years?
- Is the code easy to delete?

When in doubt, choose the smaller, simpler, more boring option.
