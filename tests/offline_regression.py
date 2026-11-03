"""Offline regression checks; no model requests or game input. Run with -B."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from ck3mcp.core import Config, Session, Stopped, Replan
from ck3mcp.journal import Journal
from ck3mcp.tasks import CouncilTask, ProgressTracker
from ck3mcp.verification import interpret
from ck3mcp.runner import Runner
from ck3mcp.model import VisionModel

def facts(**updates):
    f = dict(panel='council', panel_evidence='visible', council_visible=True,
        candidates_visible=False, slot=None, vacant=None, vacancy_evidence=None,
        visible_candidates=[], list_position=None, inspected_candidate=None,
        inspected_details={}, selected_candidate=None, selection_confirmed=False,
        eligible=None, confirmation_visible=False, candidate_evidence=None,
        appointed_slot=None, appointed_candidate=None, appointment_evidence=None)
    f.update(updates)
    return f

def verdict(**updates):
    return dict(status='success', evidence='visible result', facts=facts(**updates))

class Regression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s = Session(Config(run_directory=self.temp.name, max_calls=30,
            max_tokens=1000000, max_steps=4, model='offline-test'))
        self.addCleanup(self.s.journal.close)

    def test_syntax_all_modules(self):
        for path in (ROOT / 'ck3mcp').glob('*.py'):
            compile(path.read_text(encoding='utf-8-sig'), str(path), 'exec')

    def test_import_server(self):
        import ck3mcp.server
        import ck3mcp.windows

    def test_budget_group_atomic(self):
        self.s.cfg.max_calls = 1
        with self.assertRaises(Stopped): self.s.reserve_calls('action', 'final')
        self.assertEqual((self.s.calls, self.s.tokens, self.s.leases), (0, 0, {}))

    def test_stop_refunds_only_unconsumed(self):
        a, b = self.s.reserve_calls('action', 'final')
        reserve = self.s.consume_call(a)
        self.s.stop('manual_stop')
        self.s.release_call(b)
        self.s.settle_call(reserve, 123)
        self.assertEqual((self.s.calls, self.s.tokens, self.s.leases), (1, 123, {}))

    def test_reservation_log_failure_rolls_back(self):
        with patch.object(self.s, 'log', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.s.reserve_calls('action', 'final')
        self.assertEqual((self.s.tokens, self.s.leases), (0, {}))
        self.assertTrue(self.s.stop_event.is_set())

    def test_stop_log_failure_does_not_skip_cleanup(self):
        called = []
        with patch.object(self.s, 'log', side_effect=OSError('disk full')):
            self.s.stop('Ctrl+C')
        self.s.cleanup(lambda: (_ for _ in ()).throw(OSError()), lambda: called.append(True))
        self.assertEqual(called, [True])
        self.assertEqual(self.s.status, 'cancelled')

    def test_replan_limit_and_reset(self):
        self.s.replanning.rejected(Replan('changed'))
        self.s.replanning.dispatched()
        self.assertEqual(self.s.replanning.consecutive, 0)
        for _ in range(2): self.s.replanning.rejected(Replan('changed'))
        with self.assertRaises(Stopped): self.s.replanning.rejected(Replan('changed'))

    def test_wrong_target_and_missing_facts(self):
        task = CouncilTask(); task.index = 2; task.slot = 'steward'
        intent = dict(kind='select_candidate', slot='steward', candidate='A')
        result = interpret(task, verdict(slot='steward', selected_candidate='B'), intent)
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(task.candidate)
        self.assertEqual(interpret(task, dict(status='success', evidence='x', facts=None), intent)['status'], 'unknown')

    def test_progress_new_people_vs_stall(self):
        tracker = ProgressTracker()
        for name in ['A', 'B', 'C', 'D']:
            self.assertIsNone(tracker.observe({}, facts(visible_candidates=[name])))
        self.assertIsNone(tracker.observe({}, facts(visible_candidates=['D'])))
        self.assertEqual(tracker.observe({}, facts(visible_candidates=['D'])), 'no_task_progress')

    def test_journal_restart_and_final(self):
        data = dict(action_id='a', status='success')
        self.s.journal.record_action_verification('a', self.s.id, data, True)
        second = Journal(Path(self.temp.name) / 'actions.sqlite3')
        try: self.assertEqual(second.unresolved()[0]['state'], 'awaiting_final')
        finally: second.close()
        with self.assertRaises(RuntimeError): self.s.ensure_reconciled()
        outcome = dict(status='success', advanced=True, evidence='x', facts={}, error=None)
        self.s.journal.record_final_verification('a', self.s.id, data, {}, outcome, {})
        self.assertEqual(self.s.journal.unresolved(), [])
        self.assertEqual(data['journal_state'], 'verified')

    def test_failed_write_preserves_input_objects(self):
        data = dict(action_id='a', status='success')
        before = copy.deepcopy(data)
        with patch.object(self.s.journal, 'write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.s.journal.record_action_verification('a', self.s.id, data, True)
        self.assertEqual(data, before)
        self.s.journal.record_action_verification('a', self.s.id, data, True)
        before = copy.deepcopy(data)
        outcome = dict(status='success', advanced=True, evidence='x', facts={}, error=None)
        with patch.object(self.s.journal, 'write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.s.journal.record_final_verification('a', self.s.id, data, {}, outcome, {})
        self.assertEqual(data, before)
        self.assertEqual(self.s.journal.unresolved()[0]['state'], 'awaiting_final')

    def test_model_payload_failure_refunds(self):
        with patch.dict(os.environ, {'CK3_API_KEY': 'offline-placeholder'}): model = VisionModel(self.s)
        lease = self.s.reserve_call()
        with patch.object(model, '_payload', side_effect=OSError()):
            with self.assertRaises(OSError): model.ask('x', [], lease)
        self.assertEqual((self.s.calls, self.s.tokens, self.s.leases), (0, 0, {}))

    def run_scenario(self, replan=False, final='success', interrupt=False):
        s = self.s
        replies = []
        kinds = ['navigate', 'select_slot', 'select_candidate', 'submit']
        post = [verdict(), verdict(slot='steward', candidates_visible=True, vacant=True, vacancy_evidence='empty'),
            verdict(slot='steward', vacant=True, selected_candidate='A', eligible=True,
                    selection_confirmed=True, confirmation_visible=True, candidate_evidence='selected'),
            verdict(appointed_slot='steward', appointed_candidate='A', appointment_evidence='appointed')]
        for i, kind in enumerate(kinds):
            decision = dict(decision='act', paused=True, pause_evidence='paused', expected='result',
                irreversible=i==3, action=dict(type='click', x=1, y=1),
                task=dict(stage=CouncilTask.stages[i], panel='council', slot='steward' if i>=2 else None,
                          candidate='A' if i==3 else None, intent=dict(kind=kind, slot='steward', candidate='A')))
            if replan and i == 0: replies.append(copy.deepcopy(decision))
            replies.extend([decision, post[i]])
        replies.append(RuntimeError('offline final error') if final=='error' else
            verdict(appointed_slot='steward', appointed_candidate='A' if final=='success' else 'B', appointment_evidence='appointed'))
        class FakeModel:
            def __init__(self, session): pass
            def ask(self, instruction, observations, lease=None):
                lease = lease or s.reserve_call()
                reserve = s.consume_call(lease); s.settle_call(reserve, 10)
                answer = replies.pop(0)
                if isinstance(answer, Exception): raise answer
                return answer
        class FakeGame:
            closed = False
            attempts = 0
            inputs = 0
            def capture(self):
                if interrupt: raise KeyboardInterrupt()
                return dict(observation_id='offline', screenshot='unused')
            def execute(self, oid, action):
                self.attempts += 1
                if replan and self.attempts==1: raise Replan('changed')
                s.ensure_reconciled(); self.inputs += 1
                result = dict(action_id=str(self.inputs), status='unknown', action=action,
                    input_dispatched=True, execution_before=self.capture())
                s.journal.write(result['action_id'], s.id, 'dispatched', result)
                return result
            def close(self): self.closed = True
        game = FakeGame()
        with patch('ck3mcp.runner.VisionModel', FakeModel), patch.object(s, 'wait', lambda seconds: s.check()):
            Runner(s, game).run()
        self.assertTrue(game.closed)
        self.assertEqual(s.leases, {})
        return game, json.loads((s.path / 'report.json').read_text(encoding='utf-8'))

    def test_runner_last_step_final(self):
        game, report = self.run_scenario()
        self.assertEqual((game.inputs, report['steps'], report['status']), (4, 4, 'completed'))
        self.assertEqual(report['calls'], 9)
        self.assertEqual(report['unresolved'], [])

    def test_runner_replan_does_not_use_step(self):
        game, report = self.run_scenario(replan=True)
        self.assertEqual((game.attempts, report['steps'], report['status']), (5, 4, 'completed'))

    def test_runner_final_mismatch_blocks_restart(self):
        game, report = self.run_scenario(final='mismatch')
        self.assertEqual(report['unresolved'][0]['state'], 'unknown')
        with self.assertRaises(RuntimeError): self.s.ensure_reconciled()

    def test_runner_final_interrupt_blocks_restart(self):
        game, report = self.run_scenario(final='error')
        self.assertEqual(report['unresolved'][0]['state'], 'awaiting_final')

    def test_runner_ctrl_c(self):
        game, report = self.run_scenario(interrupt=True)
        self.assertEqual((report['status'], report['reason'], game.inputs), ('cancelled', 'Ctrl+C', 0))

if __name__ == '__main__':
    unittest.main(verbosity=2)
