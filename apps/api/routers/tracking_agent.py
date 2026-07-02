from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute

from routers.agent_user_router import SpecialistAgentConfig, create_specialist_agent_router
from services.auth_dependencies import require_permission


TRACKING_READ_PERMISSION = "tracking.read"
TRACKING_WRITE_PERMISSION = "tracking.write"


def _tracking_permission_for(route: APIRoute) -> str:
    if "GET" in route.methods:
        return TRACKING_READ_PERMISSION
    return TRACKING_WRITE_PERMISSION


_base_router = create_specialist_agent_router(
    SpecialistAgentConfig(prefix="/tracking", tag="tracking", profile="siq_tracking")
)

router = APIRouter()
for route in _base_router.routes:
    if not isinstance(route, APIRoute):
        router.routes.append(route)
        continue
    dependencies = [
        *route.dependencies,
        Depends(require_permission(_tracking_permission_for(route))),
    ]
    router.add_api_route(
        route.path,
        route.endpoint,
        response_model=route.response_model,
        status_code=route.status_code,
        tags=route.tags,
        dependencies=dependencies,
        summary=route.summary,
        description=route.description,
        response_description=route.response_description,
        responses=route.responses,
        deprecated=route.deprecated,
        methods=route.methods,
        operation_id=route.operation_id,
        response_model_include=route.response_model_include,
        response_model_exclude=route.response_model_exclude,
        response_model_by_alias=route.response_model_by_alias,
        response_model_exclude_unset=route.response_model_exclude_unset,
        response_model_exclude_defaults=route.response_model_exclude_defaults,
        response_model_exclude_none=route.response_model_exclude_none,
        include_in_schema=route.include_in_schema,
        response_class=route.response_class,
        name=route.name,
        route_class_override=type(route),
        callbacks=route.callbacks,
        openapi_extra=route.openapi_extra,
        generate_unique_id_function=route.generate_unique_id_function,
        strict_content_type=route.strict_content_type,
    )
