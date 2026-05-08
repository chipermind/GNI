"""GNI HTTP API surfaces.

Holds in-process aggregators that other framework layers (FastAPI app in
``apps/api``, Streamlit UI, internal probes) can call. Each module here is a
pure-stdlib builder that can be unit-tested without a web framework; an
optional framework adapter (FastAPI router) lives next to the builder when
the framework is installed.
"""
