"""Bundle naming helpers for the support kit."""

BUNDLE_NAME = "bundle.tgz"
CASE_ID = "SUP-4471"


def receipt_line(size: int, endpoint: str) -> str:
    return f"{CASE_ID} {BUNDLE_NAME} {size} {endpoint}"
