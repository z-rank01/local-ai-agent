"""Offline regression driver: real HTTP transports fail, fixtures still work.

Run with python -m tests.run_offline. This is a test driver, not an app entry.
"""
import sys
import unittest
from unittest.mock import patch
import httpx


def main():
    attempts = []
    current = ['discovery']

    class Result(unittest.TextTestResult):
        def startTest(self, test):
            current[0] = test.id()
            super().startTest(test)

    def reject(self, request):
        # Do not log headers, credentials, query parameters, or payloads.
        attempts.append((current[0], request.method, request.url.host))
        raise httpx.ConnectError('Offline tests prohibit real HTTP transport', request=request)

    async def reject_async(self, request):
        return reject(self, request)

    with patch.object(httpx.HTTPTransport, 'handle_request', reject), \
            patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', reject_async):
        suite = (unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]) if len(sys.argv) > 1
                 else unittest.defaultTestLoader.discover('tests'))
        result = unittest.TextTestRunner(verbosity=1, resultclass=Result).run(suite)
    if attempts:
        print(f'Blocked {len(attempts)} real HTTP attempts; offline validation failed.', file=sys.stderr)
        for attempt in sorted(set(attempts)):
            print('Blocked test/operation/host:', *attempt, file=sys.stderr)
    return 0 if result.wasSuccessful() and not attempts else 1


if __name__ == '__main__':
    raise SystemExit(main())
