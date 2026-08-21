// SPDX-License-Identifier: MIT

use serde::Serialize;
use std::fs::{File, OpenOptions};
use std::io::Write;

pub struct JsonlWriter {
    file: File,
}

impl JsonlWriter {
    pub fn new(path: &str) -> std::io::Result<Self> {
        let file = OpenOptions::new().create(true).append(true).open(path)?;
        Ok(Self { file })
    }

    pub fn write_line<T: Serialize>(&mut self, value: &T) -> std::io::Result<()> {
        let line = serde_json::to_string(value)?;
        writeln!(self.file, "{}", line)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use tempfile::NamedTempFile;

    #[test]
    fn writes_jsonl_lines() {
        let temp = NamedTempFile::new().unwrap();
        let path = temp.path().to_string_lossy().to_string();
        let mut writer = JsonlWriter::new(&path).unwrap();

        writer
            .write_line(&json!({"type": "intent", "value": 1}))
            .unwrap();
        writer
            .write_line(&json!({"type": "link", "value": 2}))
            .unwrap();

        let content = std::fs::read_to_string(path).unwrap();
        let lines: Vec<&str> = content.lines().collect();
        assert_eq!(lines.len(), 2);
        assert!(lines[0].contains("\"intent\""));
        assert!(lines[1].contains("\"link\""));
    }
}
