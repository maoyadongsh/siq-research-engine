from routers.agent_user_router import SpecialistAgentConfig, create_specialist_agent_router


router = create_specialist_agent_router(
    SpecialistAgentConfig(prefix="/tracking", tag="tracking", profile="siq_tracking")
)
