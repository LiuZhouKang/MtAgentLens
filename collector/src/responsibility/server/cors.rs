// SPDX-License-Identifier: Apache-2.0 OR MIT
// CORS headers and preflight handling for the server module.

use hyper::{Method, Request, Response, body::Bytes};

/// Headers to inject into every API response.
pub const CORS_HEADERS: &[(&str, &str)] = &[
    ("access-control-allow-origin", "*"),
    ("access-control-allow-methods", "GET, POST, OPTIONS"),
    ("access-control-allow-headers", "content-type"),
];

/// Inject CORS headers into a `Response<B>` for any body type.
pub fn with_cors_headers<B>(resp: &mut Response<B>) {
    let headers = resp.headers_mut();
    for &(name, value) in CORS_HEADERS {
        headers.insert(
            hyper::header::HeaderName::from_static(name),
            hyper::header::HeaderValue::from_static(value),
        );
    }
}

/// Handle an OPTIONS preflight request. Returns an empty 200 with CORS headers.
pub fn handle_preflight<T>(_req: &Request<T>) -> Response<Bytes> {
    let mut resp = Response::new(Bytes::new());
    with_cors_headers(&mut resp);
    *resp.status_mut() = hyper::StatusCode::OK;
    resp
}

/// Returns true if the request method is OPTIONS (preflight).
pub fn is_preflight(req: &Request<impl hyper::body::Body>) -> bool {
    req.method() == Method::OPTIONS
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cors_headers_applied() {
        let mut resp = Response::new(Bytes::from("ok"));
        with_cors_headers(&mut resp);
        let h = resp.headers();
        assert_eq!(h.get("access-control-allow-origin").unwrap(), "*");
        assert_eq!(
            h.get("access-control-allow-methods").unwrap(),
            "GET, POST, OPTIONS"
        );
    }

    #[test]
    fn test_preflight_returns_200() {
        let req = Request::builder()
            .method(Method::OPTIONS)
            .uri("/api/runs")
            .body(Bytes::new())
            .unwrap();
        let resp = handle_preflight(&req);
        assert_eq!(resp.status(), hyper::StatusCode::OK);
        assert!(resp.body().is_empty());
    }
}
