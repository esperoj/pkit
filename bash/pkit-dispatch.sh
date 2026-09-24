#!/usr/bin/env bash
# Polyglot dispatcher: routes to specific language binaries if needed.

CMD=$1
shift

case "$CMD" in
  wb|wayback)
    # Route to the compiled OCaml binary
    exec pkit wb "$@"
    ;;
  py-tool)
    # Route to a Python module within the monorepo
    exec python3 -m pkit.python_tool "$@"
    ;;
  *)
    echo "Unknown pkit command: $CMD" >&2
    exit 1
    ;;
esac
