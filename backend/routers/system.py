from fastapi import APIRouter

from services.system_status import collect_system_status

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status")
async def system_status():
    return await collect_system_status()
