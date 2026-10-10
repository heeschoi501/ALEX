"""Unsupported thinking must not make an installed chat model unusable."""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from jarvis.app import provider_runtime_settings
from jarvis.providers import Message, OllamaProvider, ProviderError


def rejected(detail='does not support thinking', status=400):
    return urllib.error.HTTPError('http://localhost/api/chat', status, 'Bad Request', {},
                                  io.BytesIO(json.dumps({'error': detail}).encode()))


def response(stream=False):
    data = {'message': {'content': 'OK'}, 'done': True}
    return io.BytesIO((json.dumps(data) + ('\n' if stream else '')).encode())


class ThinkingCompatibilityTests(unittest.TestCase):
    def test_nonstream_retries_without_thinking_and_remembers_per_provider(self):
        provider = OllamaProvider('mistral-large:123b', think=True)
        with patch('jarvis.providers.urllib.request.urlopen', side_effect=[rejected(), response(), response()]) as call:
            self.assertEqual(provider.generate([Message('user', 'hello')]), 'OK')
            self.assertEqual(provider.generate([Message('user', 'again')]), 'OK')
        payloads = [json.loads(c.args[0].data) for c in call.call_args_list]
        self.assertTrue(payloads[0]['think'])
        self.assertNotIn('think', payloads[1])
        self.assertNotIn('think', payloads[2])
        self.assertEqual(payloads[0]['messages'], payloads[1]['messages'])
        self.assertFalse(provider_runtime_settings(provider)['think'])
        other = OllamaProvider('gpt-oss:120b', think=True)
        self.assertTrue(provider_runtime_settings(other)['think'])
        self.assertTrue(other._chat_payload([Message('user', 'hi')], stream=True)['think'])

    def test_stream_retry_preserves_tools_and_emits_answer_once(self):
        provider = OllamaProvider('mistral-large:123b', think=True)
        tools = [{'type': 'function', 'function': {'name': 'web_search'}}]
        with patch('jarvis.providers.urllib.request.urlopen', side_effect=[rejected(), response(True)]) as call:
            self.assertEqual(list(provider.generate_stream([Message('user', 'hi')], tools=tools)), ['OK'])
        retry = json.loads(call.call_args_list[1].args[0].data)
        self.assertNotIn('think', retry)
        self.assertEqual(retry['tools'], tools)

    def test_repeated_rejection_does_not_loop(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                provider = OllamaProvider('model', think=True)
                with patch('jarvis.providers.urllib.request.urlopen', side_effect=[rejected(), rejected()]) as call:
                    with self.assertRaises(ProviderError):
                        if stream:
                            list(provider.generate_stream([Message('user', 'hi')]))
                        else:
                            provider.generate([Message('user', 'hi')])
                self.assertEqual(call.call_count, 2)

    def test_other_errors_are_not_retried(self):
        for stream in (False, True):
            for status, detail in ((400, 'invalid options'), (500, 'insufficient memory')):
                with self.subTest(stream=stream, status=status):
                    provider = OllamaProvider('model', think=True)
                    with patch('jarvis.providers.urllib.request.urlopen', side_effect=rejected(detail, status)) as call:
                        with self.assertRaises(ProviderError):
                            if stream:
                                list(provider.generate_stream([Message('user', 'hi')]))
                            else:
                                provider.generate([Message('user', 'hi')])
                    self.assertEqual(call.call_count, 1)
                    self.assertTrue(provider_runtime_settings(provider)['think'])

    def test_false_flag_is_also_omitted_if_server_rejects_it(self):
        provider = OllamaProvider('model', think=False)
        with patch('jarvis.providers.urllib.request.urlopen', side_effect=[rejected(), response()]) as call:
            self.assertEqual(provider.generate([Message('user', 'hi')]), 'OK')
        self.assertNotIn('think', json.loads(call.call_args_list[1].args[0].data))
