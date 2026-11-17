"""Provider-reported model tokens. Never estimate or expose request contents."""
from typing import Any


def _field(value: Any, name: str):
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**12 else None


def record_usage(client: Any, usage: Any, *, provider: str, model: str) -> None:
    """Called once per parsed model response, including rejected model content."""
    input_tokens = _count(_field(usage, 'prompt_tokens'))
    if input_tokens is None:
        input_tokens = _count(_field(usage, 'input_tokens'))
        if input_tokens is not None and provider == 'anthropic':
            # Anthropic's uncached input excludes these two cache categories.
            for field in ('cache_read_input_tokens', 'cache_creation_input_tokens'):
                cached = _count(_field(usage, field))
                if cached is not None:
                    input_tokens += cached
    output_tokens = _count(_field(usage, 'completion_tokens'))
    if output_tokens is None:
        output_tokens = _count(_field(usage, 'output_tokens'))
    total_tokens = _count(_field(usage, 'total_tokens'))
    if input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    client.usage_calls.append({
        'provider': provider, 'model': model,
        'input_tokens': input_tokens, 'output_tokens': output_tokens,
        'total_tokens': total_tokens,
    })


def usage_offset(client: Any) -> int:
    calls = getattr(client, 'usage_calls', None)
    return len(calls) if isinstance(calls, list) else 0


def client_calls(client: Any, offset: int, stage: str) -> list[dict]:
    if client is None:
        return []
    calls = getattr(client, 'usage_calls', None)
    entries = calls[offset:] if isinstance(calls, list) else []
    if not entries:
        entries = [{'provider': 'unknown', 'model': None,
                    'input_tokens': None, 'output_tokens': None, 'total_tokens': None}]
    return [{**entry, 'stage': stage} for entry in entries]


def previous_calls(usage: Any) -> list[dict]:
    if isinstance(usage, dict) and isinstance(usage.get('calls'), list):
        return usage['calls']
    # Legacy proposals have no evidence of how many tokens were used.
    return [{'provider': 'unknown', 'model': None, 'stage': 'generation',
             'input_tokens': None, 'output_tokens': None, 'total_tokens': None}]


def summarize_calls(calls: list[dict]) -> dict:
    known = [call for call in calls if _count(call.get('total_tokens')) is not None]
    complete = len(known) == len(calls)
    def total(field):
        values = [_count(call.get(field)) for call in calls]
        return sum(values) if all(value is not None for value in values) else None
    return {
        'status': 'complete' if complete else 'partial' if known else 'unavailable',
        'input_tokens': total('input_tokens'),
        'output_tokens': total('output_tokens'),
        'total_tokens': total('total_tokens'),
        'reported_tokens': sum(call['total_tokens'] for call in known),
        'calls': calls,
        'scope': 'Planning, SQL generation, repairs and verification model calls. Excludes translation, speech and embeddings.',
    }
