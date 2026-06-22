/// API contract: Frontend → Backend handoff for development task execution.
///
/// This contract defines the interface between the task runner UI (frontend)
/// and the task execution engine (backend). The frontend triggers task lifecycle
/// events; the backend executes and returns structured results.

// --- Task lifecycle endpoints ---

/// POST /api/task/start — 前端发起任务
pub const API_TASK_START: &str = "/api/task/start";
/// POST /api/task/complete — 前端通知任务完成
pub const API_TASK_COMPLETE: &str = "/api/task/complete";

// --- Task status codes ---

pub const TASK_STATUS_STARTED: &str = "started";
pub const TASK_STATUS_COMPLETED: &str = "completed";
pub const TASK_STATUS_FAILED: &str = "failed";

// --- Response keys (JSON fields shared between frontend and backend) ---

/// task_id — 任务唯一标识
pub const KEY_TASK_ID: &str = "task_id";
/// status — 任务状态
pub const KEY_STATUS: &str = "status";
/// user — 执行者
pub const KEY_USER: &str = "user";
/// working_dir — 工作目录
pub const KEY_WORKING_DIR: &str = "working_dir";
/// timestamp — 时间戳 (ISO 8601)
pub const KEY_TIMESTAMP: &str = "timestamp";
/// message — 附加信息
pub const KEY_MESSAGE: &str = "message";

// --- Contract version ---

pub const HANDOFF_CONTRACT_VERSION: &str = "0.1.0";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn handoff_contract_constant_values_are_stable() {
        assert_eq!(API_TASK_START, "/api/task/start");
        assert_eq!(API_TASK_COMPLETE, "/api/task/complete");
        assert_eq!(TASK_STATUS_STARTED, "started");
        assert_eq!(TASK_STATUS_COMPLETED, "completed");
        assert_eq!(TASK_STATUS_FAILED, "failed");
        assert_eq!(KEY_TASK_ID, "task_id");
        assert_eq!(KEY_STATUS, "status");
        assert_eq!(KEY_USER, "user");
        assert_eq!(KEY_WORKING_DIR, "working_dir");
        assert_eq!(KEY_TIMESTAMP, "timestamp");
        assert_eq!(KEY_MESSAGE, "message");
        assert_eq!(HANDOFF_CONTRACT_VERSION, "0.1.0");
    }
}
