from datetime import datetime
from sqlmodel import Session, select, func
from models import Achievement, AgentState, InteractionLog
from schemas import AchievementResponse


def check_achievements(session: Session) -> list[AchievementResponse]:
    """Check all achievement conditions and return newly unlocked ones."""
    achievements = session.exec(select(Achievement)).all()
    agent = session.get(AgentState, 1)

    chat_count = session.exec(
        select(func.count()).where(InteractionLog.action == "chat")
    ).one()
    feed_count = session.exec(
        select(func.count()).where(InteractionLog.action == "feed")
    ).one()

    conditions = {
        "first_chat": chat_count >= 1,
        "chat_10": chat_count >= 10,
        "feed_5": feed_count >= 5,
        "level_5": agent.level >= 5,
        "all_max": agent.hunger > 90 and agent.mood > 90 and agent.energy > 90,
    }

    progress_map = {
        "first_chat": (chat_count, 1),
        "chat_10": (chat_count, 10),
        "feed_5": (feed_count, 5),
        "level_5": (agent.level, 5),
        "all_max": (1 if conditions["all_max"] else 0, 1),
    }

    newly_unlocked = []
    for ach in achievements:
        prog, tgt = progress_map.get(ach.id, (0, ach.target))
        ach.progress = min(prog, tgt)

        if ach.unlocked_at is None and conditions.get(ach.id, False):
            ach.unlocked_at = datetime.utcnow()
            newly_unlocked.append(
                AchievementResponse.model_validate(ach)
            )

    session.commit()
    return newly_unlocked
