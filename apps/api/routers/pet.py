from datetime import datetime
from fastapi import APIRouter, Depends
from sqlmodel import Session
from database import get_session
from models import PetState, InteractionLog
from schemas import PetStateResponse, ActionResponse
from services.achievement_checker import check_achievements

router = APIRouter(tags=["pet"])


def xp_to_next_level(level: int) -> int:
    return int(100 * level * 1.5)


def apply_decay(pet: PetState) -> None:
    now = datetime.utcnow()
    elapsed_minutes = (now - pet.updated_at).total_seconds() / 60
    pet.hunger = max(0, pet.hunger - elapsed_minutes * 0.1)
    pet.mood = max(0, pet.mood - elapsed_minutes * 0.05)
    pet.energy = max(0, pet.energy - elapsed_minutes * 0.08)
    pet.updated_at = now


def perform_action(pet: PetState, action: str) -> None:
    if action == "feed":
        pet.hunger = min(100, pet.hunger + 20)
        pet.xp += 10
    elif action == "play":
        pet.mood = min(100, pet.mood + 20)
        pet.energy = max(0, pet.energy - 10)
        pet.xp += 15
    elif action == "rest":
        pet.energy = min(100, pet.energy + 30)
        pet.xp += 5
    elif action == "chat":
        pet.mood = min(100, pet.mood + 5)
        pet.xp += 10

    # Check level up
    needed = xp_to_next_level(pet.level)
    while pet.xp >= needed:
        pet.xp -= needed
        pet.level += 1
        needed = xp_to_next_level(pet.level)


def pet_to_response(pet: PetState) -> PetStateResponse:
    return PetStateResponse(
        name=pet.name,
        level=pet.level,
        xp=pet.xp,
        xp_to_next=xp_to_next_level(pet.level),
        hunger=round(pet.hunger),
        mood=round(pet.mood),
        energy=round(pet.energy),
    )


@router.get("/pet/state", response_model=PetStateResponse)
def get_pet_state(session: Session = Depends(get_session)):
    pet = session.get(PetState, 1)
    apply_decay(pet)
    session.add(pet)
    session.commit()
    return pet_to_response(pet)


@router.post("/pet/feed", response_model=ActionResponse)
def feed_pet(session: Session = Depends(get_session)):
    pet = session.get(PetState, 1)
    apply_decay(pet)
    perform_action(pet, "feed")
    session.add(InteractionLog(action="feed"))
    session.add(pet)
    session.commit()
    new_achs = check_achievements(session)
    return ActionResponse(pet=pet_to_response(pet), new_achievements=new_achs)


@router.post("/pet/play", response_model=ActionResponse)
def play_pet(session: Session = Depends(get_session)):
    pet = session.get(PetState, 1)
    apply_decay(pet)
    perform_action(pet, "play")
    session.add(InteractionLog(action="play"))
    session.add(pet)
    session.commit()
    new_achs = check_achievements(session)
    return ActionResponse(pet=pet_to_response(pet), new_achievements=new_achs)


@router.post("/pet/rest", response_model=ActionResponse)
def rest_pet(session: Session = Depends(get_session)):
    pet = session.get(PetState, 1)
    apply_decay(pet)
    perform_action(pet, "rest")
    session.add(InteractionLog(action="rest"))
    session.add(pet)
    session.commit()
    new_achs = check_achievements(session)
    return ActionResponse(pet=pet_to_response(pet), new_achievements=new_achs)
