from fastapi import APIRouter, Depends

from services.system_status import collect_system_status
from services.auth_dependencies import require_permission

router = APIRouter(
    prefix="/system",
    tags=["system"],
    dependencies=[Depends(require_permission("system.config"))],
)


@router.get("/status")
async def system_status():
    return await collect_system_status()
