"""Exercise the real PTY relay with a harmless local fixture process, never a guest."""
import json
import socket
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from vmctl import web_terminal, webui


class TerminalRelayTests(unittest.TestCase):
    def relay(self, source):
        server, client = socket.socketpair()
        client.settimeout(5)
        handler = SimpleNamespace(connection=server, rfile=server.makefile('rb'), wfile=server.makefile('wb'))
        thread = threading.Thread(target=web_terminal.bridge, args=(handler, [sys.executable, '-u', '-c', source]), daemon=True)
        thread.start()
        reader = client.makefile('rb')

        def cleanup():
            client.sendall(webui.ws_frame(8, b'')) if thread.is_alive() else None
            thread.join(timeout=5)
            reader.close()
            client.close()
            handler.rfile.close()
            handler.wfile.close()
            server.close()
            self.assertFalse(thread.is_alive(), 'PTY relay must finish when the browser disconnects')

        self.addCleanup(cleanup)
        return client, reader, thread

    def collect(self, reader, wanted):
        output = b''
        while wanted not in output:
            frame = webui.ws_read_frame(reader)
            if frame is None or frame[0] == 8:
                break
            output += frame[1]
        self.assertIn(wanted, output)
        return output

    def test_input_resize_and_password_terminal(self):
        source = "import os; open('/dev/tty').close(); print('READY', flush=True); value=input(); print('GOT:'+value+':'+str(os.get_terminal_size(0)), flush=True)"
        client, reader, thread = self.relay(source)
        self.collect(reader, b'READY')
        for message in ({'type':'resize','cols':123,'rows':41}, {'type':'input','data':'hello\n'}):
            client.sendall(webui.ws_frame(1, json.dumps(message).encode()))
        output = self.collect(reader, b'GOT:hello')
        self.assertIn(b'columns=123, lines=41', output)
        thread.join(timeout=5)

    def test_disconnect_terminates_and_reaps_only_its_client(self):
        with mock.patch.object(web_terminal.subprocess, 'Popen', wraps=web_terminal.subprocess.Popen) as popen:
            client, reader, thread = self.relay("print('READY',flush=True); input()")
            self.collect(reader, b'READY')
            client.sendall(webui.ws_frame(8, b''))
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertTrue(popen.call_args.kwargs['start_new_session'])

    def test_resize_clamps_untrusted_dimensions(self):
        import struct
        with mock.patch.object(web_terminal.fcntl, 'ioctl') as ioctl:
            web_terminal.resize(12, 99999, -2)
        self.assertEqual(struct.unpack('HHHH', ioctl.call_args.args[2]), (1, 500, 0, 0))
