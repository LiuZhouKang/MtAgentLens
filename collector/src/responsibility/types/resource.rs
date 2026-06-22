use serde::{Deserialize, Serialize};
use super::ids::{ProcessId, MessageId};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum NormalizedResource {
    File { canonical_path: String },
    Network { host: String, port: u16, scheme: String },
    Process { process_id: ProcessId },
    LlmConversation { provider: String, model: String, message_id: MessageId },
    ToolCall { tool_name: String, call_id: String },
    Credential { credential_id: String },
    Context { context_id: String },
}
