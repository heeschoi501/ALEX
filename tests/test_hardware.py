"""Hardware profile selection and detection fallback tests."""
import unittest

from jarvis.hardware import HardwareInfo, PerformanceProfile, choose_profile, detect_hardware


class HardwareProfileTests(unittest.TestCase):
    def test_laptop_profile(self):
        info = HardwareInfo("Windows", "CPU", 32, "NVIDIA RTX 4070 Laptop GPU", 8)
        self.assertEqual(choose_profile(info), PerformanceProfile.LAPTOP)

    def test_desktop_profile(self):
        info = HardwareInfo("Windows", "Ryzen 7 9800X3D", 96, "NVIDIA RTX 5080", 16)
        self.assertEqual(choose_profile(info), PerformanceProfile.DESKTOP)

    def test_unknown_fallback(self):
        info = HardwareInfo("Linux", "Unknown", None, None, None)
        self.assertEqual(choose_profile(info), PerformanceProfile.UNKNOWN)

    def test_low_ram_gpu_is_not_laptop_profile(self):
        """VRAM 은 충분해도 RAM 이 부족하면 보수적으로 UNKNOWN 이다."""
        info = HardwareInfo("Windows", "CPU", 8, "NVIDIA RTX 4070 Laptop GPU", 8)
        self.assertEqual(choose_profile(info), PerformanceProfile.UNKNOWN)

    def test_big_gpu_but_small_ram_falls_to_laptop(self):
        info = HardwareInfo("Windows", "CPU", 32, "NVIDIA RTX 5080", 16)
        self.assertEqual(choose_profile(info), PerformanceProfile.LAPTOP)

    def test_wsl_laptop_target_uses_visible_ram_threshold(self):
        info = HardwareInfo(
            "Linux-WSL2-microsoft-standard", "CPU", 16, "NVIDIA RTX 4070 Laptop GPU", 8
        )
        self.assertEqual(choose_profile(info), PerformanceProfile.LAPTOP)

    def test_wsl_desktop_target_uses_visible_ram_threshold(self):
        info = HardwareInfo(
            "Linux-WSL2-microsoft-standard", "CPU", 48, "NVIDIA RTX 5080", 16
        )
        self.assertEqual(choose_profile(info), PerformanceProfile.DESKTOP)

    def test_wsl_tiny_memory_still_stays_conservative(self):
        info = HardwareInfo(
            "Linux-WSL2-microsoft-standard", "CPU", 8, "NVIDIA RTX 4070 Laptop GPU", 8
        )
        self.assertEqual(choose_profile(info), PerformanceProfile.UNKNOWN)

    def test_cpu_only_machine(self):
        info = HardwareInfo("Linux", "CPU", 128, None, None)
        self.assertEqual(choose_profile(info), PerformanceProfile.UNKNOWN)

    def test_detect_hardware_never_crashes(self):
        info = detect_hardware()
        self.assertIsInstance(info, HardwareInfo)
        self.assertIsInstance(info.profile, PerformanceProfile)
        self.assertTrue(info.os_name)


if __name__ == "__main__":
    unittest.main()


class NvidiaDetectionTests(unittest.TestCase):
    def test_multi_gpu_picks_largest_vram(self):
        from unittest.mock import patch
        import subprocess
        import jarvis.hardware as hw

        completed = subprocess.CompletedProcess(
            ["nvidia-smi"], 0,
            stdout="NVIDIA Small GPU, 4096\nNVIDIA RTX 5080, 16384\n",
            stderr="",
        )
        with patch.object(hw, "_find_nvidia_smi", return_value="nvidia-smi"), patch.object(
            hw.subprocess, "run", return_value=completed
        ):
            name, vram = hw._detect_nvidia()
        self.assertEqual(name, "NVIDIA RTX 5080")
        self.assertEqual(vram, 16.0)

    def test_wsl_fallback_path_can_be_used(self):
        from unittest.mock import patch
        import jarvis.hardware as hw

        with patch.object(hw.shutil, "which", return_value=None), patch.object(
            hw.os.path, "isfile", return_value=True
        ), patch.object(hw.os, "access", return_value=True):
            self.assertEqual(hw._find_nvidia_smi(), "/usr/lib/wsl/lib/nvidia-smi")
