"""AI providers.

- ``base``               AIProvider protocol, Usage, ProviderError types
- ``extraction_models``  LLM-facing models for extraction and job analysis
- ``generation_models``  LLM-facing models for generation and verification
- ``factory``            build_provider / get_provider
- ``openai/``            real provider (SDK wrapper in ``client``)
- ``fake/``              deterministic provider for tests and demo mode
"""
