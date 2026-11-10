import unittest
from investment_agents.llm.provider import classify_provider_exception

class ProviderClassificationTests(unittest.TestCase):
    def test_windows_socket_permission_is_tcp_failure(self):
        self.assertEqual(classify_provider_exception(PermissionError(10013, 'socket access forbidden'), model='x').code, 'TCP_CONNECT_FAILURE')

if __name__ == '__main__': unittest.main()
