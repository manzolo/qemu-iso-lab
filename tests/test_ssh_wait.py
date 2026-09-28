"""wait_for_ssh: a sshd that will never accept us fails fast instead of eating the whole timeout."""
import subprocess
from unittest import mock

import vmctl.ssh
from tests._common import BaseVmctlTestCase
from vmctl import ssh
from vmctl.errors import VMError


def completed(code, stderr=b""):
    return subprocess.CompletedProcess([], code, b"", stderr)


class WaitForSshTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.vm_config["ssh_provision"] = {"user": "lab", "hostname": "box", "ssh_host_port": 2222}
        self.patches = [mock.patch.object(vmctl.ssh.time, "sleep"), mock.patch.object(ssh, "ssh_base_cmd", return_value=["ssh", "box"])]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_closed_port_keeps_waiting_and_success_returns(self):
        answers = [completed(255, b"ssh: connect to host 127.0.0.1 port 2222: Connection refused"), completed(255, b""), completed(0)]
        with mock.patch.object(vmctl.ssh.subprocess, "run", side_effect=answers) as run:
            ssh.wait_for_ssh(self.vm_config, 60)
        self.assertEqual(run.call_count, 3)

    def test_an_algorithm_mismatch_fails_at_once_with_the_hint(self):
        stderr = b"Unable to negotiate with 127.0.0.1 port 2222: no matching host key type found. Their offer: ssh-rsa,ssh-dss"
        with mock.patch.object(vmctl.ssh.subprocess, "run", return_value=completed(255, stderr)) as run:
            with self.assertRaisesRegex(VMError, "cannot agree on an algorithm.*Their offer: ssh-rsa,ssh-dss.*key_type: rsa"):
                ssh.wait_for_ssh(self.vm_config, 3600)
        self.assertEqual(run.call_count, 1)

    def test_a_refused_key_fails_after_five_minutes_not_an_hour(self):
        clock = iter(range(0, 100000, 20))
        with mock.patch.object(vmctl.ssh.time, "monotonic", side_effect=lambda: float(next(clock))), \
             mock.patch.object(vmctl.ssh.subprocess, "run", return_value=completed(255, b"lab@127.0.0.1: Permission denied (publickey).")) as run:
            with self.assertRaisesRegex(VMError, "refused the key for 300s.*Permission denied"):
                ssh.wait_for_ssh(self.vm_config, 3600)
        self.assertLess(run.call_count, 40)  # about 300 s of probes at 20 s each on this clock, not 3600

    def test_a_denial_that_stops_resets_the_clock(self):
        answers = [completed(255, b"Permission denied (publickey).")] * 3 + [completed(255, b"")] + [completed(0)]
        with mock.patch.object(vmctl.ssh.subprocess, "run", side_effect=answers):
            ssh.wait_for_ssh(self.vm_config, 3600)
        self.assertEqual(ssh.classify_ssh_failure("kex_exchange_identification: read: Connection reset"), "")
        self.assertEqual(ssh.classify_ssh_failure("sign_and_send_pubkey: no mutual signature supported"), "negotiate")
