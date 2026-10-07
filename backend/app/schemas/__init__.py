"""Pydantic models.

- ``common``      IDs, UTC timestamp helpers, shared enums and text field types
- ``errors``      the error envelope
- ``sessions``, ``profiles``, ``evidence``, ``jobs``, ``generations``
                  request and response models of the HTTP API
- ``documents``   what is stored in MongoDB, with ``to_api`` conversions
"""
