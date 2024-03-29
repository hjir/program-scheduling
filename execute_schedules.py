# from __future__ import annotations
import logging
import os
import time
from argparse import ArgumentParser
from dataclasses import dataclass
from typing import Dict, List

import netsquid as ns
import numpy as np
import pandas as pd
from netsquid.qubits import ketstates
from qoala.lang.ehi import UnitModule
from qoala.lang.parse import QoalaParser
from qoala.lang.program import QoalaProgram
from qoala.runtime.config import LatenciesConfig, ProcNodeConfig, ProcNodeNetworkConfig, TopologyConfig, LinkConfig, \
    LinkBetweenNodesConfig
from qoala.runtime.environment import NetworkInfo
from qoala.runtime.program import BatchInfo, BatchResult, ProgramInput
from qoala.runtime.schedule import TaskSchedule, TaskScheduleEntry
from qoala.sim.build import build_network

from program_scheduling.datasets import create_dataset
from program_scheduling.node_schedule import NodeSchedule
from setup_logging import setup_logging

HOST_INSTR_TIME = 100_000
HOST_PEER_LATENCY = 10_000_000
QNOS_INSTR_TIME = 100_000


def create_network_info(names: List[str]) -> NetworkInfo:
    env = NetworkInfo.with_nodes({i: name for i, name in enumerate(names)})
    env.set_global_schedule([0, 1, 2])
    env.set_timeslot_len(1e6)
    return env


def create_procnode_cfg(name: str, id: int, num_qubits: int, perfect_params=False) -> ProcNodeConfig:
    if perfect_params:
        topology = TopologyConfig.perfect_config_uniform_default_params(num_qubits)
    else:
        topology = TopologyConfig.from_file("configs/qoala_topology_config.yaml")
    return ProcNodeConfig(
        node_name=name,
        node_id=id,
        topology=topology,
        latencies=LatenciesConfig(
            host_instr_time=HOST_INSTR_TIME, host_peer_latency=HOST_PEER_LATENCY, qnos_instr_time=QNOS_INSTR_TIME
        ),
    )


def load_program(path: str) -> QoalaProgram:
    path = os.path.join(os.path.dirname(__file__), path)
    with open(path) as file:
        text = file.read()
    return QoalaParser(text).parse()


def create_batch(
    program: QoalaProgram,
    unit_module: UnitModule,
    inputs: List[ProgramInput],
    num_iterations: int,
) -> BatchInfo:
    return BatchInfo(
        program=program,
        unit_module=unit_module,
        inputs=inputs,
        num_iterations=num_iterations,
        deadline=0,
    )


def create_task_schedule(tasks, node_schedule_config):
    """
    To fix the length of QC blocks, this method can in the future also take in network schedule (or have the relevant
    information in the node schedule) and return the changed tasks as well.
    """
    node_schedule = pd.read_csv(node_schedule_config)

    end_times = [node_schedule["start_time"][i] + node_schedule["duration"][i] for i in range(len(node_schedule["index"]))]
    temp = []
    for i in range(len(node_schedule["index"])):
        if end_times.count(node_schedule["start_time"][i]) == 1:
            prev_index = end_times.index(node_schedule["start_time"][i])
            if node_schedule["type"][i][0] != node_schedule["type"][prev_index][0]:
                temp.append((node_schedule["start_time"][i], node_schedule["index"][i], prev_index))
            else:
                # there is only one task which finishes before this one but it's the same type
                temp.append((node_schedule["start_time"][i], node_schedule["index"][i], None))
        else:
            # there is no prev task
            temp.append((node_schedule["start_time"][i], node_schedule["index"][i], None))
    # sort tuples according to start time
    temp.sort()

    # indices of tasks in the order they start in the node schedule
    indices = [i for (_, i, _) in temp]
    start_times = [s for (s, _, _) in temp]
    # prev[i] is an index of tasks that needs to be defined as previous to task i
    prev = [p for (_, _, p) in temp]

    schedule = TaskSchedule([
        TaskScheduleEntry(tasks[indices[i]], timestamp=start_times[i],
                          prev=tasks[prev[i]] if prev[i] is not None else None)
        for i in range(len(tasks))])

    return schedule


def create_network_cfg(alice_cfg, bob_cfg):
    link_config = LinkConfig.from_file("configs/qoala_link_config.yaml")
    links = [LinkBetweenNodesConfig(node_id1=alice_cfg.node_id, node_id2=bob_cfg.node_id, link_config=link_config)]

    return ProcNodeNetworkConfig(nodes=[alice_cfg, bob_cfg], links=links)


def execute_node_schedule(dataset, node_schedule_name, seed=0, perfect_params=False):
    logger.debug(f"Executing {node_schedule_name}")
    ns.sim_reset()
    ns.set_qstate_formalism(ns.qubits.qformalism.QFormalism.DM)

    network_info = create_network_info(names=["alice", "bob"])
    alice_id = network_info.get_node_id("alice")
    bob_id = network_info.get_node_id("bob")

    alice_node_cfg = create_procnode_cfg("alice", alice_id, num_qubits=2, perfect_params=perfect_params)
    bob_node_cfg = create_procnode_cfg("bob", bob_id, num_qubits=2, perfect_params=perfect_params)

    if perfect_params:
        network_cfg = ProcNodeNetworkConfig.from_nodes_perfect_links(
            nodes=[alice_node_cfg, bob_node_cfg], link_duration=20_000_000
        )
    else:
        network_cfg = create_network_cfg(alice_node_cfg, bob_node_cfg)
    network = build_network(network_cfg, network_info)
    alice_procnode = network.nodes["alice"]
    bob_procnode = network.nodes["bob"]

    for (path, num_iterations) in dataset.items():
        alice_program = load_program(path + "_alice.iqoala")
        session = path.split("/")[-1]

        # different inputs based on program to be used
        if session == "qkd":
            # theta should be either 0 (Z basis meas) or 24 (X basis measurement) for rot Y rotation
            alice_inputs = [ProgramInput(
                {"bob_id": bob_id, "theta0": 0, "theta1": 24, "theta2": 0}
            ) for _ in range(num_iterations)]
        elif session == "pingpong":
            np.random.seed(seed)
            bases = np.random.choice([0, 16], num_iterations)
            alice_inputs = [ProgramInput({"bob_id": bob_id, "theta": bases[i]}) for i in range(num_iterations)]
        elif session == "bqc":
            alice_inputs = [ProgramInput(
                # angles are in multiples of pi / 16
                {"bob_id": bob_id, "alpha": 8, "beta": 8, "theta1": 0, "theta2": 0}
            ) for _ in range(num_iterations)]
        else:
            raise ValueError(f"Unknown session type {session}")

        alice_unit_module = UnitModule.from_full_ehi(alice_procnode.memmgr.get_ehi())
        alice_batch = create_batch(alice_program, alice_unit_module, alice_inputs, num_iterations)
        alice_procnode.submit_batch(alice_batch)

    alice_procnode.initialize_processes()
    alice_tasks = alice_procnode.scheduler.get_tasks_to_schedule()

    alice_schedule = create_task_schedule(alice_tasks, "node_schedules/" + node_schedule_name + "-alice.csv")
    alice_procnode.scheduler.upload_schedule(alice_schedule)

    for (path, num_iterations) in dataset.items():
        bob_program = load_program(path + "_bob.iqoala")
        session = path.split("/")[-1]

        if session == "qkd":
            # theta should be either 0 (Z basis meas) or 24 (X basis measurement) for rot Y rotation
            bob_inputs = [ProgramInput(
                # TODO: use kwargs or default values?
                {"alice_id": alice_id, "theta0": 0, "theta1": 24, "theta2": 0}
            ) for _ in range(num_iterations)]
        elif session == "pingpong":
            bob_inputs = [ProgramInput({"alice_id": alice_id}) for _ in range(num_iterations)]
        elif session == "bqc":
            bob_inputs = [ProgramInput({"alice_id": alice_id}) for _ in range(num_iterations)]
        else:
            raise ValueError(f"Unknown session type {session}")

        bob_unit_module = UnitModule.from_full_ehi(bob_procnode.memmgr.get_ehi())
        bob_batch = create_batch(bob_program, bob_unit_module, bob_inputs, num_iterations)
        bob_procnode.submit_batch(bob_batch)

    bob_procnode.initialize_processes()
    bob_tasks = bob_procnode.scheduler.get_tasks_to_schedule()

    bob_schedule = create_task_schedule(bob_tasks, "node_schedules/" + node_schedule_name + "-bob.csv")
    bob_procnode.scheduler.upload_schedule(bob_schedule)

    network.start()
    ns.sim_run()

    alice_results = alice_procnode.scheduler.get_batch_results()
    bob_results = bob_procnode.scheduler.get_batch_results()
    makespan = ns.sim_time()

    return NodeScheduleResult(alice_results, bob_results, makespan)


@dataclass
class NodeScheduleResult:
    alice_results: Dict[int, BatchResult]
    bob_results: Dict[int, BatchResult]
    makespan: float


def save_success_metrics(node_schedule_name, success_metrics, schedule_type, n_qoala_runs, risk_aware):
    # there will be a saved file for each combination of dataset, number of sessions, and session type
    # node schedule name is e.g. node-schedule_sessions-6_dataset-0_schedule-HEU_length-3_NS-75_role
    parts = node_schedule_name.split("_")
    n_sessions = int(parts[1].split("-")[1])
    dataset_id = int(parts[2].split("-")[1])
    assert schedule_type == parts[3].split("-")[1]
    length_factor = int(parts[4].split("-")[1])
    ns_id = parts[5].split("-")[1]

    filename = f"qoala-results-node-schedule_sessions-{n_sessions}_dataset-{dataset_id}_schedule-{schedule_type}"

    metadata = {
        "qoala_run_index": list(range(n_qoala_runs)),
        "dataset_id": [dataset_id] * n_qoala_runs,
        "n_sessions": [n_sessions] * n_qoala_runs,
        "ns_id": [ns_id] * n_qoala_runs,
        "schedule_type": [schedule_type] * n_qoala_runs,
        "risk_aware": [risk_aware] * n_qoala_runs,
        "length_factor": [length_factor] * n_qoala_runs
    }

    df = pd.DataFrame(data={**metadata, **success_metrics},
                      columns=list(metadata.keys()) + list(success_metrics.keys()))

    if os.path.isfile(f"results/{filename}.csv"):  # if the file exists, append
        old_df = pd.read_csv(f"results/{filename}.csv")
        new_df = pd.concat([old_df, df], ignore_index=True)
        new_df.to_csv(f"results/{filename}.csv", index=False)
    else:  # otherwise make new file
        df.to_csv(f"results/{filename}.csv", index=False)


def evaluate_node_schedule(node_schedule_name, perfect_params):
    # node_schedule_name is `node-schedule_sessions-6_dataset-1_schedule-HEU_length-3_NS-1_role`
    parts = node_schedule_name.split("_")
    dataset_id = int(parts[2].split("-")[1])
    n_sessions = int(parts[1].split("-")[1])
    dataset = create_dataset(dataset_id=dataset_id, n_sessions=n_sessions)

    if parts[5].split("-")[1] == "None":
        seed = 0
    else:
        seed = int(parts[5].split("-")[1])
    result = execute_node_schedule(dataset=dataset, node_schedule_name=node_schedule_name, seed=seed,
                                   perfect_params=perfect_params)

    if result is None:
        # execution of the node schedule failed (TODO: why does this happen?)
        sm = {"success_probability": 0, "makespan": 0}
        for (session, _) in \
                create_dataset(dataset_id=dataset_id, n_sessions=n_sessions, only_session_name=True).items():
            sm.update({f"success_probability_{session}": 0})
        return sm

    successful_sessions = {}
    for (path, alice_batch_result, bob_batch_result) in zip(dataset.keys(), result.alice_results.values(),
                                                            result.bob_results.values()):
        session = path.split("/")[-1]

        logger.debug(f"\n{session}:\nAlice's results: {alice_batch_result}\nBob's results: {bob_batch_result}\n")

        if session == "bqc":
            # m2 should be measurement in Z basis of the eff computation H Rz(beta) H Rz(alpha) |+>
            for i, program_result in enumerate(bob_batch_result.results):
                bob_measures_expected_outcome = program_result.values["m2"] == 0
                successful_bqc_session = bob_measures_expected_outcome
                if successful_bqc_session:
                    successful_sessions.update({"bqc": successful_sessions.get("bqc", 0) + 1})
                logger.debug(f"The BQC session #{i} was successful" if successful_bqc_session
                             else f"The BQC session #{i} failed")

        if session == "pingpong":
            np.random.seed(seed)
            bases = np.random.choice([0, 16], len(alice_batch_result.results))
            for i, program_result in enumerate(alice_batch_result.results):
                # Alice always prepares state |1> to teleport
                alice_measures_correct_outcome = program_result.values["outcome"] == (0 if bases[i] == 0 else 1)
                successful_pingpong_session = alice_measures_correct_outcome
                if successful_pingpong_session:
                    successful_sessions.update({"pingpong": successful_sessions.get("pingpong", 0) + 1})
                logger.debug(f"The PP session #{i} was successful" if successful_pingpong_session
                             else f"The PP #{i} session failed")

        if session == "qkd":
            for i, (alice, bob) in enumerate(zip(alice_batch_result.results, bob_batch_result.results)):
                default_thetas = {
                    "alice_theta0": 0, "alice_theta1": 24, "alice_theta2": 0, "alice_theta3": 24, "alice_theta4": 0,
                    "bob_theta0": 0, "bob_theta1": 24, "bob_theta2": 0, "bob_theta3": 24, "bob_theta4": 0,
                }
                same_meas_outcomes = all(alice.values[v] == bob.values[v] for v in ["m0", "m1", "m2"])
                # these are just sanity checks
                alice_gets_correct_thetas = all(alice.values[v] == default_thetas[v] for v in
                                                ["bob_theta" + str(t) for t in [0, 1, 2]])
                bob_gets_correct_thetas = all(bob.values[v] == default_thetas[v] for v in
                                              ["alice_theta" + str(t) for t in [0, 1, 2]])
                successful_qkd_session = same_meas_outcomes and alice_gets_correct_thetas and bob_gets_correct_thetas
                if successful_qkd_session:
                    successful_sessions.update({"qkd": successful_sessions.get("qkd", 0) + 1})
                logger.debug(f"The QKD session #{i} was successful" if successful_qkd_session
                             else f"The QKD session #{i} failed")

    success_metrics = {
        "success_probability": sum(successful_sessions.values()) / n_sessions,
        "makespan": result.makespan
    }

    for (session, iterations) in \
            create_dataset(dataset_id=dataset_id, n_sessions=n_sessions, only_session_name=True).items():
        success_metrics.update({f"success_probability_{session}": successful_sessions.get(session, 0) / iterations})

    return success_metrics


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument('-d', '--dataset-id', required=False, type=int,
                        help="Dataset IDs of schedules to be executed.")
    parser.add_argument("--all", dest="all", default=False, action="store_true",
                        help="Schedules for all datasets should be executed.")
    parser.add_argument('-s', '--n_sessions', required=False, default=6, type=int,
                        help="Total number of sessions in a dataset.")
    parser.add_argument("-n", '--n_qoala_runs', required=False, default=100, type=int,
                        help="How many networks schedules should be created.")
    parser.add_argument('--no_ns', dest="no_ns", action="store_true",
                        help="The schedules to be executed were created without network schedule constraints.")
    parser.add_argument('--opt', dest="opt", action="store_true",
                        help="The schedules to be executed were scheduled in an optimal fashion.")
    parser.add_argument('--naive', dest="naive", action="store_true",
                        help="The schedules to be executed were scheduled in a naive fashion.")
    parser.add_argument('--risk_aware', dest="risk_aware", action="store_true",
                        help="Use a risk-aware extension of Qoala execution.")
    parser.add_argument('--perfect_params', dest="perfect_params", action="store_true",
                        help="Use perfect parameters.")
    parser.add_argument('--log', dest='loglevel', type=str, required=False, default="INFO",
                        help="Set logging level: DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    args, unknown = parser.parse_known_args()

    setup_logging(args.loglevel)
    logger = logging.getLogger("program_scheduling")

    if args.dataset_id is None and not args.all:
        raise ValueError("No dataset ID was specified and the `--all` flag is not set.")

    dataset_ids = range(7) if args.all else [args.dataset_id]
    schedule_type = "OPT" if args.opt else ("NAIVE" if args.naive else "HEU")

    start = time.time()
    for dataset_id in dataset_ids:
        logger.info(f"EXECUTING NODE SCHEDULES FOR DATASET {dataset_id}")
        for node_schedule_name in NodeSchedule.get_relevant_node_schedule_names(dataset_id, args.n_sessions,
                                                                                schedule_type=schedule_type):
            logger.debug(f"Executing node schedule {node_schedule_name}")
            if args.no_ns and "NS-None" not in node_schedule_name:
                continue
            elif not args.no_ns and "NS-None" in node_schedule_name:
                continue

            success_metrics = {}
            for _ in range(args.n_qoala_runs):
                sm = evaluate_node_schedule(node_schedule_name=node_schedule_name,
                                            perfect_params=args.perfect_params)
                for (k, v) in sm.items():
                    success_metrics.update({k: success_metrics.get(k, []) + [v]})

            save_success_metrics(node_schedule_name=node_schedule_name, success_metrics=success_metrics,
                                 schedule_type=schedule_type, n_qoala_runs=args.n_qoala_runs,
                                 risk_aware=args.risk_aware)

            succ_prob = 0 if success_metrics["success_probability"][0] is None \
                else round(np.mean(success_metrics['success_probability']), 2) * 100
            logger.info(f"Executed {args.n_qoala_runs} qoala runs for {node_schedule_name[:-5]} with average "
                        f"{succ_prob}% success probability.")

    end = time.time()
    logger.info("Time taken to finish: %.4f seconds" % (end - start))
