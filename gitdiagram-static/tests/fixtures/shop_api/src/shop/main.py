"""Application factory and HTTP entry point for the shop API."""

from fastapi import FastAPI

from shop.api.routes_catalog import router as catalog_router
from shop.api.routes_orders import router as orders_router
from shop.db.session import init_db
from shop.integrations.payments import configure_stripe


def create_app() -> FastAPI:
    """Build the ASGI application."""
    app = FastAPI(title="Shop API")
    init_db()
    configure_stripe()
    app.include_router(catalog_router, prefix="/catalog")
    app.include_router(orders_router, prefix="/orders")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
