use std::sync::Mutex;
use async_trait::async_trait;

use crate::responsibility::classification::{ObserverInput, ObserverOutput};
use crate::responsibility::observer::provider::{ObserverProvider, ObserverProviderError};

pub struct MockObserverProvider {
    responses: Vec<ObserverOutput>,
    call_count: Mutex<usize>,
    error_on_call: Option<usize>,
}

impl MockObserverProvider {
    pub fn with_responses(responses: Vec<ObserverOutput>) -> Self {
        Self {
            responses,
            call_count: Mutex::new(0),
            error_on_call: None,
        }
    }

    #[allow(dead_code)]
    pub fn with_error_on_call(mut self, call_index: usize) -> Self {
        self.error_on_call = Some(call_index);
        self
    }

    pub fn call_count(&self) -> usize {
        *self.call_count.lock().unwrap()
    }
}

#[async_trait]
impl ObserverProvider for MockObserverProvider {
    async fn evaluate(
        &self,
        _input: &ObserverInput,
    ) -> Result<ObserverOutput, ObserverProviderError> {
        let mut count = self.call_count.lock().unwrap();
        let idx = *count;
        *count += 1;

        if self.error_on_call == Some(idx) {
            return Err(ObserverProviderError::ApiError("mock error".into()));
        }

        if let Some(resp) = self.responses.get(idx) {
            Ok(resp.clone())
        } else {
            self.responses.last().cloned().ok_or_else(|| {
                ObserverProviderError::ApiError("mock: no responses configured".into())
            })
        }
    }
}
