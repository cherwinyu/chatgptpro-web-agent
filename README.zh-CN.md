# chatgptpro-web-bridge — 非商业研究待审版本

[English](README.md) · [离线快速开始](docs/QUICKSTART.zh-CN.md) · [研究与使用边界](docs/RESEARCH_USE.zh-CN.md) · [许可证](LICENSE) · [安全说明](SECURITY.md)

**仅供 Private 仓库审核，不是已批准公开的正式版本。** 本仓库采用全新 Git 历史和严格的文件白名单，不 Fork、不克隆旧内部工程仓库。本项目不是 OpenAI 官方产品，也不代表获得官方背书。

## 当前可以验证的功能

面向 Windows 的有限范围 Agent 任务交接、单次本地发送资格、原回执及 Git 结果验证参考实现。首版确认的是**合成数据的离线流程**，不需要账号、Cookie 或 API 凭据。

```powershell
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang zh-CN --json
```

要求 Windows、Python 3.12、Git 2.32+。权限预览始终不授予发送资格。不要把离线测试通过理解为线上 ChatGPT 网页自动化已通过认证。

## 功能与使用限制

本项目不提供通用浏览器驱动、托管模型 API 客户端或 GitHub 结果写入工具。原有部分宿主接口仅保留作技术研究，不承诺任何账户、模型档位、费用、UI 或在线端到端兼容性。研究与非商业使用也不能绕过第三方服务条款。

正式法律文本为未修改的 [PolyForm Noncommercial License 1.0.0](LICENSE)，只适用于授权者实际有权许可的内容。它允许符合条款的非商业使用、修改与分发，但不是 OSI 开源许可证，也不自动授权未核实的第三方材料。“不用于生产环境”是项目支持定位，不是偷偷收紧 PolyForm 原条款。详见[授权范围说明](docs/legal/LICENSE_SCOPE.md)。

**公开仍被阻止。** 本项目独立新根提交必须在其自身的精确版本上通过原生 Windows CI；历史项目的测试记录不可沿用。组件级代码来源与版权/通知审计、最终发布审批必须另行完成。在此之前保持 Private。

提 Issue 前请阅读[贡献说明](CONTRIBUTING.md)与[安全说明](SECURITY.md)，不要上传真实回执、凭据或私人源码。
