"""HTTP adapter modules for the CapitalRadar web dashboard.

Each submodule is a deep workflow exposed as a FastAPI ``APIRouter`` and wired
into ``web.app`` via ``app.include_router(...)``. This keeps ``web/app.py`` as
the composition root (lifespan, middleware, static mounts) rather than a single
monolithic file.
"""
