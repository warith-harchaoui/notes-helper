# notes-helper — native core (`core/`)

The **platform-agnostic engine** behind Notes Helper, in Rust. It holds the domain
model, the ports (ports-and-adapters seam), the typed error surface, and the per-session
orchestration — shared identically across macOS, Linux, Windows, iOS and Android.

See [`../ARCHITECTURE.md`](../ARCHITECTURE.md) for the design, [`../PLAN.md`](../PLAN.md)
for the milestone roadmap, and [`../CODING.md`](../CODING.md) for the standards every
file here follows.

## Layout

| Crate | Role |
|---|---|
| `nh-core` | Domain model, ports, errors, session orchestration. **No OS code, no engine code** (engines arrive as binding crates in M1). |

## Develop

Requires a stable Rust toolchain (install via [rustup.rs](https://rustup.rs)).

```bash
# From the core/ directory:
cargo fmt --all -- --check          # formatting
cargo clippy --all-targets -- -D warnings   # lint, warnings are errors
cargo test --all                    # unit + doctests + integration
```

## Status

**M0** — skeleton: domain model, ports, session offline orchestration wired through mock
adapters, tests green. Engines (whisper.cpp / sherpa-onnx / llama.cpp) land in M1.
