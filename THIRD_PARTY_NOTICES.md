# Third-party material and attribution

This repository is an **unreleased, limited noncommercial research preview**, not a certification that all historical upstream rights have been resolved.

- The standard **PolyForm Noncommercial License 1.0.0** is included unmodified at `LICENSE` and `docs/legal/LICENSE`; its license text is controlled by its own publisher.
- The included Python modules were statically inspected for imports. The currently allowlisted modules use Python standard-library and sibling project modules; no separately bundled external Python wheel/vendor directory was identified in this preview.
- The included JavaScript host-observer source has no separately bundled third-party npm distribution in this preview. `adapters/generic/browser_host.mjs` has no package import. It still requires appropriate browser-host support for any experimental use.
- A static import scan of this preview found Python standard-library modules and sibling project modules only. No `requirements.txt`, `package.json`, or vendored wheel was in the tracked set. This scan does not identify copied excerpts that lack an import.
- Git, Python, Node.js, GitHub Actions and the selected service platforms are **tools invoked externally**, not bundled third-party library copies. Their names do not imply affiliation or service authorization.

**These observations do not independently prove originality of every code fragment or establish a license for any embedded third-party excerpt.** Any newly identified outside material must be reviewed before public release and accompanied by its required license/attribution, or excluded. Keep source-rights and brand gates separate from a green CI check.

中文：以上是当前有限源码清单中可观察的依赖及授权说明，不代表已经验证所有代码片段完全原创。正式公开前应继续核实第三方源代码、署名及通知义务；未获得许可的内容应排除或替换。非商业研究不等于自动获得外部服务或商标许可。
