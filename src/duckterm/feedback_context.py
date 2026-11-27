"""Bound reader-supplied page locations without parsing PDF content on the server."""

from typing import Any

from duckterm.core.session_api import APIError


def pdf_page_context(req: dict[str, Any], media_type: str) -> dict[str, int]:
    page = req.get("pdf_page")
    if page is None:
        return {}
    if media_type != "application/pdf" or type(page) is not int or not 1 <= page <= 100_000:
        raise APIError(400, "PDF page must be a positive whole number for a PDF document")
    # The trusted identity is the rechecked file/artifact hash. This location is
    # reader-supplied context, not a server assertion about the document's pages.
    return {"pdf_page": page}
