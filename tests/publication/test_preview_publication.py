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
        self.assertEqual(report['source_items'], 57)

    def test_clean_root_does_not_import_old_ci_results_or_grant_publication(self):
        readme=(self.root/'README.md').read_text(encoding='utf-8')
        zh=(self.root/'README.zh-CN.md').read_text(encoding='utf-8')
        manifest=json.loads((self.root/'capability-manifest.json').read_text(encoding='utf-8'))
        self.assertIn('not approved for public release', readme)
        self.assertIn('Windows CI', readme)
        self.assertIn('Windows CI', zh)
        self.assertFalse(manifest['publication_authorized'])
        self.assertNotIn('SOURCE_VERIFIED_PREVIOUSLY', json.dumps(manifest))
        for path in ('README.md','README.zh-CN.md','docs/RESEARCH_USE.md','docs/MIGRATION_SCOPE.md'):
            body=(self.root/path).read_text(encoding='utf-8')
            self.assertNotRegex(body, r'/actions/runs/\d+')
            self.assertNotRegex(body, r'\b[0-9a-f]{40}\b')

if __name__=='__main__':unittest.main()
