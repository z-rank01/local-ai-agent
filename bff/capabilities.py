"""Authenticated inventory route; mutations remain in their lifecycle owner."""
from fastapi import APIRouter
from core import config
from harness.capabilities.catalog import package_catalog
from .deps import get_runtime

router = APIRouter()


@router.get('/api/capabilities')
async def capabilities():
    return {'api_version': 1, 'capabilities': package_catalog(
        config._PROJECT_ROOT / 'plugins', get_runtime().tool_registry.ownership())}
