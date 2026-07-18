//! Integration tests for the WAV audio-source adapter: write real WAV files with hound,
//! load them back through the port, and assert the decode/downmix/rate rules hold.

use nh_core::model::PIPELINE_SAMPLE_RATE;
use nh_core::ports::AudioSource;
use nh_io::WavFileSource;

/// Helper: write an interleaved i16 WAV to a temp path and return that path.
fn write_wav(
    dir: &std::path::Path,
    name: &str,
    channels: u16,
    rate: u32,
    samples: &[i16],
) -> std::path::PathBuf {
    let path = dir.join(name);
    let spec = hound::WavSpec {
        channels,
        sample_rate: rate,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut writer = hound::WavWriter::create(&path, spec).expect("create wav");
    for &s in samples {
        writer.write_sample(s).expect("write sample");
    }
    writer.finalize().expect("finalize wav");
    path
}

#[test]
fn loads_mono_16k_wav() {
    // A short 16 kHz mono file should load to exactly that many samples at 16 kHz.
    let dir = tempfile::tempdir().expect("tempdir");
    let samples = vec![0i16; 8_000]; // 0.5 s of silence
    let path = write_wav(dir.path(), "mono.wav", 1, PIPELINE_SAMPLE_RATE, &samples);

    let buf = WavFileSource::new(path).load().expect("load mono wav");
    assert_eq!(buf.sample_rate, PIPELINE_SAMPLE_RATE);
    assert_eq!(buf.samples.len(), 8_000);
}

#[test]
fn downmixes_stereo_to_mono() {
    // A stereo file has twice as many interleaved samples; after downmix the mono buffer
    // has half as many, and each mono sample is the average of the L/R pair.
    let dir = tempfile::tempdir().expect("tempdir");
    // Two frames: (L=1.0-ish, R=-1.0-ish) then (L=full, R=full). Use i16 extremes.
    let interleaved = vec![i16::MAX, i16::MIN, i16::MAX, i16::MAX];
    let path = write_wav(
        dir.path(),
        "stereo.wav",
        2,
        PIPELINE_SAMPLE_RATE,
        &interleaved,
    );

    let buf = WavFileSource::new(path).load().expect("load stereo wav");
    assert_eq!(
        buf.samples.len(),
        2,
        "stereo should collapse to one channel"
    );
    // Frame 1 averages +full and -full → ~0.0; frame 2 averages +full and +full → ~+1.0.
    assert!(
        buf.samples[0].abs() < 0.01,
        "opposite channels should cancel"
    );
    assert!(
        buf.samples[1] > 0.9,
        "equal max channels should stay near full scale"
    );
}

#[test]
fn rejects_wrong_sample_rate() {
    // A 44.1 kHz file must be refused rather than silently mis-fed to the engines.
    let dir = tempfile::tempdir().expect("tempdir");
    let path = write_wav(dir.path(), "hi.wav", 1, 44_100, &[0i16; 100]);

    let err = WavFileSource::new(path).load().unwrap_err();
    let msg = format!("{err}");
    assert!(
        msg.contains("resampling not wired"),
        "unexpected error: {msg}"
    );
}
