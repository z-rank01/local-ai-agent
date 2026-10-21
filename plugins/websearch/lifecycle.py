"""Search owns container control, health checks and its registration changes."""


class SearchLifecycle:
    def __init__(self, service, runtime):
        self.service, self.runtime = service, runtime

    async def activate(self):
        try:
            if not await self.service._websearch_healthy():
                await self.service._compose('up', '-d')
                await self.service._wait_websearch()
            self.service.apply_to(self.runtime, True)
        except BaseException:
            self.service.apply_to(self.runtime, False)
            raise

    async def deactivate(self):
        await self.service._compose('stop')
        self.service.apply_to(self.runtime, False)
