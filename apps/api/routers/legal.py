from routers.agent_user_router import SpecialistAgentConfig, create_specialist_agent_router


router = create_specialist_agent_router(
    SpecialistAgentConfig(prefix="/legal", tag="legal", profile="siq_legal")
)
