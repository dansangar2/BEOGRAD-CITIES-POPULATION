"""Run regression tests with external connections disabled."""
from contextlib import ExitStack
from unittest.mock import patch

from django.test.runner import DiscoverRunner


class OfflineDiscoverRunner(DiscoverRunner):
    def run_tests(self, test_labels, **kwargs):
        with ExitStack() as stack:
            for method in ("connect", "connect_ex"):
                stack.enter_context(patch(
                    f"socket.socket.{method}",
                    side_effect=AssertionError("External connections are disabled in regression tests."),
                ))
            return super().run_tests(test_labels, **kwargs)
