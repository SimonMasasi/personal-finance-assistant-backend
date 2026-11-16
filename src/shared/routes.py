from src.modules.auth.views import auth_router
from src.modules.health.views import health_router
from src.modules.sync.views import sync_router
from src.utils.open_api_routes import open_api_router

# Adding a module is one import and one entry here. src/main.py loops this list.
routes = [
    health_router,
    auth_router,
    sync_router,
    open_api_router,
]
