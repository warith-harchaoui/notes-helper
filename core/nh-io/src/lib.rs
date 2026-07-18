//! `nh-io` — filesystem and decoding adapters for `nh-core`.
//!
//! Module summary
//! --------------
//! Adapters that satisfy `nh-core`'s ports using real I/O and codecs. Keeping them in a
//! separate crate preserves the purity of `nh-core` (no filesystem/codec deps there) and
//! matches the ports-and-adapters design (see `ARCHITECTURE.md`).
//!
//! Current adapters:
//! - [`wav::WavFileSource`] — an [`nh_core::ports::AudioSource`] backed by a WAV file.

// No FFI here; keep unsafe out and doc-comments compiler-enforced (CODING.md).
#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod wav;

pub use wav::WavFileSource;
