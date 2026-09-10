"""Shared upload validation.

Every upload endpoint funnels through here, so size and content-type limits are
enforced in one place instead of being re-derived (or forgotten) per feature.

The frontend already tells users "PDF up to ~10MB"; these constants are what
makes that claim true on the server side.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, UploadFile, status

logger = logging.getLogger(__name__)

MB = 1024 * 1024

MAX_IMAGE_BYTES = 8 * MB
MAX_PDF_BYTES = 10 * MB

IMAGE_CONTENT_TYPES = ("image/jpeg", "image/png")


def _too_large(limit: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail=f"That file is too large. The limit is {limit // MB} MB.",
    )


async def read_upload(file: UploadFile, *, max_bytes: int) -> bytes:
    """Read an upload, refusing anything over ``max_bytes``.

    The declared size is checked first because it's free and lets us reject
    before buffering, then the bytes actually received are checked too — the
    header is client-supplied and may lie.
    """
    declared = file.size
    if declared is not None and declared > max_bytes:
        logger.info("Rejected upload: declared %d bytes > limit %d", declared, max_bytes)
        raise _too_large(max_bytes)

    data = await file.read()
    if len(data) > max_bytes:
        logger.info("Rejected upload: received %d bytes > limit %d", len(data), max_bytes)
        raise _too_large(max_bytes)
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty.")
    return data


def require_image(file: UploadFile) -> None:
    """Reject anything that isn't a JPEG or PNG."""
    if file.content_type not in IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=400, detail="Only JPEG or PNG images supported."
        )
