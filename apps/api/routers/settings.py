from fastapi import APIRouter, Depends

from routers.auth import require_permission
from services.llm_settings import LLMSettingsUpdate, LLMTestRequest, load_llm_settings, save_llm_settings, test_llm_provider

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(require_permission("system.config"))],
)


@router.get("/llm")
def get_llm_settings():
    return load_llm_settings(include_secrets=False)


@router.put("/llm")
def update_llm_settings(payload: LLMSettingsUpdate):
    return save_llm_settings(payload)


@router.post("/llm/test")
async def test_llm_settings(payload: LLMTestRequest):
    return await test_llm_provider(payload)
