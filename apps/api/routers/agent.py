from datetime import datetime
from fastapi import APIRouter, Depends
from sqlmodel import Session
from database import get_session
from models import AgentState, InteractionLog
from schemas import AgentStateResponse, AgentActionResponse
from services.achievement_checker import check_achievements

router = APIRouter(tags=["agent"])


def xp_to_next_level(level: int) -> int:
    return int(100 * level * 1.5)


def apply_decay(agent: AgentState) -> None:
    now = datetime.utcnow()
    elapsed_minutes = (now - agent.updated_at).total_seconds() / 60
    agent.hunger = max(0, agent.hunger - elapsed_minutes * 0.1)
    agent.mood = max(0, agent.mood - elapsed_minutes * 0.05)
    agent.energy = max(0, agent.energy - elapsed_minutes * 0.08)
    agent.updated_at = now


def perform_action(agent: AgentState, action: str) -> None:
    if action == "feed":
        agent.hunger = min(100, agent.hunger + 20)
        agent.xp += 10
    elif action == "play":
        agent.mood = min(100, agent.mood + 20)
        agent.energy = max(0, agent.energy - 10)
        agent.xp += 15
    elif action == "rest":
        agent.energy = min(100, agent.energy + 30)
        agent.xp += 5
    elif action == "chat":
        agent.mood = min(100, agent.mood + 5)
        agent.xp += 10

    # Check level up
    needed = xp_to_next_level(agent.level)
    while agent.xp >= needed:
        agent.xp -= needed
        agent.level += 1
        needed = xp_to_next_level(agent.level)


def agent_to_response(agent: AgentState) -> AgentStateResponse:
    return AgentStateResponse(
        name=agent.name,
        level=agent.level,
        xp=agent.xp,
        xp_to_next=xp_to_next_level(agent.level),
        hunger=round(agent.hunger),
        mood=round(agent.mood),
        energy=round(agent.energy),
    )


@router.get("/agent/state", response_model=AgentStateResponse)
def get_agent_state(session: Session = Depends(get_session)):
    agent = session.get(AgentState, 1)
    apply_decay(agent)
    session.add(agent)
    session.commit()
    return agent_to_response(agent)


@router.post("/agent/feed", response_model=AgentActionResponse)
def feed_agent(session: Session = Depends(get_session)):
    agent = session.get(AgentState, 1)
    apply_decay(agent)
    perform_action(agent, "feed")
    session.add(InteractionLog(action="feed"))
    session.add(agent)
    session.commit()
    new_achs = check_achievements(session)
    return AgentActionResponse(agent=agent_to_response(agent), new_achievements=new_achs)


@router.post("/agent/play", response_model=AgentActionResponse)
def play_agent(session: Session = Depends(get_session)):
    agent = session.get(AgentState, 1)
    apply_decay(agent)
    perform_action(agent, "play")
    session.add(InteractionLog(action="play"))
    session.add(agent)
    session.commit()
    new_achs = check_achievements(session)
    return AgentActionResponse(agent=agent_to_response(agent), new_achievements=new_achs)


@router.post("/agent/rest", response_model=AgentActionResponse)
def rest_agent(session: Session = Depends(get_session)):
    agent = session.get(AgentState, 1)
    apply_decay(agent)
    perform_action(agent, "rest")
    session.add(InteractionLog(action="rest"))
    session.add(agent)
    session.commit()
    new_achs = check_achievements(session)
    return AgentActionResponse(agent=agent_to_response(agent), new_achievements=new_achs)
