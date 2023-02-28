import time

from pycsp3 import *
from node_schedule import NodeSchedule
from activity_metadata import ActiveSet
from network_schedule import NetworkSchedule

if __name__ == '__main__':
    # TODO: network_schedule and input for create_active_set as command line arguments
    # network_schedule = NetworkSchedule([1, 10], [3, 3], [1, 2])
    network_schedule = NetworkSchedule()

    active = ActiveSet.create_active_set(["../session_configs/qkd.yaml"], [[1, 2]], network_schedule)
    # active = ActiveSet.create_active_set(["../session_configs/qkd.yaml", "../session_configs/bqc-client.yaml"],
    #                                      [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]])

    """
        TODO: How to define length of node_schedule?
        If it's too low, there might not be any feasible solution. 
        If it's too high, we are unnecessarily solving a more complex problem. 
    """
    schedule_size = 2 * sum(active.durations)
    capacities = [1, 1]  # capacity of [CPU, QPU]

    # x[i] is the starting time of the ith job
    x = VarArray(size=active.n_blocks, dom=range(schedule_size))
    # TODO: already set the initial domains more efficiently??

    # taken from http://pycsp.org/documentation/models/COP/RCPSP/
    def cumulative_for(k):
        origins, lengths, heights = zip(*[(x[i], active.durations[i], active.resource_reqs[i][k])
                                          for i in range(active.n_blocks) if active.resource_reqs[i][k] > 0])
        return Cumulative(origins=origins, lengths=lengths, heights=heights)

    # constraints
    satisfy(
        # precedence constraints
        [x[i] + active.durations[i] <= x[j] for i in range(active.n_blocks) for j in active.successors[i]],
        # resource constraints
        [cumulative_for(k) <= capacity for k, capacity in enumerate(capacities)],
        # constraints for max time lags
        [(x[i + 1] - (x[i] + active.durations[i])) <= active.d_max[i + 1] for i in range(active.n_blocks - 1)],
        # constraint for min time lags
        [active.d_min[i+1] <= (x[i+1] - (x[i] + active.durations[i])) for i in range(active.n_blocks - 1)],
        # network-schedule constraints (all quantum communication blocks adhere to network schedule if it's defined)
        [(x[i] == network_schedule.get_session_start_time(active.ids[i]) for i in range(active.n_blocks - 1)
          if network_schedule.is_defined and active.types[i] == "QC")]
    )

    # optional objective function
    if variant("makespan"):
        minimize(
            Maximum([x[i] + active.durations[i] for i in range(active.n_blocks)])
        )

    instance = compile()
    ace = solver(ACE)

    # https://github.com/xcsp3team/pycsp3/blob/master/docs/optionsSolvers.pdf
    # heuristics = {"valh": "max"}
    heuristics = {}

    print(f"\nTrying to construct a node schedule of length {schedule_size}")
    start = time.time()
    result = ace.solve(instance, dict_options=heuristics)
    end = time.time()

    if status() is SAT:
        ns = NodeSchedule(active.n_blocks, solution().values, active.durations, active.resource_reqs)
        ns.print()
        print("\nTime taken to finish: %.4f seconds" % (end - start))
    else:
        print("\nNo feasible node schedule was found. "
              "Consider making the length of node schedule longer or finding a better network schedule.")
        print("\nTime taken to finish: %.4f seconds" % (end - start))
