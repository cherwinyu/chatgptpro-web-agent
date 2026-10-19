# Third-party material and attribution

This repository (`cherwinyu/chatgptpro-web-agent`) is a **publicly available, limited noncommercial research preview**. These notes are not a certification that historical upstream rights have been resolved, and they are not a formal Release. No formal versioned Release has been published. The repository name does not license OpenAI marks or imply endorsement. Public source availability does not permit use of third-party services.

- The standard **PolyForm Noncommercial License 1.0.0** is included unmodified at `LICENSE` and `docs/legal/LICENSE`; its license text is controlled by its own publisher. PolyForm Noncommercial is not OSI Open Source and is not an OSI-approved open-source license.
- The included Python modules were statically inspected for imports. The currently allowlisted modules use Python standard-library and sibling project modules; no separately bundled external Python wheel/vendor directory was identified in this preview.
- The included JavaScript host-observer source has no separately bundled third-party npm distribution in this preview. `adapters/generic/browser_host.mjs` has no package import. It still requires appropriate browser-host support for any experimental use.
- A static import scan of this preview found Python standard-library modules and sibling project modules only. No `requirements.txt`, `package.json`, or vendored wheel was in the tracked set. This scan does not identify copied excerpts that lack an import.
- Git, Python, Node.js, GitHub Actions and the selected service platforms are **tools invoked externally**, not bundled third-party library copies. Their names do not imply affiliation, endorsement, or service authorization. OpenAI marks and hosted ChatGPT access are not licensed by this repository.

**These observations do not independently prove originality of every code fragment or establish a license for any embedded third-party excerpt.** Third-party rights are not cleared by this file. Any newly identified outside material must be accompanied by its required license/attribution, or excluded, before it is added. Keep source-rights and brand gates separate from a green CI check and from public source availability.

中文：以上是当前有限源码清单中可观察的依赖及授权说明，不代表已经验证所有代码片段完全原创，也不代表第三方权利已经核清。本仓库源码已公开；目前尚未发布正式版本 Release。源码公开不代表获得 OpenAI 官方背书或第三方服务使用授权。仓库名 `chatgptpro-web-agent` 不是 OpenAI 产品名，也不构成背书。PolyForm Noncommercial 不是 OSI Open Source。新增材料在纳入前应继续核实第三方源代码、署名及通知义务；未获得许可的内容应排除或替换。非商业研究不等于自动获得外部服务、商标或 OpenAI 许可。源码公开不能代替这些审查。
