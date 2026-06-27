from sqlmodel import Session, select
from database import engine
from models import AgentState, Achievement


SEED_ACHIEVEMENTS = [
    {"id": "first_chat", "name": "初次对话", "description": "和智能体进行第一次聊天", "icon": "💬", "target": 1},
    {"id": "chat_10", "name": "话痨", "description": "和智能体聊天 10 次", "icon": "🗣️", "target": 10},
    {"id": "feed_5", "name": "美食家", "description": "补给智能体 5 次", "icon": "🍖", "target": 5},
    {"id": "level_5", "name": "成长中", "description": "智能体达到 5 级", "icon": "⭐", "target": 5},
    {"id": "all_max", "name": "完美照顾", "description": "三项属性同时超过 90", "icon": "💯", "target": 1},
]


def seed_data():
    with Session(engine) as session:
        # Seed agent state if not exists
        agent = session.get(AgentState, 1)
        if agent is None:
            session.add(AgentState())
            session.commit()

        # Seed achievements if not exists
        for ach_data in SEED_ACHIEVEMENTS:
            existing = session.get(Achievement, ach_data["id"])
            if existing is None:
                session.add(Achievement(**ach_data))
            else:
                existing.name = ach_data["name"]
                existing.description = ach_data["description"]
                existing.icon = ach_data["icon"]
                existing.target = ach_data["target"]
        session.commit()
