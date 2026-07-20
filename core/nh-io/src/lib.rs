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
//! - [`probe::probe_duration_s`] — a cheap `ffprobe` metadata read of a file's duration,
//!   the input the diarization [`nh_core::router`] needs to pick an offline backend.

// No FFI here; keep unsafe out and doc-comments compiler-enforced (CODING.md).
#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod ffmpeg;
pub mod probe;
pub mod wav;

pub use ffmpeg::FfmpegSource;
pub use probe::probe_duration_s;
pub use wav::WavFileSource;
