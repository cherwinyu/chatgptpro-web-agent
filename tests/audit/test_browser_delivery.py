"""Real ledger/browser-response contract tests; synthetic browser observations only."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True


class BrowserDeliveryTests(unittest.TestCase):
    def call(self, *args):
        p = subprocess.run([sys.executable, '-B', str(ROOT/'adapters/generic/run_bridge.py'),
                            *args, '--ledger', str(self.root/'ledger')], capture_output=True, encoding='utf-8')
        return p.returncode, json.loads(p.stdout)

    def save(self, name, value):
        path = self.root/name
        path.write_text(json.dumps(value), encoding='utf-8')
        return str(path)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        task = self.root/'task.txt'
        task.write_text('Review this synthetic function: def add(a,b): return a-b', encoding='utf-8')
        self.target = dict(mode='CHAT', project_id=None, conversation_id='NEW', browser_context='test',
                           tab_id='synthetic', model_label='Observed model', effort_label='Observed effort')
        code, self.prepared = self.call('prepare', '--task-type', 'RESEARCH', '--repository', 'example/repo',
            '--source-sha', 'a'*40, '--source-ref', 'main', '--orchestration', 'synthetic',
            '--delivery', 'browser_response', '--task-file', str(task),
            '--browser-target', self.save('target.json', self.target))
        self.assertEqual(code, 0, self.prepared)
        self.receipt = self.save('receipt.json', self.prepared)
        self.task_id = self.prepared['task_id']
        self.ledger = self.root/'ledger/ledger.json'
        self.record = json.loads(self.ledger.read_text())['tasks'][self.task_id]
        self.manifest = json.loads(self.record['request_bytes'])

    def claim(self):
        observation = self.target | dict(observed_at=time.time(), logged_in=True, composer_ready=True,
            selection_unambiguous=True, mode_evidence='synthetic', project_evidence='synthetic',
            conversation_evidence='synthetic', page_url='https://chatgpt.com/')
        return self.call('claim', '--task', self.task_id, '--browser-observation', self.save('obs.json', observation))

    def submit(self):
        self.assertEqual(self.claim()[0], 0)
        self.assertEqual(self.call('mark-submitted', '--task', self.task_id, '--session-url',
            'https://chatgpt.com/c/synthetic', '--evidence', 'synthetic')[0], 0)

    def result(self):
        return dict(task_id=self.task_id, nonce=self.manifest['nonce'], request_sha256=self.prepared['request_sha256'],
            status='PAGE_COMPLETE', browserId='test', tabId='synthetic', url='https://chatgpt.com/c/synthetic',
            responseIds='synthetic-response', observed_at=time.time(), text='The function subtracts instead of adding.')

    def collect(self, result=None, receipt=None):
        return self.call('collect-browser', '--original-receipt', receipt or self.receipt,
                         '--result', self.save('result.json', result or self.result()))

    def test_prompt_and_at_most_once(self):
        self.assertIn('return a-b', self.prepared['prompt'])
        self.assertNotIn('write_only:', self.prepared['prompt'])
        self.assertEqual(self.claim()[0], 0)
        self.assertNotEqual(self.claim()[0], 0)

    def test_collect_persist_and_idempotent_recovery(self):
        self.submit()
        code, value = self.collect()
        self.assertEqual(code, 0, value)
        self.assertEqual(value['delivery_integrity'], 'BROWSER_OBSERVED')
        self.assertFalse(value['review_approved'])
        record = json.loads(self.ledger.read_text())['tasks'][self.task_id]
        self.assertEqual(record['browser_result']['text'], self.result()['text'])
        self.assertEqual(self.collect()[0], 0)

    def test_unknown_submission_cannot_collect(self):
        self.claim()
        self.assertNotEqual(self.collect()[0], 0)

    def test_wrong_identity_tab_url_and_stale_result(self):
        self.submit()
        for key, value in [('nonce', '0'*32), ('tabId', 'other'), ('url', 'https://chatgpt.com/g/other/c/synthetic'),
                           ('observed_at', 0), ('text', ''), ('status', 'RUNNING')]:
            before = self.ledger.read_bytes()
            self.assertNotEqual(self.collect(self.result() | {key: value})[0], 0, key)
            self.assertEqual(self.ledger.read_bytes(), before)

    def test_receipt_mismatch_never_adopted(self):
        self.submit()
        wrong = self.prepared['receipt'] | {'ledger_id': '0'*64}
        self.assertNotEqual(self.collect(receipt=self.save('wrong.json', wrong))[0], 0)

    def test_changed_response_cannot_overwrite(self):
        self.submit()
        self.assertEqual(self.collect()[0], 0)
        self.assertNotEqual(self.collect(self.result() | {'text': 'different'})[0], 0)
        self.assertNotEqual(self.collect(self.result() | {'responseIds': 'different-response'})[0], 0)

    def test_expired_deadline_cannot_be_extended_by_collection(self):
        self.submit()
        data = json.loads(self.ledger.read_text(encoding='utf-8'))
        data['tasks'][self.task_id]['deadline_at'] = 1
        self.ledger.write_text(json.dumps(data), encoding='utf-8')
        before = self.ledger.read_bytes()
        code, body = self.collect()
        self.assertNotEqual(code, 0)
        self.assertEqual(body['error'], 'browser_collection_not_allowed')
        self.assertTrue(body['do_not_resend'])
        self.assertEqual(self.ledger.read_bytes(), before)
        saved = json.loads(self.ledger.read_text(encoding='utf-8'))['tasks'][self.task_id]
        self.assertEqual(saved['state'], 'SUBMITTED')
        self.assertEqual(saved['send_attempts'], 1)
        self.assertNotIn('browser_result', saved)

    def test_other_repository_cannot_share_the_ledger(self):
        before = self.ledger.read_bytes()
        code, body = self.call('prepare', '--task-type', 'RESEARCH', '--repository', 'other/repo',
            '--source-sha', 'b' * 40, '--source-ref', 'main', '--orchestration', 'other-project')
        self.assertNotEqual(code, 0)
        self.assertEqual(body['error'], 'ledger_repository_mismatch')
        self.assertFalse(body['ledger_mutated'])
        self.assertTrue(body['do_not_resend'])
        self.assertFalse(body['replacement_prepared'])
        self.assertEqual(self.ledger.read_bytes(), before)
        code, same = self.call('prepare', '--task-type', 'RESEARCH', '--repository', 'example/repo',
            '--source-sha', 'c' * 40, '--source-ref', 'main', '--orchestration', 'same-project')
        self.assertEqual(code, 0, same)
        tasks = json.loads(self.ledger.read_text(encoding='utf-8'))['tasks']
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[self.task_id]['state'], 'PREPARED')

    def test_browser_delivery_cannot_be_git_approved(self):
        self.submit()
        self.assertNotEqual(self.call('verify', '--task', self.task_id, '--remote',
            'https://github.com/example/repo.git')[0], 0)


if __name__ == '__main__':
    unittest.main()
