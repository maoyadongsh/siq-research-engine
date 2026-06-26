from routers.agent_user_router import SpecialistAgentConfig, create_specialist_agent_router


router = create_specialist_agent_router(
    SpecialistAgentConfig(prefix="/analysis", tag="analysis", profile="siq_analysis")
)
