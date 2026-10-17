"""Read-only research-policy and entry-language checks; no license grant.

This checks explicit declarations and file consistency, not actual user purpose,
translation/legal accuracy, third-party authorization or inherited ownership.
"""
from __future__ import annotations
import json
from pathlib import Path
import deploy
import runtime_identity as identity

POLICY_PATH = "docs/legal/research-use-policy.json"
POLICY_ID = "research-noncommercial-v1"
FALSE_FIELDS = ("commercial_use_permitted", "production_use_permitted", "official_product_substitute",
                "third_party_authorization_granted", "rights_cleared", "distribution_license_granted",
                "publication_authorized", "osi_open_source")
SECTIONS = ("purpose", "research-scope", "exclusions", "authorization", "rights", "classification",
            "limitations", "privacy", "sources")


def read_json(root: Path, name: str) -> dict:
    if not identity.safe_name(name):
        raise ValueError("invalid research metadata path")
    path = deploy._safe_location(root / name)
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError("regular research metadata required")
    with path.open("rb") as stream:
        raw = stream.read(262145)
    if len(raw) > 262144:
        raise ValueError("research metadata too large")
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=identity._unique)
    if not isinstance(data, dict):
        raise ValueError("research metadata object required")
    return data


def check(root: Path, policy: dict) -> dict:
    root = deploy._safe_location(root)
    if policy.get("use_policy_path") != POLICY_PATH or policy.get("use_policy_id") != POLICY_ID:
        raise ValueError("current research policy reference is required")
    data = read_json(root, POLICY_PATH)
    if (type(data.get("schema_version")) is not int or data["schema_version"] != 1
            or data.get("policy_id") != POLICY_ID
            or data.get("status") != "PROJECT_USE_POLICY_NOT_A_LICENSE_GRANT"
            or data.get("intended_use") != "NONCOMMERCIAL_RESEARCH_ONLY"
            or any(data.get(key) is not False for key in FALSE_FIELDS)):
        raise ValueError("research use policy conflicts with the owner direction")
    license_record = policy.get("license", {})
    if not isinstance(license_record, dict):
        raise ValueError("license record must be an object")
    status = license_record.get("status")
    if (status not in {"NOT_GRANTED", "OWNER_SCOPED_NONCOMMERCIAL_GRANTED"}
            or license_record.get("spdx") is not None
            or license_record.get("recommended_spdx") is not None):
        raise ValueError("restricted research direction cannot activate a permissive grant")
    if status == "OWNER_SCOPED_NONCOMMERCIAL_GRANTED":
        if (license_record.get("license_id") != "PolyForm-Noncommercial-1.0.0"
                or data.get("owner_scoped_rights_license_granted") is not True
                or data.get("project_wide_distribution_clearance") is not False):
            raise ValueError("owner-scoped research license fields mismatch")
    elif data.get("owner_scoped_rights_license_granted") is True:
        raise ValueError("owner grant has no reviewed legal text")
    docs = {"en": "docs/legal/RESEARCH_USE.md", "zh-CN": "docs/legal/RESEARCH_USE.zh-CN.md"}
    if data.get("documents") != docs:
        raise ValueError("both research notices must be packaged")
    hashes = {}
    for name in docs.values():
        path = deploy._safe_location(root / name)
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        if any(text.count('<a id="' + section + '"></a>') != 1 for section in SECTIONS):
            raise ValueError("research notice is missing a required section")
        hashes[name] = identity.digest(raw)
    languages = read_json(root, "docs/languages.json")
    if (languages.get("languages") != ["en", "zh-CN"]
            or languages.get("default_machine_language") != "en"):
        raise ValueError("unsupported language contract")
    pairs = languages.get("pairs")
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 64:
        raise ValueError("bounded language pairs required")
    seen = set()
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) != {"en", "zh-CN"}:
            raise ValueError("invalid language pair")
        for name in pair.values():
            if not identity.safe_name(name) or name in seen or not identity.in_scope(name):
                raise ValueError("invalid or duplicate translated document")
            seen.add(name)
            path = deploy._safe_location(root / name)
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                raise ValueError("empty translated entry document")
    if not set(docs.values()).issubset(seen):
        raise ValueError("research notices missing from language index")
    return {"policy_id": POLICY_ID, "intended_use": "NONCOMMERCIAL_RESEARCH_ONLY",
            **{key: False for key in FALSE_FIELDS}, "language_pairs_checked": len(pairs),
            "notice_sha256": hashes, "legal_clearance": "NOT_ATTESTED",
            "usage_enforcement": "DECLARATIVE_NOT_A_SANDBOX"}


def render_preview(report: dict, language: str) -> str:
    """Human-only presentation; never translate original data or protocol fields."""
    if language not in {"en", "zh-CN"}:
        raise ValueError("unsupported display language")
    english = language == "en"
    title = "PERMISSION PREVIEW" if english else "权限预览"
    warning = ("Non-commercial research only. Not for production or an official product replacement.\n"
               "Owner-scoped rights only; no third-party service authorization or legal immunity.") if english else (
               "仅限非商业研究，不用于生产，不替代官方或正式产品。\n"
               "仅就有权处分的成果授予非商业许可；不授予第三方权限，也不构成免责保证。")
    labels = {} if english else {
        "command_succeeded": "命令完成", "state": "原任务状态", "task_id": "任务标识",
        "source_repository": "源码仓库", "source_commit": "源提交", "read_targets": "请求读取范围",
        "result_repository": "结果仓库", "result_branch": "结果分支", "result_paths": "结果文件",
        "result_writer": "外部结果写入者", "host": "宿主", "input_binding": "输入绑定",
        "send_attempts": "已消费发送次数", "send_authorized": "是否授予发送许可",
        "review_approved": "是否批准", "ledger_mutated": "是否修改账本", "network_attempted": "是否联网",
        "error": "拒绝原因", "do_not_resend": "不得重发", "request_sha256": "原请求哈希"}
    lines = [title, warning, ""]
    for key in sorted(report):
        lines.append(labels.get(key, key) + ": " + json.dumps(report[key], ensure_ascii=False, sort_keys=True))
    lines.append("\nFor automation use --json; this view never authorizes a send." if english else
                 "\n自动化使用 --json；此预览永远不授予发送资格。")
    return "\n".join(lines)
