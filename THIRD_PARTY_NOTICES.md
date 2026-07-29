# Third-party notices

The repository's original code is licensed under the MIT License. Third-party
components listed below remain under their respective licenses and are not
relicensed by the repository-level MIT License.

`backend/script/wechat_media_decrypt.py` includes an ISAAC-64 implementation
derived from `ltaoo/wx_channels_download`, itself derived from
`Hanson/WechatSphDecrypt`.

- Upstream: https://github.com/ltaoo/wx_channels_download
- Copyright: Copyright (c) 2025 ltaoo
- License: MIT License with Commons Clause License Condition v1.0
- Full license text: `third_party/wx_channels_download-LICENSE`

The Commons Clause restricts selling software whose value derives entirely or
substantially from the covered software. That file is not relicensed under the
repository's MIT license.

`backend/wechat_process_capture` links against `qtgolang/SunnyNet` for its
Windows process-driver interface.

- Upstream: https://github.com/qtgolang/SunnyNet
- Copyright: Copyright (c) 2025 秦天
- License: MIT License
- Full license text: `third_party/SunnyNet-LICENSE`
