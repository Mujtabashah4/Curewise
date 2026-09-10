"""Skin analysis routes."""

import base64
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from src.core.dependencies import get_current_user
from src.core.uploads import MAX_IMAGE_BYTES, read_upload, require_image
from src.features.skin import service
from src.features.skin.schemas import AcneAnalysisResponse

router = APIRouter(prefix="/api/skin", tags=["skin"])
logger = logging.getLogger(__name__)


@router.post("/acne-analysis", response_model=AcneAnalysisResponse)
async def acne_analysis(
    image: UploadFile = File(...), current_user: dict = Depends(get_current_user)
):
    require_image(image)
    data = await read_upload(image, max_bytes=MAX_IMAGE_BYTES)
    try:
        b64 = base64.b64encode(data).decode("utf-8")
        image_url = f"data:{image.content_type};base64,{b64}"
        response = await service.analyze_acne(image_url)
    except Exception as exc:  # noqa: BLE001
        logger.error("Acne analysis failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="We couldn't analyze that photo right now. Please try again.",
        ) from exc
    return AcneAnalysisResponse(response=response)
