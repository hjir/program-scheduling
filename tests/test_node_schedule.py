from activity_metadata import ActiveSet
from node_schedule import NodeSchedule
from unittest import *


class TestNodeSchedule(TestCase):

    def setUp(self):
        """
        The values defined in this method are equivalent to this node schedule:

        CPU: [00][06]--------[03][04][05][09][10][11]
        QPU: ----[  01  ][02][  07  ][08]------------
             |   |   |   |   |   |   |   |   |   |
             0   1   2   3   4   5   6   7   8   9
        """
        resources = [[1, 0], [0, 1], [0, 1], [1, 0], [1, 0], [1, 0],
                     [1, 0], [0, 1], [0, 1], [1, 0], [1, 0], [1, 0]]
        types = ["CL", "QC", "QL", "CC", "CC", "CC"] * 2
        durations = [1, 2, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1]
        self.active = ActiveSet(12, [], [], resources, types, durations, [], [], [], 0)
        self.start_times = [0, 1, 3, 4, 5, 6, 1, 4, 6, 7, 8, 9]
        self.ns = NodeSchedule(self.active, self.start_times)

    def test_node_schedule_correct(self):
        self.assertEqual(self.ns._CPU_activities, [0, 6, -1, -1, 3, 4, 5, 9, 10, 11])
        self.assertEqual(self.ns._QPU_activities, [-1, 1, 1, 2, 7, 7, 8, -1, -1, -1])
        self.assertEqual(self.ns.get_makespan(), 10)

    def test_PUF(self):
        self.assertEqual(self.ns.get_PUF_CPU(), 8/10)
        self.assertEqual(self.ns.get_PUF_QPU(), 6/10)
        self.assertEqual(self.ns.get_PUF_both(), 10/10)
