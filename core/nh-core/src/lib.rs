//! `nh-core` — the shared, platform-agnostic engine behind Notes Helper.
//!
//! Module summary
//! --------------
//! This crate holds the parts of Notes Helper that are identical on every target
//! (macOS, Linux, Windows, iOS, Android): the domain vocabulary ([`model`]), the
//! trait boundary to the outside world ([`ports`]), the typed error surface
//! ([`error`]), the study-grounded diarization backend [`router`], and the
//! per-discussion orchestration ([`session`]).
//!
//! It follows the ports-and-adapters design (see `ARCHITECTURE.md`): the core only
//! ever talks to the traits in [`ports`]; each platform and each engine supplies its
//! own adapter. Opening a new OS/device means writing adapters, never editing the core.
//!
//! What it deliberately does NOT contain yet: the ASR/diarization/LLM engines
//! (whisper.cpp / sherpa-onnx / llama.cpp arrive in milestone M1, behind the ports),
//! any capture code, and any UI. That keeps this crate compiling and testing in
//! milliseconds with no native build step.

// M0 has no FFI of its own; the engine bindings (M1) live in separate crates so this
// crate can categorically forbid unsafe code and keep `missing_docs` compiler-enforced.
#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod error;
pub mod lid;
pub mod model;
pub mod models;
pub mod pipeline;
pub mod ports;
pub mod router;
pub mod session;
pub mod settings;

// Re-export the handful of types callers reach for most, so shells can `use nh_core::…`
// without knowing the internal module layout.
pub use error::{CoreError, Result};
pub use lid::{regions_from_posteriors, LangRegion};
pub use router::{select_diarization, BackendPlan, DiarBackend, DiarMode, DiarizationQuery};
pub use session::Session;
pub use settings::resolve_diarization_engines_url;
