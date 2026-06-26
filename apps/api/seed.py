from sqlmodel import Session, select
from database import engine
from models import PetState, Achievement


SEED_ACHIEVEMENTS = [
    {"id": "first_chat", "name": "初次对话", "description": "和宠物进行第一次聊天", "icon": "💬", "target": 1},
    {"id": "chat_10", "name": "话痨", "description": "和宠物聊天 10 次", "icon": "🗣️", "target": 10},
    {"id": "feed_5", "name": "美食家", "description": "喂食宠物 5 次", "icon": "🍖", "target": 5},
    {"id": "level_5", "name": "成长中", "description": "宠物达到 5 级", "icon": "⭐", "target": 5},
    {"id": "all_max", "name": "完美照顾", "description": "三项属性同时超过 90", "icon": "💯", "target": 1},
]


def seed_data():
    with Session(engine) as session:
        # Seed pet state if not exists
        pet = session.get(PetState, 1)
        if pet is None:
            session.add(PetState())
            session.commit()

        # Seed achievements if not exists
        for ach_data in SEED_ACHIEVEMENTS:
            existing = session.get(Achievement, ach_data["id"])
            if existing is None:
                session.add(Achievement(**ach_data))
        session.commit()
