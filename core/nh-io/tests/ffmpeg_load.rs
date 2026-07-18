//! Integration tests for the ffmpeg audio-source adapter. These require `ffmpeg` on
//! PATH (a documented build dependency); they prove that arbitrary formats and sample
//! rates are decoded and resampled to the canonical 16 kHz mono buffer.

use nh_core::model::PIPELINE_SAMPLE_RATE;
use nh_core::ports::AudioSource;
use nh_io::FfmpegSource;

/// Write a 44.1 kHz stereo i16 WAV of `secs` seconds of silence and return its path.
///
/// We start from a non-canonical rate and channel count on purpose, so a successful
/// decode proves ffmpeg both resampled (44.1 kHz → 16 kHz) and down-mixed (stereo → mono).
fn write_441_stereo(dir: &std::path::Path, secs: u32) -> std::path::PathBuf {
    let path = dir.join("input.wav");
    let spec = hound::WavSpec {
        channels: 2,
        sample_rate: 44_100,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut writer = hound::WavWriter::create(&path, spec).expect("create wav");
    // Interleaved stereo silence: 2 samples per frame, 44_100 frames per second.
    for _ in 0..(secs * 44_100) {
        writer.write_sample(0i16).expect("L");
        writer.write_sample(0i16).expect("R");
    }
    writer.finalize().expect("finalize");
    path
}

#[test]
fn decodes_and_resamples_to_16k_mono() {
    // Build a 1-second 44.1 kHz stereo input and decode it through ffmpeg.
    let dir = tempfile::tempdir().expect("tempdir");
    let input = write_441_stereo(dir.path(), 1);

    let buf = FfmpegSource::new(input).load().expect("ffmpeg decode");

    // The output must be at the canonical rate regardless of the input rate.
    assert_eq!(buf.sample_rate, PIPELINE_SAMPLE_RATE);
    // One second resampled to 16 kHz is ~16_000 samples; allow a small codec-edge margin.
    let len = buf.samples.len() as i64;
    assert!(
        (len - 16_000).abs() < 500,
        "expected ~16000 samples for 1s @ 16kHz, got {len}"
    );
}
