import unittest
from pathlib import Path

from script.local_audio_recorder import (
    build_record_command,
    choose_loopback_device,
    parse_dshow_audio_devices,
)


class LocalAudioRecorderTest(unittest.TestCase):
    def test_parses_audio_devices_from_ffmpeg_dshow_output(self):
        output = """
        [dshow @ 000001] "HP Wide Vision HD Camera" (video)
        [dshow @ 000001] "立体声混音 (Realtek(R) Audio)" (audio)
        [dshow @ 000001]   Alternative name "@device_cm_{abc}"
        [dshow @ 000001] "麦克风阵列 (英特尔)" (audio)
        """

        self.assertEqual(
            parse_dshow_audio_devices(output),
            ["立体声混音 (Realtek(R) Audio)", "麦克风阵列 (英特尔)"],
        )

    def test_prefers_loopback_device_over_microphone(self):
        devices = ["麦克风阵列 (英特尔)", "CABLE Output (VB-Audio Virtual Cable)"]

        self.assertEqual(
            choose_loopback_device(devices),
            "CABLE Output (VB-Audio Virtual Cable)",
        )

    def test_builds_ffmpeg_dshow_record_command(self):
        command = build_record_command(
            "立体声混音 (Realtek(R) Audio)",
            Path("capture.mp3"),
        )

        self.assertEqual(command[:4], ["ffmpeg", "-y", "-f", "dshow"])
        self.assertIn("audio=立体声混音 (Realtek(R) Audio)", command)
        self.assertIn("capture.mp3", command)


if __name__ == "__main__":
    unittest.main()
