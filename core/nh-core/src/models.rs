//! Model provisioning: fetch, verify, cache, and tier-select the on-device model files.
//!
//! Module summary
//! --------------
//! Every engine (whisper.cpp for ASR, sherpa-onnx for diarization, llama.cpp for
//! synthesis) needs weights on disk before it can run. This module is the backbone that
//! puts them there: it downloads a file (through an injected [`Downloader`] so the core
//! stays network-agnostic), **verifies its sha256** against a manifest before trusting
//! it, caches it locally, and picks the right file for the device [`ModelTier`] (heavy on
//! desktop, light on mobile — TECHNICAL_QUESTIONS Q11).
//!
//! Sovereignty note: the only outbound connection here is the one-time model download
//! from the configured source (Warith's FTP). It carries no user data — it is app-asset
//! provisioning, distinct from the opt-in sharing that targets the user's own infra.

use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::error::{CoreError, Result};
use crate::ports::ModelProvider;

/// Device capability tier: heavy models on desktop, light on mobile (Q11).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ModelTier {
    /// Full-size models (macOS/Linux/Windows).
    Desktop,
    /// Reduced models sized to run on phones (iOS/Android).
    Mobile,
}

/// What a model file is used for, so a manifest can carry several roles at once.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ModelRole {
    /// Automatic speech recognition (whisper.cpp).
    Asr,
    /// Local large-language-model synthesis (llama.cpp).
    Llm,
    /// Diarization segmentation model (sherpa-onnx).
    DiarSegmentation,
    /// Speaker-embedding model (sherpa-onnx).
    DiarEmbedding,
    /// Speech-emotion-recognition model (Plutchik, audio path).
    Ser,
}

/// One provisionable model file: where to get it, how to verify it, which tier it serves.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelSpec {
    /// Cache file name (stable, used as the on-disk key).
    pub name: String,
    /// What this model is for.
    pub role: ModelRole,
    /// Which device tier this file targets.
    pub tier: ModelTier,
    /// Fetch URL (on the configured model host, e.g. Warith's FTP).
    pub url: String,
    /// Expected sha256, lowercase hex — the integrity gate before trusting the file.
    pub sha256: String,
    /// Expected size in bytes (informational; UI progress, sanity check).
    pub size_bytes: u64,
}

/// A catalog of model files, loaded from a manifest published next to the models.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct ModelManifest {
    /// All known model files across roles and tiers.
    pub models: Vec<ModelSpec>,
}

impl ModelManifest {
    /// Parse a manifest from JSON bytes (as fetched from the model host).
    ///
    /// # Errors
    /// Returns [`CoreError::Model`] if the bytes are not a valid manifest.
    pub fn from_json(bytes: &[u8]) -> Result<Self> {
        // Wrap serde's error in our typed error so callers get a uniform surface.
        serde_json::from_slice(bytes)
            .map_err(|e| CoreError::Model(format!("manifest parse failed: {e}")))
    }

    /// Find the model serving `role` at `tier`, if the manifest declares one.
    #[must_use]
    pub fn select(&self, role: ModelRole, tier: ModelTier) -> Option<&ModelSpec> {
        // First exact match wins; a manifest should not declare duplicates for a pair.
        self.models
            .iter()
            .find(|m| m.role == role && m.tier == tier)
    }

    /// Find a model by its cache name.
    #[must_use]
    pub fn by_name(&self, name: &str) -> Option<&ModelSpec> {
        self.models.iter().find(|m| m.name == name)
    }
}

/// Fetches raw bytes for a model URL.
///
/// The adapter (reqwest/FTP) lives outside the core; tests inject a mock so the whole
/// provisioning path is verified with no network access.
pub trait Downloader {
    /// Fetch the full contents of `url`.
    ///
    /// # Errors
    /// Returns [`CoreError::Model`] if the transfer fails.
    fn fetch(&self, url: &str) -> Result<Vec<u8>>;
}

/// Compute the lowercase-hex sha256 of a byte slice.
///
/// Kept private and tiny; it is the single point where integrity is judged, so the
/// hashing choice is centralized here rather than sprinkled across call sites.
fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hex::encode(hasher.finalize())
}

/// Provisions model files into a local cache directory, verifying integrity and picking
/// the device tier.
///
/// Generic over the [`Downloader`] so tests run entirely offline and production wires a
/// real HTTP/FTP client.
pub struct ModelManager<D: Downloader> {
    /// The catalog of available models.
    manifest: ModelManifest,
    /// The tier this device should provision.
    tier: ModelTier,
    /// Directory where verified model files are cached.
    cache_dir: PathBuf,
    /// The injected transport used to fetch missing files.
    downloader: D,
}

impl<D: Downloader> ModelManager<D> {
    /// Build a manager for a manifest, tier, cache directory, and transport.
    pub fn new(
        manifest: ModelManifest,
        tier: ModelTier,
        cache_dir: impl Into<PathBuf>,
        downloader: D,
    ) -> Self {
        Self {
            manifest,
            tier,
            cache_dir: cache_dir.into(),
            downloader,
        }
    }

    /// Ensure the model serving `role` at this manager's tier is present locally, and
    /// return its path — downloading and verifying it on a cache miss.
    ///
    /// # Errors
    /// Returns [`CoreError::Model`] if the manifest has no such model, the download
    /// fails, or the sha256 does not match.
    pub fn ensure_role(&self, role: ModelRole) -> Result<PathBuf> {
        // Resolve the concrete file for (role, tier) from the manifest first.
        let spec = self.manifest.select(role, self.tier).ok_or_else(|| {
            CoreError::Model(format!("no model for {role:?} at tier {:?}", self.tier))
        })?;
        self.ensure_spec(spec)
    }

    /// Ensure a specific [`ModelSpec`] is present and verified locally; return its path.
    ///
    /// # Errors
    /// Returns [`CoreError::Model`] on download failure or checksum mismatch.
    fn ensure_spec(&self, spec: &ModelSpec) -> Result<PathBuf> {
        let path = self.cache_dir.join(&spec.name);

        // Cache hit: a present file whose hash matches is trusted and reused, so we never
        // re-download a good model. A mismatch means a partial/corrupt cache — fall
        // through and re-fetch rather than hand back bad weights.
        if path.is_file() {
            let cached = fs::read(&path)
                .map_err(|e| CoreError::Model(format!("read cache {}: {e}", path.display())))?;
            if sha256_hex(&cached) == spec.sha256 {
                return Ok(path);
            }
        }

        // Cache miss (or corrupt): fetch the bytes through the injected transport.
        let bytes = self.downloader.fetch(&spec.url)?;

        // Integrity gate: refuse anything whose digest does not match the manifest. This
        // is what lets us trust an over-the-network file as if it were bundled.
        let got = sha256_hex(&bytes);
        if got != spec.sha256 {
            return Err(CoreError::Model(format!(
                "hash mismatch for {}: expected {}, got {got}",
                spec.name, spec.sha256
            )));
        }

        // Make sure the cache directory exists before writing into it.
        fs::create_dir_all(&self.cache_dir).map_err(|e| {
            CoreError::Model(format!(
                "create cache dir {}: {e}",
                self.cache_dir.display()
            ))
        })?;

        // Write atomically: stage to a `.part` file then rename, so a crash mid-write can
        // never leave a truncated file that later passes the existence check.
        let tmp = self.cache_dir.join(format!("{}.part", spec.name));
        fs::write(&tmp, &bytes)
            .map_err(|e| CoreError::Model(format!("write {}: {e}", tmp.display())))?;
        fs::rename(&tmp, &path)
            .map_err(|e| CoreError::Model(format!("finalize {}: {e}", path.display())))?;

        Ok(path)
    }
}

// Wire the manager into the architecture's `ModelProvider` port so the pipeline can ask
// for a model by name without knowing how provisioning works.
impl<D: Downloader> ModelProvider for ModelManager<D> {
    fn ensure(&self, name: &str) -> Result<String> {
        // Resolve the name against the manifest, then provision as usual.
        let spec = self
            .manifest
            .by_name(name)
            .ok_or_else(|| CoreError::Model(format!("unknown model {name}")))?
            .clone();
        Ok(self.ensure_spec(&spec)?.display().to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;
    use std::rc::Rc;

    /// A downloader that returns canned bytes and counts how many times it was hit, so
    /// tests can assert the cache actually prevents re-downloads.
    struct MockDownloader {
        bytes: Vec<u8>,
        calls: Rc<Cell<u32>>,
    }

    impl Downloader for MockDownloader {
        fn fetch(&self, _url: &str) -> Result<Vec<u8>> {
            // Record the hit and hand back the canned payload.
            self.calls.set(self.calls.get() + 1);
            Ok(self.bytes.clone())
        }
    }

    /// Build a spec whose checksum matches `bytes`, for a given role and tier.
    fn spec_for(name: &str, bytes: &[u8], role: ModelRole, tier: ModelTier) -> ModelSpec {
        ModelSpec {
            name: name.to_string(),
            role,
            tier,
            url: format!("ftp://example/{name}"),
            sha256: sha256_hex(bytes),
            size_bytes: bytes.len() as u64,
        }
    }

    #[test]
    fn ensures_then_serves_from_cache() {
        // A valid spec + matching bytes should download once, then be served from cache.
        let bytes = b"pretend-model-weights".to_vec();
        let manifest = ModelManifest {
            models: vec![spec_for(
                "asr.bin",
                &bytes,
                ModelRole::Asr,
                ModelTier::Desktop,
            )],
        };
        let dir = tempfile::tempdir().expect("tempdir");
        let calls = Rc::new(Cell::new(0));
        let mgr = ModelManager::new(
            manifest,
            ModelTier::Desktop,
            dir.path(),
            MockDownloader {
                bytes: bytes.clone(),
                calls: Rc::clone(&calls),
            },
        );

        // First call downloads and caches the file.
        let p1 = mgr.ensure_role(ModelRole::Asr).expect("first ensure");
        assert!(p1.is_file());
        // Second call must hit the cache — the downloader is not called again.
        let p2 = mgr.ensure_role(ModelRole::Asr).expect("second ensure");
        assert_eq!(p1, p2);
        assert_eq!(calls.get(), 1, "cache should prevent a second download");
    }

    #[test]
    fn rejects_hash_mismatch() {
        // The manifest claims a checksum that the delivered bytes do not satisfy.
        let manifest = ModelManifest {
            models: vec![ModelSpec {
                name: "tampered.bin".to_string(),
                role: ModelRole::Llm,
                tier: ModelTier::Mobile,
                url: "ftp://example/tampered.bin".to_string(),
                sha256: sha256_hex(b"the-expected-bytes"),
                size_bytes: 42,
            }],
        };
        let dir = tempfile::tempdir().expect("tempdir");
        let mgr = ModelManager::new(
            manifest,
            ModelTier::Mobile,
            dir.path(),
            MockDownloader {
                bytes: b"WRONG-bytes".to_vec(),
                calls: Rc::new(Cell::new(0)),
            },
        );

        // Provisioning must fail closed rather than cache untrusted weights.
        let err = mgr.ensure_role(ModelRole::Llm).unwrap_err();
        assert!(matches!(err, CoreError::Model(_)));
    }

    #[test]
    fn selects_by_tier() {
        // Same role, two tiers: the manager must pick the file for its own tier.
        let desk = b"desktop-weights".to_vec();
        let mob = b"mobile-weights".to_vec();
        let manifest = ModelManifest {
            models: vec![
                spec_for("asr-desktop.bin", &desk, ModelRole::Asr, ModelTier::Desktop),
                spec_for("asr-mobile.bin", &mob, ModelRole::Asr, ModelTier::Mobile),
            ],
        };
        let dir = tempfile::tempdir().expect("tempdir");
        let mgr = ModelManager::new(
            manifest,
            ModelTier::Mobile,
            dir.path(),
            MockDownloader {
                bytes: mob.clone(),
                calls: Rc::new(Cell::new(0)),
            },
        );

        // The mobile tier resolves to the mobile file name.
        let path = mgr.ensure_role(ModelRole::Asr).expect("ensure mobile");
        assert!(path.ends_with("asr-mobile.bin"));
    }

    #[test]
    fn manifest_round_trips_through_json() {
        // The manifest is fetched as JSON from the model host, so parsing must hold.
        let manifest = ModelManifest {
            models: vec![spec_for(
                "m.bin",
                b"x",
                ModelRole::DiarEmbedding,
                ModelTier::Desktop,
            )],
        };
        let json = serde_json::to_vec(&manifest).expect("serialize manifest");
        let parsed = ModelManifest::from_json(&json).expect("parse manifest");
        assert_eq!(parsed, manifest);
    }
}
