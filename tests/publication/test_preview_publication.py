"""Offline English/Chinese view and publication safety tests."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import publication_audit

class PublicPreviewTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT
        self.cli=self.root/'adapters/generic/run_bridge.py'
        self.sample=self.root/'schemas/manifest.example.json'

    def call(self,*args):
        x=subprocess.run([sys.executable,'-B',str(self.cli),'preview-permissions','--manifest',str(self.sample),*args],
                         capture_output=True,text=True,encoding='utf-8',errors='strict',timeout=35,cwd=self.root)
        self.assertEqual(x.returncode,0,x.stderr[:300])
        return x.stdout

    def test_permission_preview_remains_unauthorized(self):
        body=json.loads(self.call('--json'))
        for field in ('send_authorized','ledger_mutated','network_attempted','review_approved'):
            self.assertFalse(body[field],field)

    def test_locales_do_not_translate_protocol_json(self):
        en=self.call('--lang','en','--json')
        zh=self.call('--lang','zh-CN','--json')
        self.assertEqual(en,zh)

    def test_no_original_or_internal_content_in_allowlisted_tree(self):
        report=publication_audit.check(self.root)
        self.assertEqual(report['status'],'RULE_CHECKS_PASSED',report)
        self.assertFalse(report['release_approved'])

    def test_modified_license_fails_without_echoing_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'sample'
            shutil.copytree(self.root,root,ignore=shutil.ignore_patterns('.git','__pycache__'))
            path=root/'LICENSE'
            path.write_bytes(path.read_bytes()+b' X')
            report=publication_audit.check(root)
            self.assertEqual(report['status'],'REFUSED')
            self.assertIn('license_bytes',report['rule_violations'])
            self.assertNotIn('BEGIN PRIVATE',str(report))

    def test_secret_pattern_refuses_if_a_new_file_is_added(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'sample'
            shutil.copytree(self.root,root,ignore=shutil.ignore_patterns('.git','__pycache__'))
            key=root/'notices.txt'
            key.write_text('ghp_'+'A'*36,encoding='utf8')
            (root/'PUBLIC_FILELIST.txt').write_text('\n'.join(sorted([p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()]))+'\n')
            report=publication_audit.check(root)
            self.assertEqual(report['status'],'REFUSED')
            self.assertIn('secret_shape',report['rule_violations'])
            self.assertNotIn('ghp_',str(report))

    def test_internal_agent_document_is_refused_even_if_filelisted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'sample'
            shutil.copytree(self.root,root,ignore=shutil.ignore_patterns('.git','__pycache__'))
            (root/'AGENTS.md').write_text('internal setup',encoding='utf8')
            (root/'PUBLIC_FILELIST.txt').write_text('\n'.join(sorted([p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()]))+'\n')
            report=publication_audit.check(root)
            self.assertEqual(report['status'],'REFUSED')
            self.assertIn('forbidden_path',report['rule_violations'])

    def test_line_endings_are_pinned_without_approving_release(self):
        attributes=(self.root/'.gitattributes').read_text(encoding='utf-8')
        self.assertIn('* text=auto eol=lf', attributes)
        report=publication_audit.check(self.root)
        self.assertEqual(report['status'],'RULE_CHECKS_PASSED', report)
        self.assertFalse(report['release_approved'])
        names = (self.root/'PUBLIC_FILELIST.txt').read_text(encoding='utf-8').splitlines()
        self.assertEqual(report['source_items'], len(names))

    def test_clean_root_does_not_import_old_ci_results_or_grant_publication(self):
        readme=(self.root/'README.md').read_text(encoding='utf-8')
        zh=(self.root/'README.zh-CN.md').read_text(encoding='utf-8')
        manifest=json.loads((self.root/'capability-manifest.json').read_text(encoding='utf-8'))
        self.assertIn('publicly available', readme)
        self.assertIn('No formal versioned Release has been published', readme)
        self.assertNotIn('not approved for public release', readme)
        self.assertNotIn('Publication is not approved', readme)
        self.assertIn('Windows CI', readme)
        self.assertIn('Windows CI', zh)
        self.assertIn('尚未发布正式版本 Release', zh)
        self.assertNotIn('不是已批准的公开发布', zh)
        self.assertNotIn('公开发布尚未批准', zh)
        self.assertFalse(manifest['publication_authorized'])
        self.assertNotIn('SOURCE_VERIFIED_PREVIOUSLY', json.dumps(manifest))
        for path in ('README.md','README.zh-CN.md','docs/RESEARCH_USE.md','docs/MIGRATION_SCOPE.md'):
            body=(self.root/path).read_text(encoding='utf-8')
            self.assertNotRegex(body, r'/actions/runs/\d+')
            self.assertNotRegex(body, r'\b[0-9a-f]{40}\b')

    def test_online_evidence_does_not_certify_head_or_token_savings(self):
        manifest=json.loads((self.root/'capability-manifest.json').read_text(encoding='utf-8'))
        offload=manifest['online_offload']
        self.assertEqual(offload['status'], 'HISTORICAL_SINGLE_HOST_OBSERVATION')
        self.assertFalse(offload['certifies_current_tree'])
        self.assertEqual(offload['result_characters'], 325)
        self.assertEqual(offload['runtime_source_digest_at_collection'],
                         '4fe06dd107a243e016728081e311e8056f6fa12b05ee83d80632ac27ebcb9784')
        acceptance=manifest['synthetic_online_acceptance']
        self.assertEqual(acceptance['status'], 'SINGLE_HOST_SYNTHETIC_ONLINE_ACCEPTED')
        self.assertFalse(acceptance['certifies_current_tree'])
        self.assertFalse(acceptance['documentation_executed_acceptance'])
        self.assertEqual(acceptance['accepted_commit'], '4399582ddc500216cf4a6034f273946f2b79208d')
        self.assertEqual(acceptance['covers_only_runtime_digest'],
                         '68510a984a3182f487ff953d7eeca495c6457615694e29f33f931dd016569e39')
        self.assertEqual(acceptance['result_characters'], 296)
        self.assertEqual(acceptance['result_sha256'],
                         'e6e40e1bef2265a97e1d44a89395290132ae6af012b6f9233fdc82bea6b3d7e8')
        self.assertEqual(acceptance['send_attempts'], 1)
        self.assertEqual(acceptance['bounded_read_characters'], 120)
        self.assertTrue(acceptance['bounded_read_truncated'])
        self.assertFalse(acceptance['result_truncated'])
        self.assertEqual(acceptance['selection']['model_evidence_source'], 'preclaim_menu')
        self.assertTrue(acceptance['selection']['closed_control_showed_only_effort'])
        self.assertFalse(acceptance['selection']['closed_control_confirms_model'])
        self.assertFalse(acceptance['selection']['concurrent_model_switch_ruled_out'])
        self.assertEqual(manifest['model_or_fee_claim'], 'NOT_OBSERVABLE')
        sys.path.insert(0, str(self.root/'tools/pro_bridge'))
        import runtime_identity
        current=runtime_identity.mapping_digest(runtime_identity.inventory(self.root, runtime_identity.RUNTIME_ROOTS))
        self.assertNotEqual(current, offload['runtime_source_digest_at_collection'])
        if current != acceptance['covers_only_runtime_digest']:
            self.assertNotIn(current, json.dumps(acceptance))
        readme=(self.root/'README.md').read_text(encoding='utf-8')
        zh=(self.root/'README.zh-CN.md').read_text(encoding='utf-8')
        self.assertIn('does not certify this checkout', readme)
        self.assertIn('不能证明当前这份代码', zh)
        self.assertIn('A consumer project needs its own synthetic online acceptance', readme)
        self.assertIn('消费方项目需要自己做一次合成在线验收', zh)
        self.assertIn('offloadTask', readme)
        self.assertIn('recoverTask', zh)
        workflow=(self.root/'docs/ONLINE_WORKFLOW.md').read_text(encoding='utf-8')
        self.assertIn('HISTORICAL_SINGLE_HOST_OBSERVATION', workflow)
        self.assertIn('does not certify commit', workflow)
        self.assertIn('4399582ddc500216cf4a6034f273946f2b79208d', workflow)
        self.assertIn('certifies_current_tree', workflow)
        self.assertIn('stays false', workflow)
        for name in ('docs/INTEGRATION.md','docs/INTEGRATION.zh-CN.md','docs/ONLINE_WORKFLOW.md'):
            body=(self.root/name).read_text(encoding='utf-8')
            self.assertIn('browser_response', body)
            self.assertIn('recoverTask', body)
            self.assertNotIn('token savings achieved', body.lower())

if __name__=='__main__':unittest.main()
