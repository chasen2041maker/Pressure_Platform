import unittest
from unittest import mock

from recovery_supervisor import serve


class SupervisorTests(unittest.TestCase):
    def process(self, code=None):
        result=mock.Mock()
        result.poll.return_value=code
        return result

    def test_web_exit_stops_independent_recovery(self):
        daemon,web=self.process(),self.process(2)
        stopped=mock.Mock();stopped.wait.return_value=False
        with mock.patch('recovery_supervisor.signal.signal'):
            self.assertEqual(serve(['web'],['daemon'],cwd='.',popen=mock.Mock(side_effect=[daemon,web]),stopped=stopped),2)
        daemon.terminate.assert_called_once()
        daemon.wait.assert_called_once()

    def test_daemon_is_restarted_but_crash_loop_terminates_web(self):
        daemons=[self.process(1) for _ in range(3)];web=self.process()
        stopped=mock.Mock();stopped.wait.return_value=False
        launch=mock.Mock(side_effect=[daemons[0],web,*daemons[1:]])
        with mock.patch('recovery_supervisor.signal.signal'):
            self.assertEqual(serve(['web'],['daemon'],cwd='.',popen=launch,stopped=stopped),1)
        self.assertEqual(launch.call_count,4)
        web.terminate.assert_called_once()

    def test_shutdown_stops_both_children_and_waits(self):
        daemon,web=self.process(),self.process()
        stopped=mock.Mock();stopped.wait.return_value=True
        with mock.patch('recovery_supervisor.signal.signal'):
            self.assertEqual(serve(['web'],['daemon'],cwd='.',popen=mock.Mock(side_effect=[daemon,web]),stopped=stopped),0)
        daemon.terminate.assert_called_once();web.terminate.assert_called_once()
