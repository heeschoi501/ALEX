import unittest
from unittest import mock

from jarvis.device import DeviceError, WakeOnLanTool, magic_packet, normalize_mac


class WakeOnLanTests(unittest.TestCase):
    def test_mac_normalization_and_packet(self):
        self.assertEqual(normalize_mac("aa:bb:cc:dd:ee:ff"), "AABBCCDDEEFF")
        packet = magic_packet("AA-BB-CC-DD-EE-FF")
        self.assertEqual(len(packet), 102)
        self.assertEqual(packet[:6], b"\xff" * 6)
        self.assertEqual(packet[6:12], bytes.fromhex("AABBCCDDEEFF"))

    def test_bad_mac_rejected(self):
        with self.assertRaises(DeviceError):
            normalize_mac("not-a-mac")

    @mock.patch("jarvis.device.socket.socket")
    def test_run_sends_only_configured_target(self, socket_cls):
        sock = socket_cls.return_value.__enter__.return_value
        tool = WakeOnLanTool("AA:BB:CC:DD:EE:FF", "255.255.255.255", 9)
        result = tool.run()
        args = sock.sendto.call_args.args
        self.assertEqual(len(args[0]), 102)
        self.assertEqual(args[1], ("255.255.255.255", 9))
        self.assertNotIn("AABBCCDDEEFF", result)

if __name__ == "__main__":
    unittest.main()

class VSCodeLauncherTests(unittest.TestCase):
    @mock.patch("subprocess.Popen")
    def test_launcher_uses_fixed_argv_without_shell(self, popen):
        import tempfile
        from pathlib import Path
        from jarvis.device import VSCodeLauncher
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            launcher = VSCodeLauncher(root, "code")
            launcher.run()
            _, kwargs = popen.call_args
            self.assertEqual(popen.call_args.args[0], ["code", "."])
            self.assertEqual(kwargs["cwd"], root.resolve())
            self.assertFalse(kwargs["shell"])
