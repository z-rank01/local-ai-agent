"""Finance keeps its authenticated launcher/stock-service lifecycle behind the contract."""


class FinanceLifecycle:
    def __init__(self, service, runtime):
        self.service, self.runtime = service, runtime

    async def activate(self):
        await self.service.start(self.runtime)

    async def deactivate(self):
        await self.service.stop(self.runtime)
