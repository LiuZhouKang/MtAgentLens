// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 eunomia-bpf org.

use std::fs;
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use tempfile::TempDir;
use tokio::time::{Duration, sleep};

// BPF binaries from bpftools/.output/bin/
const PROCESS_BINARY: &[u8] = include_bytes!("../../../bpftools/.output/bin/process");
const TLS_CAPTURE_BINARY: &[u8] = include_bytes!("../../../bpftools/.output/bin/tls_capture");
// Previously `&[]` — an empty placeholder. `get_stdiocap_path()` then wrote a
// zero-byte "executable" and handed it to StdioRunner, so stdio capture never
// worked in production while appearing configured.
const STDIOCAP_BINARY: &[u8] = include_bytes!("../../../bpftools/.output/bin/stdiocap");

pub struct BinaryExtractor {
    _temp_dir: TempDir, // Keep alive to prevent cleanup
    pub process_path: PathBuf,
    pub tls_capture_path: PathBuf,
    stdiocap_init_lock: Mutex<()>,
    stdiocap_path: OnceLock<PathBuf>,
    verbose: bool,
}

impl BinaryExtractor {
    pub async fn new() -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        Self::new_with_verbosity(true).await
    }

    /// Extract without printing. Used by `mtagentlens doctor`, where the
    /// progress lines are noise.
    pub async fn new_quiet() -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        Self::new_with_verbosity(false).await
    }

    async fn new_with_verbosity(
        verbose: bool,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        if verbose {
            println!("Creating temporary directory...");
        }

        let temp_dir = TempDir::new()?;
        let temp_path = temp_dir.path();

        if verbose {
            println!("Created temporary directory: {}", temp_path.display());
        }

        // Extract and setup the process binary
        let process_path = temp_path.join("process");
        Self::extract_binary(&process_path, PROCESS_BINARY, "process", verbose).await?;

        // Extract and setup the tls_capture binary
        let tls_capture_path = temp_path.join("tls_capture");
        Self::extract_binary(
            &tls_capture_path,
            TLS_CAPTURE_BINARY,
            "tls_capture",
            verbose,
        )
        .await?;

        // Small delay to ensure files are fully written
        sleep(Duration::from_millis(100)).await;

        Ok(Self {
            _temp_dir: temp_dir,
            process_path,
            tls_capture_path,
            stdiocap_init_lock: Mutex::new(()),
            stdiocap_path: OnceLock::new(),
            verbose,
        })
    }

    async fn extract_binary(
        path: &Path,
        binary_data: &[u8],
        name: &str,
        verbose: bool,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        {
            let mut file = fs::File::create(path)?;
            file.write_all(binary_data)?;
            file.flush()?;
        } // File is closed here

        // Make the binary executable
        let mut perms = fs::metadata(path)?.permissions();
        perms.set_mode(0o755);
        fs::set_permissions(path, perms)?;

        if verbose {
            println!("Extracted {} binary to: {}", name, path.display());
        }

        Ok(())
    }

    pub fn get_process_path(&self) -> &Path {
        &self.process_path
    }

    pub fn get_tls_capture_path(&self) -> &Path {
        &self.tls_capture_path
    }

    pub fn get_stdiocap_path(&self) -> Result<&Path, Box<dyn std::error::Error + Send + Sync>> {
        if let Some(path) = self.stdiocap_path.get() {
            return Ok(path.as_path());
        }

        let _guard = self
            .stdiocap_init_lock
            .lock()
            .map_err(|_| std::io::Error::other("stdiocap extraction lock poisoned"))?;

        if let Some(path) = self.stdiocap_path.get() {
            return Ok(path.as_path());
        }

        let stdiocap_path = self._temp_dir.path().join("stdiocap");
        {
            let mut file = fs::File::create(&stdiocap_path)?;
            file.write_all(STDIOCAP_BINARY)?;
            file.flush()?;
        }

        let mut perms = fs::metadata(&stdiocap_path)?.permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&stdiocap_path, perms)?;
        if self.verbose {
            println!("Extracted stdiocap binary to: {}", stdiocap_path.display());
        }

        self.stdiocap_path
            .set(stdiocap_path)
            .map_err(|_| std::io::Error::other("stdiocap path initialized concurrently"))?;

        Ok(self
            .stdiocap_path
            .get()
            .expect("stdiocap path should be initialized")
            .as_path())
    }
}

#[cfg(test)]
mod tests {
    use super::TLS_CAPTURE_BINARY;
    use std::fs;
    use std::path::PathBuf;

    #[test]
    fn embedded_tls_capture_binary_matches_on_disk_fixture() {
        let on_disk_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("bpftools")
            .join(".output")
            .join("bin")
            .join("tls_capture");

        let on_disk_bytes = fs::read(&on_disk_path).unwrap_or_else(|error| {
            panic!(
                "failed to read on-disk tls_capture binary at {}: {error}",
                on_disk_path.display()
            )
        });

        assert_eq!(
            TLS_CAPTURE_BINARY,
            on_disk_bytes.as_slice(),
            "embedded tls_capture bytes differ from on-disk bpftools tls_capture; rebuild collector embeds or bpf artifact parity is stale"
        );
    }

    /// The stdiocap bytes must be the real binary, not an empty placeholder.
    ///
    /// `STDIOCAP_BINARY` used to be `&[]`, so `get_stdiocap_path()` wrote a
    /// zero-byte "executable" that StdioRunner then tried to run — the stdio
    /// capture path never actually worked in production.
    #[test]
    fn embedded_stdiocap_binary_is_not_empty() {
        assert!(
            !super::STDIOCAP_BINARY.is_empty(),
            "STDIOCAP_BINARY must contain the built stdiocap binary"
        );
        // An ELF executable starts with 0x7F 'E' 'L' 'F'.
        assert_eq!(
            &super::STDIOCAP_BINARY[..4],
            b"\x7fELF",
            "embedded stdiocap must be an ELF executable"
        );
    }

    #[test]
    fn embedded_stdiocap_binary_matches_on_disk_fixture() {
        let on_disk_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("bpftools")
            .join(".output")
            .join("bin")
            .join("stdiocap");
        let on_disk = fs::read(&on_disk_path)
            .unwrap_or_else(|e| panic!("failed to read {}: {e}", on_disk_path.display()));
        // Compare lengths first: a full `assert_eq!` on a multi-megabyte
        // binary would dump the entire blob into the failure message.
        assert_eq!(
            super::STDIOCAP_BINARY.len(),
            on_disk.len(),
            "embedded stdiocap length differs from bpftools output; rebuild bpftools or the embed is stale"
        );
        assert!(
            super::STDIOCAP_BINARY == on_disk.as_slice(),
            "embedded stdiocap bytes differ from bpftools output"
        );
    }
}
