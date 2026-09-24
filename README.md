# pkit

BusyBox-style personal CLI toolkit. Stable utilities in OCaml, fast workflow glue in Python/Bash.

## Architecture

```
pkit/
├── bin/pkit.ml              # BusyBox dispatcher + compiled subcommands
├── lib/
│   ├── core/                # Shared CLI helpers (stdin, exit codes)
│   └── wayback/             # SPN2 client (functor-tested, stable)
├── python/                  # Python SDK & ephemeral glue scripts
├── bash/                    # Shell glue & batch wrappers
└── Makefile                 # Unified polyglot dev workflow
```

| Layer | Language | Purpose |
|---|---|---|
| Complex, stable utilities | OCaml | Type-safe, functor-tested, single binary |
| Fast workflow glue | Python / Bash | Low ceremony, rapid iteration, easy to delete |
| Dispatch / entrypoint | OCaml | Native symlink resolution, instant startup |

## Install

### Development

```bash
make dev          # Build OCaml, setup Python venv, create ./bin/ symlinks
export PATH="$PWD/bin:$PATH"
```

This creates `./bin/pkit`, `./bin/wb`, and `./bin/wayback` as local BusyBox symlinks.

### Global

```bash
make install      # Install to opam switch + pip, create global symlinks
```

## Usage

```bash
# Subcommand dispatch
pkit wb save https://example.com

# BusyBox symlink mode (wb → pkit wb)
wb save https://example.com

# Read URL from stdin
echo https://example.com | wb save

# JSON output
wb save --json https://example.com

# Skip authentication headers
wb save --no-auth https://example.com

# Help
wb --help
wb save --help
```

## Contracts

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Unknown / unexpected failure |
| 2 | Usage / input / auth error (user can fix) |
| 75 | Temporary failure, retry later |

### stdout / stderr

- **stdout**: data only (archive URL or JSON)
- **stderr**: short human-readable diagnostics
- Errors never print to stdout

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `INTERNET_ARCHIVE_ACCESS_KEY` | S3 access key for SPN2 |
| `INTERNET_ARCHIVE_SECRET_KEY` | S3 secret key for SPN2 |
| `PKIT_WB_PROXY_PREFIX` | Proxy prefix prepended to `https://web.archive.org` |

## Dev Workflow

```bash
make build        # Build OCaml binaries
make test         # Run OCaml + Python tests
make fmt          # Format OCaml + Python code
make lint         # Lint Python code
make check        # Full CI check (fmt + lint + test)
make clean        # Remove build artifacts
make distclean    # Remove everything including venv
```

## License

MIT
