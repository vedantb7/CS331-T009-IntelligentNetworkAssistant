"""Evaluation harness for the INA v2 assistant (Khushi's component).

Runs the assistant against scripted scenarios through the in-memory mock server,
records what happened, and writes CSV results plus a summary. Because the
rule-based parser and the mock are deterministic, the numbers reproduce exactly.
"""
