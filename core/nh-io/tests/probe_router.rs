//! Integration test: the duration probe feeds the diarization router.
//!
//! This is the composition the two toolbox translations exist to make real —
//! `nh_io::probe::probe_duration_s` reads a file's length, and
//! `nh_core::router::select_diarization` turns that length into a backend choice.
//! The test is robust to `ffprobe` being absent (as it may be on a minimal box):
//! either the probe succeeds and a short clip routes to NeMo, or it returns
//! `None` and the router falls to the robust long-form pyannote branch.

use nh_core::model::PIPELINE_SAMPLE_RATE;
use nh_core::router::{select_diarization, DiarBackend, DiarMode, DiarizationQuery};
use nh_io::probe::probe_duration_s;

/// Write a mono 16 kHz i16 WAV of `seconds` of silence to a temp path.
fn write_silence(dir: &std::path::Path, name: &str, seconds: u32) -> std::path::PathBuf {
    let path = dir.join(name);
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate: PIPELINE_SAMPLE_RATE,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut writer = hound::WavWriter::create(&path, spec).expect("create wav");
    for _ in 0..(PIPELINE_SAMPLE_RATE * seconds) {
        writer.write_sample(0i16).expect("write sample");
    }
    writer.finalize().expect("finalize wav");
    path
}

#[test]
fn probe_then_route_picks_a_backend_for_a_real_file() {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = write_silence(dir.path(), "clip.wav", 2); // a short, 2-second clip

    // A short, few-speaker clip: if we can read its length it belongs to NeMo
    // Sortformer; if ffprobe is unavailable the length is unknown and the router
    // must still return a runnable, offline plan (pyannote).
    match probe_duration_s(&path) {
        Some(d) => {
            assert!((1.5..3.0).contains(&d), "2 s clip probed as {d} s");
            let plan = select_diarization(DiarizationQuery {
                duration_s: Some(d),
                max_speakers: Some(2),
                ..Default::default()
            });
            assert_eq!(plan.mode, DiarMode::Offline);
            assert_eq!(plan.backend, DiarBackend::Nemo);
        }
        None => {
            let plan = select_diarization(DiarizationQuery {
                duration_s: None,
                ..Default::default()
            });
            assert_eq!(plan.backend, DiarBackend::Pyannote);
        }
    }
}
