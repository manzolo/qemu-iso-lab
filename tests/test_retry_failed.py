"""check-vms --retry-failed: which rows run again, how a pass on a retry is reported, and that a
protected VM's own disk goes aside again before its row is retried."""
import argparse
import unittest
from unittest import mock

from vmctl import lifecycle


class RetryFailedTests(unittest.TestCase):
    def test_only_failures_that_could_pass_next_time_are_retried(self):
        results = [
            ("a", "passed", "ok"),
            ("b", "failed", "Timed out after 3600s waiting for '==> Debian preseed install complete!'"),
            ("c", "failed", "Unable to fetch ISO from configured sources: 404"),
            ("d", "skipped", "experimental profile"),
            ("e", "failed", "Unable to negotiate with 127.0.0.1 port 2292: no matching host key type"),
            ("node", "failed", "Timed out waiting for SSH"),
        ]
        self.assertEqual(lifecycle.retry_candidates(results, deferred={"node"}), ["b"])

    def test_a_row_that_passes_on_the_retry_is_reported_flaky_one_that_fails_again_says_so(self):
        results = [("b", "failed", "!! ERROR: Architecture not supported"), ("x", "failed", "curtin in-target failed")]
        cleanup = mock.Mock(deferred=set())
        outcomes = {"b": ("passed", "preseed + post-install"), "x": ("failed", "curtin in-target failed again")}
        args = argparse.Namespace(retry_failed=1, dry_run=False)
        order = []
        cleanup.prepare_retry.side_effect = lambda name: order.append(("prepare", name))
        def once(name, vm, args):
            order.append(("run", name))
            return outcomes[name]
        with mock.patch.object(lifecycle, "run_local_test_once", side_effect=once), \
             mock.patch.object(lifecycle.config, "get_vm", return_value={}), \
             mock.patch.object(lifecycle.report, "annotate_retry") as annotate:
            lifecycle.retry_failed_rows(results, {}, args, cleanup, None, 1)
        self.assertEqual(order[:2], [("prepare", "b"), ("prepare", "x")])  # every disk aside before any retry
        self.assertEqual(results[0][1], "passed")
        self.assertIn("(flaky)", results[0][2])
        self.assertIn("Architecture not supported", results[0][2])
        self.assertEqual(results[1][1], "failed")
        self.assertIn("failed 2 times", results[1][2])
        self.assertEqual({c.args[1] for c in annotate.call_args_list}, {"b", "x"})
        self.assertEqual(cleanup.row_done.call_count, 2)

    def test_the_parallel_path_retries_through_the_scheduler(self):
        results = [("b", "failed", "Timed out")]
        cleanup = mock.Mock(deferred=set())
        class FakeScheduler:
            def run(self, jobs, worker, on_start=None):
                return [(name, worker(name)) for name, _ in jobs]
        with mock.patch.object(lifecycle, "run_local_test_vm_subprocess", return_value=("passed", "ok", "")) as sub, \
             mock.patch.object(lifecycle.config, "get_vm", return_value={}), \
             mock.patch.object(lifecycle.scheduler, "vm_cost", return_value=None), \
             mock.patch.object(lifecycle.report, "annotate_retry"):
            lifecycle.retry_failed_rows(results, {}, argparse.Namespace(retry_failed=1, dry_run=False), cleanup, FakeScheduler(), None)
        sub.assert_called_once()
        self.assertEqual(results[0][1], "passed")
        cleanup.row_done.assert_called_once_with("b", "passed")

    def test_zero_disables_it(self):
        results = [("b", "failed", "Timed out")]
        cleanup = mock.Mock(deferred=set())
        with mock.patch.object(lifecycle, "run_local_test_once") as once:
            lifecycle.retry_failed_rows(results, {}, argparse.Namespace(retry_failed=0, dry_run=False), cleanup, None, 1)
        once.assert_not_called()
        self.assertEqual(results, [("b", "failed", "Timed out")])

    def test_a_protected_vm_whose_disk_came_back_is_stashed_again_before_the_retry(self):
        cleanup = lifecycle.RowCleanup.__new__(lifecycle.RowCleanup)
        cleanup.lock = __import__("threading").Lock()
        cleanup.restored, cleanup.kept, cleanup.stashed, cleanup.dry_run = ["mine"], [], {}, False
        with mock.patch.object(lifecycle, "cmd_stop") as stop, \
             mock.patch.object(lifecycle, "stash_local_test_artifacts", return_value={"mine": "/stash/mine"}) as stash:
            cleanup.prepare_retry("mine")
        stop.assert_called_once()
        stash.assert_called_once_with(["mine"], dry_run=False)
        self.assertEqual(cleanup.stashed, {"mine": "/stash/mine"})  # row_done will give it back again
        self.assertEqual(cleanup.restored, [])


if __name__ == "__main__":
    unittest.main()
