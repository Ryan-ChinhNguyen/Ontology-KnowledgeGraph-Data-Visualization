"""Shared building blocks for the API and Worker services.

Both services depend on the same database schema, the same message envelope,
and the same connection settings. Defining them once here keeps the two
services from drifting apart.
"""
