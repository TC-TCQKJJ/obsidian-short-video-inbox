import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "wechat_capture"


class WechatCaptureInstallTest(unittest.TestCase):
    def test_clash_rules_are_narrow_and_localhost_only(self):
        text = (ROOT / "clash-extend-script.js").read_text(encoding="utf-8")

        self.assertIn('server: "127.0.0.1"', text)
        self.assertIn("DOMAIN,channels.weixin.qq.com", text)
        self.assertIn("DOMAIN-SUFFIX,wxs.qq.com", text)
        self.assertIn("config.tun", text)
        self.assertNotIn('"PROCESS-NAME,python.exe,DIRECT"', text)
        self.assertNotIn('"PROCESS-NAME,pythonw.exe,DIRECT"', text)
        self.assertNotIn("DOMAIN-SUFFIX,qq.com", text)

    def test_certificate_scripts_use_current_user_only(self):
        install = (ROOT / "install.ps1").read_text(encoding="utf-8")
        uninstall = (ROOT / "uninstall-certificate.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("install_current_user_ca", install)
        self.assertIn("uninstall_current_user_ca", uninstall)
        self.assertNotIn("LocalMachine", install + uninstall)
        self.assertNotIn("mitmproxy-ca.pem", install)

    def test_start_script_does_not_change_system_proxy(self):
        text = (ROOT / "start.ps1").read_text(encoding="utf-8").lower()

        self.assertNotIn("internetsetoption", text)
        self.assertNotIn("proxyenable", text)
        self.assertNotIn("set-itemproperty", text)


if __name__ == "__main__":
    unittest.main()
