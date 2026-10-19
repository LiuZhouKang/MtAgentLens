// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Out-of-band data sources: facts the agent wrote down itself.
//!
//! Enabled only via an explicit [`profile::OutOfBandProfile`]. The eBPF path
//! remains authoritative and independently usable; these sources add precision
//! where available and degrade to nothing where not.

pub mod openclaw;
pub mod profile;
pub mod types;

pub use openclaw::{OpenClawReader, ReadError};
pub use profile::{OutOfBandKind, OutOfBandProfile, ProfileError, load_profiles};
pub use types::{OutOfBandIdentity, OutOfBandToolCall};
