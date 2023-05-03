from argparse import ArgumentParser

import yaml
from qoala.lang.ehi import UnitModule
from qoala.runtime.config import ProcNodeNetworkConfig
from qoala.runtime.program import ProgramInput
from qoala.sim.build import build_network

from execute_schedules import create_network_info, create_batch, load_program, create_procnode_cfg


def retrieve_tasks(config, save_filename=None, num_qubits=3):
    if save_filename is None:
        save_filename = config

    network_info = create_network_info(names=["bob", "alice"])
    alice_id = network_info.get_node_id("alice")
    bob_id = network_info.get_node_id("bob")

    alice_node_cfg = create_procnode_cfg("alice", alice_id, num_qubits)
    bob_node_cfg = create_procnode_cfg("bob", bob_id, num_qubits)

    network_cfg = ProcNodeNetworkConfig.from_nodes_perfect_links(
        nodes=[alice_node_cfg, bob_node_cfg], link_duration=380_000
    )
    network = build_network(network_cfg, network_info)
    alice_procnode = network.nodes["alice"]
    bob_procnode = network.nodes["bob"]

    alice_program = load_program("configs/" + config + "_alice.iqoala")
    # this is not necessarily correct but should not matter for retrieving the blocks information
    alice_inputs = [ProgramInput({"bob_id": bob_id})]

    alice_unit_module = UnitModule.from_full_ehi(alice_procnode.memmgr.get_ehi())
    alice_batch = create_batch(
        alice_program, alice_unit_module, alice_inputs, 1
    )
    alice_procnode.submit_batch(alice_batch)
    alice_procnode.initialize_processes()
    alice_tasks = alice_procnode.scheduler.get_tasks_to_schedule()

    with open('configs/' + save_filename + '_alice.yml', 'w') as outfile:
        yaml.dump({"session_id": "TODO",
                   "app_deadline": "TODO",
                   "blocks": [{bt.block_name: {"type": bt.typ.name, "duration": int(bt.duration), "CS": "TODO"}}
                              for bt in alice_tasks]}, outfile, default_flow_style=False, sort_keys=False)

    bob_program = load_program("configs/" + config + "_bob.iqoala")
    bob_inputs = [ProgramInput({"alice_id": alice_id})]

    bob_unit_module = UnitModule.from_full_ehi(bob_procnode.memmgr.get_ehi())
    bob_batch = create_batch(bob_program, bob_unit_module, bob_inputs, 1)
    bob_procnode.submit_batch(bob_batch)
    bob_procnode.initialize_processes()
    bob_tasks = bob_procnode.scheduler.get_tasks_to_schedule()

    with open('configs/' + save_filename + '_bob.yml', 'w') as outfile:
        yaml.dump({"session_id": "TODO",
                   "app_deadline": "TODO",
                   "blocks": [{bt.block_name: {"type": bt.typ.name, "duration": int(bt.duration), "CS": "TODO"}}
                              for bt in bob_tasks]}, outfile, default_flow_style=False, sort_keys=False)


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('config', type=str, help="Name of the iqoala program (without the `_alice` or `_bob` suffix).")
    parser.add_argument('-s', '--save_filename', required=False, type=str, default=None,
                        help="Name of the file to save results in.")

    args, unknown = parser.parse_known_args()
    retrieve_tasks(args.config, args.save_filename)
