// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use super::ids::{MessageId, ProcessId};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum NormalizedResource {
    File {
        canonical_path: String,
    },
    Network {
        host: String,
        port: u16,
        scheme: String,
    },
    Process {
        process_id: ProcessId,
    },
    LlmConversation {
        provider: String,
        model: String,
        message_id: MessageId,
    },
    ToolCall {
        tool_name: String,
        call_id: String,
    },
    Credential {
        credential_id: String,
    },
    Context {
        context_id: String,
    },
}
