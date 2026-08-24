# Contributing

Cohub is currently a private personal-tool project, but changes still follow production discipline.

## Development workflow

1. Create a focused branch.
2. Add a failing `unittest` that captures the desired behavior.
3. Run the focused test and confirm the intended RED failure.
4. Implement the smallest coherent change.
5. Run the focused test and the full suite.
6. Update architecture, security, API, and workflow documentation when contracts change.
7. Keep code, comments, commits, issues, and pull requests in English.

## Quality gates

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -v
python3 -m py_compile cohub/*.py
node --check cohub/static/app.js
```

## Design rules

- The engine owns routing and state transitions.
- Executors never schedule the graph directly.
- Published workflows are immutable.
- External side effects require idempotency and read-back verification.
- Approval is bound to the exact canonical payload hash.
- UI restrictions must also exist as backend invariants.
- Handlers exposed to Hermes return JSON strings and never leak uncaught exceptions.
- Do not add a dependency when the standard library is sufficient and clearer.
