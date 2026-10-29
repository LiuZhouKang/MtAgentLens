// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 eunomia-bpf org.

use mime_guess::from_path;
use rust_embed::RustEmbed;
use std::borrow::Cow;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(RustEmbed)]
#[folder = "../frontend/out/"]
pub struct FrontendDist;

pub struct FrontendAssets {
    serve_dir: PathBuf,
    /// Whether we own the directory (temp extraction) and should clean it up on drop.
    owned: bool,
}

impl FrontendAssets {
    pub fn new() -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        // Dev mode: serve directly from a disk directory if env var is set
        if let Ok(dist_path) = std::env::var("MTAGENTLENS_FRONTEND_DIST") {
            let dir = PathBuf::from(&dist_path);
            if !dir.join("index.html").exists() {
                return Err(format!(
                    "MTAGENTLENS_FRONTEND_DIST={} does not contain index.html",
                    dist_path
                )
                .into());
            }
            log::info!("📁 Dev mode: serving frontend from disk: {}", dir.display());
            return Ok(Self {
                serve_dir: dir,
                owned: false,
            });
        }

        let temp_dir =
            std::env::temp_dir().join(format!("mtagentlens-frontend-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&temp_dir)?;

        // Extract all embedded assets to temp directory
        for file_path in FrontendDist::iter() {
            if let Some(content) = FrontendDist::get(&file_path) {
                let full_path = temp_dir.join(&*file_path);
                if let Some(parent) = full_path.parent() {
                    fs::create_dir_all(parent)?;
                }
                fs::write(&full_path, &content.data)?;
            }
        }

        log::info!("📁 Extracted frontend assets to: {}", temp_dir.display());
        Ok(Self {
            serve_dir: temp_dir,
            owned: true,
        })
    }

    fn resolve_path(&self, path: &str) -> Option<PathBuf> {
        let normalized_path = path.strip_prefix('/').unwrap_or(path);

        // A URL path is not a filesystem path. `..` is never a valid asset
        // segment: without this check `GET /../../etc/passwd` escaped the
        // serve directory and read arbitrary files as the collector user
        // (usually root).
        if normalized_path
            .split('/')
            .any(|segment| segment == ".." || segment == "." || segment.contains('\\'))
        {
            return None;
        }

        let candidates = if path == "/" || path == "/index.html" {
            vec![self.serve_dir.join("index.html")]
        } else {
            let mut candidates = vec![self.serve_dir.join(normalized_path)];
            if !normalized_path.contains('.') {
                candidates.push(self.serve_dir.join(normalized_path).join("index.html"));
                candidates.push(self.serve_dir.join(format!("{normalized_path}.html")));
            }
            candidates
        };

        let root = self.serve_dir.canonicalize().ok()?;
        candidates.into_iter().find_map(|candidate| {
            if !candidate.is_file() {
                return None;
            }
            let canonical = candidate.canonicalize().ok()?;
            // Symlinks inside the extracted tree must not point outside it.
            canonical.starts_with(&root).then_some(canonical)
        })
    }

    /// Get any asset by path from the serve directory
    pub fn get(&self, path: &str) -> Option<Cow<'static, [u8]>> {
        let file_path = self.resolve_path(path)?;
        fs::read(&file_path).ok().map(Cow::Owned)
    }

    /// Get MIME type for a file path
    pub fn get_content_type(&self, path: &str) -> String {
        if let Some(file_path) = self.resolve_path(path) {
            return from_path(file_path).first_or_octet_stream().to_string();
        }

        let file_path = if path == "/" || path == "/index.html" {
            "index.html"
        } else {
            path.strip_prefix('/').unwrap_or(path)
        };

        from_path(file_path).first_or_octet_stream().to_string()
    }

    /// List all available assets
    pub fn list_all_assets(&self) -> Vec<String> {
        if self.owned {
            // Embedded mode: use RustEmbed iterator
            FrontendDist::iter().map(|s| s.to_string()).collect()
        } else {
            // Dev mode: walk the disk directory
            let mut files = Vec::new();
            if let Ok(entries) = walkdir(&self.serve_dir, &self.serve_dir) {
                files = entries;
            }
            files
        }
    }
}

impl Drop for FrontendAssets {
    fn drop(&mut self) {
        if !self.owned {
            return;
        }
        if self.serve_dir.exists() {
            if let Err(e) = fs::remove_dir_all(&self.serve_dir) {
                log::warn!(
                    "Failed to cleanup temp directory {}: {}",
                    self.serve_dir.display(),
                    e
                );
            } else {
                log::info!("🧹 Cleaned up temp directory: {}", self.serve_dir.display());
            }
        }
    }
}

/// Recursively list files under `dir`, returning paths relative to `root`.
fn walkdir(dir: &Path, root: &Path) -> std::io::Result<Vec<String>> {
    let mut result = Vec::new();
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        if path.is_dir() {
            result.extend(walkdir(&path, root)?);
        } else if let Ok(rel) = path.strip_prefix(root) {
            result.push(rel.to_string_lossy().into_owned());
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::FrontendAssets;
    use std::fs;
    use tempfile::TempDir;

    #[test]
    fn get_resolves_exported_subroute_directory_index() {
        let tempdir = TempDir::new().unwrap();
        fs::write(tempdir.path().join("index.html"), "root").unwrap();
        fs::create_dir_all(tempdir.path().join("alerts")).unwrap();
        fs::write(
            tempdir.path().join("alerts").join("index.html"),
            "alerts-page",
        )
        .unwrap();

        let assets = FrontendAssets {
            serve_dir: tempdir.path().to_path_buf(),
            owned: false,
        };
        let content = assets.get("/alerts").unwrap();
        let body = std::str::from_utf8(content.as_ref()).unwrap();
        assert_eq!(body, "alerts-page");
        assert_eq!(assets.get_content_type("/alerts"), "text/html");
    }

    #[test]
    fn get_rejects_parent_segments_and_symlink_escapes() {
        let tempdir = TempDir::new().unwrap();
        fs::write(tempdir.path().join("index.html"), "root").unwrap();
        let outside = TempDir::new().unwrap();
        fs::write(outside.path().join("secret.txt"), "secret").unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(
            outside.path().join("secret.txt"),
            tempdir.path().join("link.txt"),
        )
        .unwrap();

        let assets = FrontendAssets {
            serve_dir: tempdir.path().to_path_buf(),
            owned: false,
        };
        assert!(assets.get("/../../etc/passwd").is_none());
        assert!(assets.get("/./index.html").is_none());
        assert!(assets.get("/..\\..\\etc\\passwd").is_none());
        #[cfg(unix)]
        assert!(assets.get("/link.txt").is_none(), "symlink must not escape");
        assert!(assets.get("/index.html").is_some());
    }
}
