"""Rules for comparing the fresh-install and upgrade schemas (no database needed)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    'check_schema_paths', Path(__file__).resolve().parents[1] / 'scripts' / 'check_schema_paths.py')
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def test_matching_schemas_need_no_allowlist():
    assert check.compare({'a', 'b'}, {'b', 'a'}, set()) == ([], [])


def test_a_new_difference_fails_unless_it_is_allowlisted():
    unexpected, stale = check.compare({'a', 'b'}, {'a'}, set())
    assert unexpected == ['fresh-only: b'] and stale == []
    assert check.compare({'a', 'b'}, {'a'}, {'fresh-only: b'}) == ([], [])


def test_an_allowlisted_difference_that_is_gone_is_flagged_as_stale():
    unexpected, stale = check.compare({'a'}, {'a'}, {'upgrade-only: c'})
    assert unexpected == [] and stale == ['upgrade-only: c']


def test_allowlist_file_ignores_comments_and_blank_lines(tmp_path):
    path = tmp_path / 'allow.txt'
    path.write_text('# reason\n\nfresh-only: b\n')
    assert check.load_allowlist(path) == {'fresh-only: b'}
