from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.services.asr as asr_module


@unittest.skipUnless(os.name == "nt", "Windows DLL lookup is Windows-only")
class WindowsLocalASRRuntimeTests(unittest.TestCase):
    def tearDown(self) -> None:
        asr_module._configure_nvidia_dll_search_path.cache_clear()
        asr_module._NVIDIA_DLL_HANDLES.clear()

    def test_project_nvidia_dll_directories_are_registered_and_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site_packages = Path(temporary) / "Lib" / "site-packages" / "nvidia"
            expected = [
                site_packages / package / "bin"
                for package in ("cublas", "cudnn", "cuda_nvrtc")
            ]
            for directory in expected:
                directory.mkdir(parents=True)

            handles = [object(), object(), object()]
            with (
                patch.object(asr_module.sys, "prefix", temporary),
                patch.dict(
                    asr_module.os.environ,
                    {"PATH": r"C:\existing"},
                    clear=False,
                ),
                patch.object(
                    asr_module.os,
                    "add_dll_directory",
                    side_effect=handles,
                ) as add_directory,
            ):
                asr_module._configure_nvidia_dll_search_path.cache_clear()
                asr_module._NVIDIA_DLL_HANDLES.clear()
                loaded = asr_module._configure_nvidia_dll_search_path()
                runtime_path = asr_module.os.environ["PATH"]

            self.assertEqual(tuple(str(path) for path in expected), loaded)
            self.assertEqual(handles, asr_module._NVIDIA_DLL_HANDLES)
            self.assertEqual(3, add_directory.call_count)
            self.assertEqual(
                os.pathsep.join([*(str(path) for path in expected), r"C:\existing"]),
                runtime_path,
            )


if __name__ == "__main__":
    unittest.main()
