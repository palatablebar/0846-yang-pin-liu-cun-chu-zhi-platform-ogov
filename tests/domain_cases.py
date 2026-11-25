from app.catalog import SPEC
from app.domain.calculate import calculate_rows
from app.domain.decision import decide_rows
from app.domain.prepare import prepare_rows
from app.intake.service import load

CLEAN = [{'sample_id': 'S1',
  'study_id': 'N1',
  'collected_at': '2025-01-31',
  'retention_months': '12',
  'consent_until': '',
  'legal_hold': 'N',
  'disposed_at': '2026-03-10',
  'storage': '-80C'},
 {'sample_id': 'S2',
  'study_id': 'N1',
  'collected_at': '2026-01-15',
  'retention_months': '12',
  'consent_until': '',
  'legal_hold': 'N',
  'disposed_at': '',
  'storage': '-80C'},
 {'sample_id': 'S3',
  'study_id': 'N2',
  'collected_at': '2024-01-01',
  'retention_months': '6',
  'consent_until': '',
  'legal_hold': 'Y',
  'disposed_at': '',
  'storage': 'LN2'},
 {'sample_id': 'S4',
  'study_id': 'N2',
  'collected_at': '2026-01-15',
  'retention_months': '24',
  'consent_until': '2026-06-30',
  'legal_hold': 'N',
  'disposed_at': '2026-08-05',
  'storage': '-80C'},
 {'sample_id': 'S5',
  'study_id': 'N3',
  'collected_at': '2026-10-31',
  'retention_months': '12',
  'consent_until': '',
  'legal_hold': 'N',
  'disposed_at': '',
  'storage': '4C'},
 {'sample_id': 'S6',
  'study_id': 'N3',
  'collected_at': '2020-01-01',
  'retention_months': '1',
  'consent_until': '',
  'legal_hold': 'N',
  'disposed_at': '2022-01-05',
  'storage': '-80C'}]


def row(**changes):
    item = dict(CLEAN[0])
    item.update(changes)
    return item


def calc(rows):
    items, issues = prepare_rows(rows)
    return calculate_rows(items), issues


def run(rows):
    items, issues = prepare_rows(rows)
    return decide_rows(calculate_rows(items)), issues


def run_profile(profile):
    return run(load(profile)["rows"])
