import pytest

from activity_metadata import ActivityMetadata, ActiveSet
from session_metadata import SessionMetadata
from unittest import *


class TestActivityMetadata(TestCase):

    def setUp(self):
        self.sm = SessionMetadata("test_config.yaml")
        self.am = ActivityMetadata(self.sm)

    def test_activity_metadata_correct(self):
        self.assertEqual(self.am.n_blocks, len(self.sm.blocks))
        self.assertEqual(self.am.successors, [[1], [2], []])
        self.assertEqual(self.am.resource_reqs, [[1, 0], [0, 1], [0, 1]])
        self.assertEqual(self.am.durations, [1, 2, 1])  # TODO: this should change if you consider NS
        self.assertEqual(self.am.d_min, [0, 0, 0])
        self.assertEqual(self.am.d_max, [16, 16, 0])


class TestActiveSet(TestCase):

    def test_create_active_set_wrong_ids(self):
        with pytest.raises(ValueError):
            ActiveSet.create_active_set(["test_config.yaml"], [1, 2, 3])

    def test_merging_active_sets(self):
        a = ActiveSet.create_active_set(["test_config.yaml"], [[1, 2]])
        self.assertEqual(a.n_blocks, 6)
        self.assertEqual(a.successors, [[1], [2], [], [4], [5], []])
        self.assertEqual(a.resource_reqs, [[1, 0], [0, 1], [0, 1]] * 2)
        self.assertEqual(a.durations, [1, 2, 1] * 2)
        self.assertEqual(a.d_min, [0, 0, 0] * 2)
        self.assertEqual(a.d_max, [16, 16, 0] * 2)
