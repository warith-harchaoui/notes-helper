//! `nh-io` — filesystem and decoding adapters for `nh-core`.
//!
//! Module summary
//! --------------
//! Adapters that satisfy `nh-core`'s ports using real I/O and codecs. Keeping them in a
//! separate crate preserves the purity of `nh-core` (no filesystem/codec deps there) and
//! matches the ports-and-adapters design (see `ARCHITECTURE.md`).
//!
//! Current adapters:
//! - [`ffmpeg::FfmpegSource`] — decode ANY ffmpeg-supported format to 16 kHz mono (the
//!   general path; mirrors what the Python `audio-helper` does under the hood).
//! - [`wav::WavFileSource`] — a zero-dependency reader for 16 kHz mono WAV (fallback when
//!   ffmpeg is unavailable).

// No FFI here; keep unsafe out and doc-comments compiler-enforced (CODING.md).
#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod ffmpeg;
pub mod wav;

pub use ffmpeg::FfmpegSource;
pub use wav::WavFileSource;
