"""Workspace reconfiguration consults registered background execution owners."""
async def check(runtime, snapshot):
    for package in (getattr(runtime, 'container_packages', None) or {}).values():
        await package.client.assert_reconfigurable(snapshot)
