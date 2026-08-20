"""
Permanent regression test suite (unittest, stdlib only -- no new
dependencies). See docs/architecture.md "Regression Test Suite" for what
this covers and why it exists: several real fixes made during the first
real-hardware validation cycle were verified only with one-off scratch
scripts and never captured durably in the repo. These tests exist so a
future change cannot silently reintroduce any of them.

Run with:
    python -m unittest discover -s tests -v

Nothing here touches real hardware -- every test uses fakes/mocks for
SMU/DAQ/relay sessions and a temporary SQLite file for storage tests.
"""
