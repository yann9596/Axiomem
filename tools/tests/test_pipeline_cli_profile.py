import argparse
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'skills/multica-context-handoff/scripts')]
import chandoff_adapter as adapter
import chandoff_note as note
import handoff_pipeline as pipeline


class CliProfileTests(unittest.TestCase):
    def test_read_transport_uses_requested_binary_profile_and_workspace(self):
        args=argparse.Namespace(executable='explicit-multica.exe',profile='approved',workspace_id='workspace')
        with patch.object(adapter,'_default_runner',return_value=(0,'{}','')) as call:
            cli=pipeline._configured_cli(args,adapter,'MulticaCli')
            cli.version()
        self.assertEqual(call.call_args.args[0][:5],['explicit-multica.exe','--profile','approved','--workspace-id','workspace'])
        self.assertEqual(call.call_args.args[0][5:],['version','--output','json'])

    def test_all_stages_accept_same_connection_scope(self):
        parser=pipeline.build_parser()
        required={
            'prepare':['--issue','I','--target-role','engineering-lead','--caller-role','engineering-lead','--purpose','planning'],
            'finalize':['--plan-file','P','--result-file','R','--request-file','Q'],
            'selfcheck':[],
            'publish':['--issue','I','--result-file','R','--prepared-by','M'],
        }
        for stage,more in required.items():
            args=parser.parse_args([stage,*more,'--executable','explicit.exe','--profile','p','--workspace-id','w'])
            self.assertEqual((args.executable,args.profile,args.workspace_id),('explicit.exe','p','w'))

    def test_profile_configuration_does_not_broaden_read_allowlist(self):
        args=argparse.Namespace(executable='explicit.exe',profile='p',workspace_id='w')
        with patch.object(adapter,'_default_runner') as call:
            cli=pipeline._configured_cli(args,adapter,'MulticaCli')
            with self.assertRaises(adapter.AdapterError):
                cli._run(['issue','update','I','--status','done'])
            call.assert_not_called()


if __name__=='__main__':unittest.main()
