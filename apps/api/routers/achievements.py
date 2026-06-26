from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from database import get_session
from models import Achievement
from schemas import AchievementResponse

router = APIRouter(tags=["achievements"])


@router.get("/achievements", response_model=list[AchievementResponse])
def list_achievements(session: Session = Depends(get_session)):
    achs = session.exec(select(Achievement)).all()
    return achs
