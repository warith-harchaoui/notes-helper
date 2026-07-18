//! Domain model for `nh-core`: the vocabulary shared by every layer and platform.
//!
//! Module summary
//! --------------
//! These types are the seam between capture, the offline/online pipeline, identity,
//! synthesis, and rendering. They carry no platform code and no engine code — only the
//! data that flows between the ports (see [`crate::ports`]). The names here match the
//! Swift/Kotlin/TypeScript shells (`Speaker`, `Utterance`, `Session`, `Report`,
//! `Source`) so the code reads the same across layers (CODING.md, Part III).

use serde::{Deserialize, Serialize};

/// Canonical sample rate (Hz) for every PCM frame flowing through the pipeline.
///
/// 16 kHz mono is what the VAD, diarization and ASR front-ends consume; pinning a
/// single rate at the seam removes a whole class of resampling bugs downstream.
pub const PIPELINE_SAMPLE_RATE: u32 = 16_000;

/// Stable identifier for one discussion; scopes the per-session output folder.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct SessionId(String);

impl SessionId {
    /// Build a [`SessionId`] from any string-like value.
    ///
    /// # Examples
    /// ```
    /// use nh_core::model::SessionId;
    /// assert_eq!(SessionId::new("2026-07-18-demo").as_str(), "2026-07-18-demo");
    /// ```
    pub fn new(id: impl Into<String>) -> Self {
        Self(id.into())
    }

    /// Borrow the underlying id string.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Opaque, stable identifier for a diarized speaker within (and across) sessions.
///
/// Early in a meeting a speaker is only known positionally (`"S0"`, `"S1"`); once
/// enrolled (the "name once" flow) the same id links to a [`Person`]. We wrap a
/// `String` so labels stay human-readable in reports and logs.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct SpeakerId(String);

impl SpeakerId {
    /// Build a [`SpeakerId`] from any string-like label.
    ///
    /// # Examples
    /// ```
    /// use nh_core::model::SpeakerId;
    /// assert_eq!(SpeakerId::new("S0").as_str(), "S0");
    /// ```
    pub fn new(label: impl Into<String>) -> Self {
        Self(label.into())
    }

    /// Borrow the underlying label.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Stable identifier for an enrolled person (the "who" behind one or more voices).
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct PersonId(String);

impl PersonId {
    /// Build a [`PersonId`] from any string-like value.
    ///
    /// # Examples
    /// ```
    /// use nh_core::model::PersonId;
    /// assert_eq!(PersonId::new("alice").as_str(), "alice");
    /// ```
    pub fn new(id: impl Into<String>) -> Self {
        Self(id.into())
    }

    /// Borrow the underlying id string.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// The kind of a single input in the OBS-style source graph (Q13).
///
/// Several of these are captured and mixed at once (multi-device, multi-source);
/// mobile constrains the set (the OS forbids capturing another app's audio).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SourceKind {
    /// A microphone input device (one of possibly several).
    Microphone,
    /// A system-audio/loopback output (one of possibly several; desktop only).
    SystemAudio,
    /// A camera device.
    Camera,
    /// A screen/window capture.
    Screen,
    /// An already-recorded media file on disk.
    File,
    /// A remote URL (YouTube/podcast), live or VOD.
    UrlStream,
}

/// Whether a source is a live flux or an already-complete recording.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SourceMode {
    /// Live flux processed with a slight delay (real-time path).
    Online,
    /// Complete recording processed whole-buffer (best-quality path).
    Offline,
}

/// One input in the source graph: what it is, whether it is live, and a label.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Source {
    /// Human-facing label (e.g. the device or file name).
    pub label: String,
    /// What kind of input this is.
    pub kind: SourceKind,
    /// Live vs. recorded.
    pub mode: SourceMode,
}

/// A canonical audio buffer: the whole signal at [`PIPELINE_SAMPLE_RATE`], mono.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioBuffer {
    /// Sample rate in Hz (expected to be [`PIPELINE_SAMPLE_RATE`] at the seam).
    pub sample_rate: u32,
    /// Mono PCM samples in `[-1.0, 1.0]`.
    pub samples: Vec<f32>,
}

impl AudioBuffer {
    /// Build a buffer from a rate and its samples.
    pub fn new(sample_rate: u32, samples: Vec<f32>) -> Self {
        Self {
            sample_rate,
            samples,
        }
    }

    /// Duration in seconds, derived from the sample count and rate.
    ///
    /// # Examples
    /// ```
    /// use nh_core::model::AudioBuffer;
    /// let buf = AudioBuffer::new(16_000, vec![0.0; 8_000]);
    /// assert!((buf.duration_s() - 0.5).abs() < 1e-9);
    /// ```
    #[must_use]
    pub fn duration_s(&self) -> f64 {
        // Guard against a zero rate so a malformed buffer reports 0 rather than dividing
        // by zero and yielding a NaN/inf that would poison downstream metrics.
        if self.sample_rate == 0 {
            return 0.0;
        }
        self.samples.len() as f64 / f64::from(self.sample_rate)
    }

    /// Whether the buffer carries no samples.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.samples.is_empty()
    }

    /// Extract the sub-buffer spanning `[t0_s, t1_s)` seconds (clamped to the buffer).
    ///
    /// Used by the diarize-then-ASR pipeline to hand each speaker turn to the ASR engine.
    /// Out-of-range or inverted bounds yield an empty buffer rather than panicking.
    ///
    /// # Examples
    /// ```
    /// use nh_core::model::AudioBuffer;
    /// let buf = AudioBuffer::new(16_000, (0..16_000).map(|i| i as f32).collect());
    /// let mid = buf.slice(0.25, 0.75);
    /// assert_eq!(mid.samples.len(), 8_000);
    /// ```
    #[must_use]
    pub fn slice(&self, t0_s: f64, t1_s: f64) -> AudioBuffer {
        // Convert the second bounds to sample indices, clamping into the valid range.
        let rate = f64::from(self.sample_rate);
        let start = (t0_s.max(0.0) * rate) as usize;
        let start = start.min(self.samples.len());
        let end = (t1_s.max(0.0) * rate) as usize;
        // Never let `end` fall below `start` (inverted spans) or past the buffer.
        let end = end.clamp(start, self.samples.len());
        AudioBuffer::new(self.sample_rate, self.samples[start..end].to_vec())
    }
}

/// A single live PCM frame (used by the online/streaming path, M4).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PcmFrame {
    /// Absolute timestamp of the frame start, in seconds since session start.
    pub t_abs_s: f64,
    /// Sample rate in Hz.
    pub sample_rate: u32,
    /// Mono PCM samples for this frame.
    pub samples: Vec<f32>,
}

/// One recognized word with its time span (word-level timestamps when the ASR backend
/// provides them).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Word {
    /// Start time in seconds.
    pub t0: f64,
    /// End time in seconds.
    pub t1: f64,
    /// The word text.
    pub text: String,
}

/// One diarized, transcribed utterance: who spoke, when, and what.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Utterance {
    /// Start time in seconds since session start.
    pub t0: f64,
    /// End time in seconds since session start.
    pub t1: f64,
    /// The speaker this utterance is attributed to.
    pub speaker: SpeakerId,
    /// The transcribed text.
    pub text: String,
    /// Optional word-level timing (empty when the backend does not emit it).
    pub words: Vec<Word>,
    /// Detected language (ISO-639-1) when known.
    pub language: Option<String>,
}

/// A diarized speaker turn: a time span attributed to one speaker, before ASR.
///
/// The diarization engine emits these; the diarize-then-ASR pipeline transcribes the
/// audio under each one to produce the final [`Utterance`]s.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DiarizedSegment {
    /// Start time in seconds since session start.
    pub t0: f64,
    /// End time in seconds since session start.
    pub t1: f64,
    /// The speaker this turn is attributed to.
    pub speaker: SpeakerId,
}

/// A diarized speaker within a session, optionally linked to an enrolled [`Person`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Speaker {
    /// The diarization label for this speaker.
    pub id: SpeakerId,
    /// The enrolled person behind this voice, once known ("name once").
    pub person: Option<PersonId>,
    /// Total time this speaker held the floor, in seconds (drives the pie chart).
    pub speaking_time_s: f64,
}

impl Speaker {
    /// Create a speaker with no linked person and zero accumulated speaking time.
    pub fn new(id: SpeakerId) -> Self {
        Self {
            id,
            person: None,
            speaking_time_s: 0.0,
        }
    }
}

/// An enrolled person: the "who" that a voice maps to, with the context the user
/// supplied (name, role, and an optional photo path extracted from vCard/site/PDF).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Person {
    /// Stable id.
    pub id: PersonId,
    /// Display name.
    pub name: String,
    /// Optional role/title.
    pub role: Option<String>,
    /// Optional local path to a photo, extracted from user-provided sources only.
    pub photo_path: Option<String>,
}

/// Plutchik's eight primary emotions, used for per-speaker and global emotion charts.
///
/// Kept as a closed enum so the emotion analyzers (LLM-on-text and audio SER) and the
/// renderer share one vocabulary; intensity is carried alongside where needed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Emotion {
    /// Joy.
    Joy,
    /// Trust.
    Trust,
    /// Fear.
    Fear,
    /// Surprise.
    Surprise,
    /// Sadness.
    Sadness,
    /// Disgust.
    Disgust,
    /// Anger.
    Anger,
    /// Anticipation.
    Anticipation,
}

/// An action item extracted from the discussion (who does what, by when).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Action {
    /// What is to be done.
    pub description: String,
    /// Who owns it, when attributable to a speaker.
    pub assignee: Option<SpeakerId>,
    /// Free-form due date as stated (normalized later).
    pub due: Option<String>,
}

/// A chapter marker: a timestamp and a short title for navigation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Chapter {
    /// Chapter start in seconds.
    pub t0: f64,
    /// Short chapter title.
    pub title: String,
}

/// A notable verbatim quote, attributed and timestamped.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Quote {
    /// The speaker quoted.
    pub speaker: SpeakerId,
    /// The quoted text.
    pub text: String,
    /// When it was said, in seconds.
    pub t0: f64,
}

/// The local-LLM synthesis of a discussion. Every field is grounded in the transcript.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Summary {
    /// One-paragraph executive overview.
    pub overview: String,
    /// Key points.
    pub key_points: Vec<String>,
    /// Decisions taken.
    pub decisions: Vec<String>,
    /// Action items.
    pub actions: Vec<Action>,
    /// Chapter markers.
    pub chapters: Vec<Chapter>,
    /// Notable quotes.
    pub quotes: Vec<Quote>,
}

impl Summary {
    /// An empty summary — the starting point a synthesizer fills in.
    #[must_use]
    pub fn empty() -> Self {
        Self {
            overview: String::new(),
            key_points: Vec::new(),
            decisions: Vec::new(),
            actions: Vec::new(),
            chapters: Vec::new(),
            quotes: Vec::new(),
        }
    }

    /// Whether the summary is entirely empty (no overview and no lists).
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.overview.is_empty()
            && self.key_points.is_empty()
            && self.decisions.is_empty()
            && self.actions.is_empty()
            && self.chapters.is_empty()
            && self.quotes.is_empty()
    }
}

/// Optional user-provided context that enriches synthesis (people, free notes).
///
/// This is the structured home of the "meeting context" input and the people forms
/// (manual entry, vCard/CSV, LinkedIn PDF, user-supplied URLs — never scraped).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MeetingContext {
    /// Free-form notes about the discussion.
    pub notes: String,
    /// Known people, used to pre-seed identity and to ground the summary.
    pub people: Vec<Person>,
}

impl MeetingContext {
    /// An empty context (no notes, no people).
    #[must_use]
    pub fn empty() -> Self {
        Self {
            notes: String::new(),
            people: Vec::new(),
        }
    }
}

/// A diarized, transcribed conversation: the utterances and the speakers in them.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Transcript {
    /// The utterances in time order.
    pub utterances: Vec<Utterance>,
    /// The distinct speakers referenced by the utterances.
    pub speakers: Vec<Speaker>,
}

/// The finished report: the single structured object every output renders from
/// (HTML notes-helper-style, PDF via WebView, DOCX via docx-rs).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Report {
    /// The session this report describes.
    pub session_id: SessionId,
    /// A human-facing title.
    pub title: String,
    /// The context that was supplied for synthesis.
    pub context: MeetingContext,
    /// The diarized transcript.
    pub transcript: Transcript,
    /// The local-LLM synthesis.
    pub summary: Summary,
}
