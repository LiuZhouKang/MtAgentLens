// SPDX-License-Identifier: Apache-2.0 OR MIT

use std::{
    env, fs,
    path::{Path, PathBuf},
};

fn source_files(directory: &Path, files: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(directory).expect("read collector sources") {
        let path = entry.expect("source directory entry").path();
        if path.is_dir() {
            source_files(&path, files);
        } else if path.extension().is_some_and(|extension| extension == "rs") {
            files.push(path);
        }
    }
}

fn main() {
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("manifest directory"));
    let mut files = Vec::new();
    source_files(&root.join("src"), &mut files);
    files.extend(["Cargo.toml", "Cargo.lock", "build.rs"].map(|name| root.join(name)));
    files.sort();
    // Hash the complete first-party collector and locked dependencies, rather
    // than maintaining an incomplete hand-written list of analysis callees.
    let mut source = String::from("const ANALYSIS_SOURCE_MANIFEST: &str = concat!(\n");
    println!("cargo:rerun-if-changed=src");
    for path in files {
        println!("cargo:rerun-if-changed={}", path.display());
        let relative = path.strip_prefix(&root).expect("source inside manifest");
        source.push_str(&format!(
            "{:?}, include_str!({:?}), \"\\n\",\n",
            format!("{}\n", relative.display()),
            path
        ));
    }
    source.push_str(");\n");
    fs::write(
        PathBuf::from(env::var_os("OUT_DIR").expect("build output directory"))
            .join("analysis_sources.rs"),
        source,
    )
    .expect("write frozen-analysis source manifest");
}
