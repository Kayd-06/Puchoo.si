"""Usage comes from provider responses; no guessed counts or shared-request state."""
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.core.llm_client import DeepSeekSQLClient, LLMClient, LocalMLXSQLClient, SQLGenerationError
from apps.core.token_usage import client_calls, summarize_calls, usage_offset
from apps.core.verification import ClaudeVerifier


def response(usage, content='SELECT 1'):
    return nullcontext(SimpleNamespace(read=lambda: json.dumps({'usage': usage, 'choices': [{'message': {'content': content}}]}).encode()))

@pytest.mark.parametrize('factory', [lambda: DeepSeekSQLClient(api_key='synthetic'), LocalMLXSQLClient])
def test_all_model_calls_accumulate_provider_usage(factory):
    client = factory()
    with patch('apps.core.llm_client.urllib.request.urlopen', side_effect=[response({'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}), response({'prompt_tokens': 40, 'completion_tokens': 10, 'total_tokens': 50})]):
        client._chat(system='s', user='plan')
        client._chat(system='s', user='sql')
    usage = summarize_calls(client_calls(client, 0, 'generation'))
    assert usage['input_tokens'] == 140
    assert usage['output_tokens'] == 30
    assert usage['total_tokens'] == 170
    assert usage['status'] == 'complete'
    assert len(usage['calls']) == 2
    assert 'plan' not in json.dumps(usage)


def test_usage_offsets_exclude_calls_from_previous_queries():
    client = DeepSeekSQLClient(api_key='synthetic')
    with patch('apps.core.llm_client.urllib.request.urlopen', return_value=response({'prompt_tokens': 10, 'completion_tokens': 5})):
        client._chat(system='s', user='first')
        start = usage_offset(client)
        client._chat(system='s', user='second')
    assert summarize_calls(client_calls(client, start, 'generation'))['total_tokens'] == 15


def test_cached_anthropic_input_is_counted_including_verification():
    sdk = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=20, cache_creation_input_tokens=30),
        content=[SimpleNamespace(text='SELECT 1')],
    )))
    client = LLMClient(client=sdk)
    client.generate_sql(schema='Table: t\nColumns: id (INTEGER)', question='List records')
    verifier = ClaudeVerifier(client=sdk)
    verifier.verify(question='List records', sql='SELECT 1', columns=[], rows=[], row_count=0)
    usage = summarize_calls(client_calls(client, 0, 'generation') + client_calls(verifier, 0, 'verification'))
    assert usage['total_tokens'] == 130
    assert usage['input_tokens'] == 120
    assert usage['output_tokens'] == 10


@pytest.mark.parametrize('metadata', [None, {}, {'prompt_tokens': -1, 'completion_tokens': True}, {'prompt_tokens': '100', 'completion_tokens': []}])
def test_malformed_usage_does_not_break_a_successful_answer(metadata):
    client = DeepSeekSQLClient(api_key='synthetic')
    with patch('apps.core.llm_client.urllib.request.urlopen', return_value=response(metadata)):
        assert client._chat(system='s', user='u') == 'SELECT 1'
    usage = summarize_calls(client_calls(client, 0, 'generation'))
    assert usage['total_tokens'] is None
    assert usage['status'] == 'unavailable'


def test_missing_usage_is_partial_not_a_fabricated_zero():
    client = DeepSeekSQLClient(api_key='synthetic')
    with patch('apps.core.llm_client.urllib.request.urlopen', side_effect=[response({'prompt_tokens': 10, 'completion_tokens': 5}), response(None)]):
        client._chat(system='s', user='u')
        client._chat(system='s', user='u')
    usage = summarize_calls(client_calls(client, 0, 'generation'))
    assert usage['status'] == 'partial'
    assert usage['total_tokens'] is None
    assert usage['reported_tokens'] == 15


def test_rejected_model_content_still_retains_reported_tokens():
    client = DeepSeekSQLClient(api_key='synthetic')
    with patch('apps.core.llm_client.urllib.request.urlopen', return_value=response({'prompt_tokens': 10, 'completion_tokens': 5}, content='')):
        with pytest.raises(SQLGenerationError):
            client._chat(system='s', user='u')
    assert summarize_calls(client_calls(client, 0, 'generation'))['total_tokens'] == 15


def test_unreported_http_retry_is_not_silently_excluded_from_total():
    import io
    import urllib.error
    client = DeepSeekSQLClient(api_key='synthetic')
    error = urllib.error.HTTPError('https://example.invalid', 400, 'bad request', {}, io.BytesIO(b'{"error":{"code":"json_validate_failed"}}'))
    with patch('apps.core.llm_client.urllib.request.urlopen', side_effect=[error, response({'prompt_tokens': 10, 'completion_tokens': 5})]):
        assert client._chat(system='s', user='u') == 'SELECT 1'
    usage = summarize_calls(client_calls(client, 0, 'generation'))
    assert usage['status'] == 'partial'
    assert usage['reported_tokens'] == 15
    assert len(usage['calls']) == 2


def test_transport_failure_after_a_successful_call_marks_usage_incomplete():
    import urllib.error
    client = DeepSeekSQLClient(api_key='synthetic')
    with patch('apps.core.llm_client.urllib.request.urlopen', side_effect=[response({'prompt_tokens': 10, 'completion_tokens': 5}), urllib.error.URLError('offline')]):
        client._chat(system='s', user='u')
        with pytest.raises(RuntimeError):
            client._chat(system='s', user='u')
    usage = summarize_calls(client_calls(client, 0, 'generation'))
    assert usage['status'] == 'partial'
    assert usage['reported_tokens'] == 15
