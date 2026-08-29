from __future__ import annotations

import os
import socket
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "nt", "PowerShell launcher validation is Windows-only")
class RunSecurityTests(unittest.TestCase):
    def test_non_loopback_bind_addresses_are_rejected_before_startup(self) -> None:
        script = Path(__file__).resolve().parents[1] / "run.ps1"
        cases = (("0.0.0.0", 39871), ("192.168.1.10", 39872))

        for address, port in cases:
            with self.subTest(address=address):
                completed = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                        "-BindAddress",
                        address,
                        "-Port",
                        str(port),
                        "-NoBrowser",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                output = completed.stdout + completed.stderr
                decoded = output.decode("utf-8", errors="replace") + output.decode(
                    "utf-16-le", errors="replace"
                )
                self.assertNotEqual(0, completed.returncode, decoded)
                self.assertIn("P3-05", decoded)
                self.assertIn("127.0.0.1", decoded)

                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.2)
                    self.assertNotEqual(0, probe.connect_ex(("127.0.0.1", port)))


if __name__ == "__main__":
    unittest.main()
