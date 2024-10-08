import copy
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx
import numpy as np
from pymatgen.analysis.graphs import MoleculeGraph, MolGraphSplitError
from pymatgen.analysis.local_env import OpenBabelNN, metal_edge_extender#, oxygen_edge_extender
from pymatgen.core.structure import Molecule
from networkx.algorithms.graph_hashing import weisfeiler_lehman_graph_hash
from HiPRGen.constants import ROOM_TEMP, metals
from itertools import permutations, product


class FragmentComplex:
    def __init__(
        self, number_of_fragments, number_of_bonds_broken, bonds_broken, fragment_hashes
    ):

        self.number_of_fragments = number_of_fragments
        self.number_of_bonds_broken = number_of_bonds_broken
        self.bonds_broken = bonds_broken
        self.fragment_hashes = fragment_hashes


class MoleculeEntry:
    """
    A molecule entry class to provide easy access to Molecule properties.

    Args:
        molecule: Molecule of interest.
        energy: Electronic energy of the molecule in Hartree.
        enthalpy: Enthalpy of the molecule (kcal/mol). Defaults to None.
        entropy: Entropy of the molecule (cal/mol.K). Defaults to None.
        entry_id: An optional id to uniquely identify the entry.
        mol_graph: MoleculeGraph of the molecule.
    """

    def __init__(
        self,
        molecule,
        energy,
        enthalpy,
        entropy,
        entry_id,
        mol_graph,
        partial_charges_resp,
        partial_charges_mulliken,
        partial_charges_nbo,
        electron_affinity,
        ionization_energy,
        spin_multiplicity,
        partial_spins_nbo,
    ):
        self.energy = energy
        self.enthalpy = enthalpy
        self.entropy = entropy
        self.electron_affinity = electron_affinity
        self.ionization_energy = ionization_energy
        self.spin_multiplicity = spin_multiplicity

        self.ind = None
        self.entry_id = entry_id

        self.star_hashes = {}
        self.fragment_data = []

        if not mol_graph:
            mol_graph = MoleculeGraph.with_local_env_strategy(molecule, OpenBabelNN())
            self.mol_graph = metal_edge_extender(mol_graph)
            # self.mol_graph = oxygen_edge_extender(mol_graph)
        else:
            self.mol_graph = mol_graph

        self.partial_charges_resp = partial_charges_resp
        self.partial_charges_mulliken = partial_charges_mulliken
        self.partial_charges_nbo = partial_charges_nbo
        self.partial_spins_nbo = partial_spins_nbo

        self.molecule = self.mol_graph.molecule
        self.graph = self.mol_graph.graph.to_undirected()
        self.species = [str(s) for s in self.molecule.species]

        self.m_inds = [i for i, x in enumerate(self.species) if x in metals]

        # penalty gets used in the non local part of species filtering.
        # certain species filters will increase penalty rather than explicitly filtering
        # out a molecule. The non local filtering step prioritizes mols with a lower
        # penalty.
        self.penalty = 0
        self.covalent_graph = copy.deepcopy(self.graph)
        self.covalent_graph.remove_nodes_from(self.m_inds)

        self.formula = self.molecule.composition.alphabetical_formula
        self.charge = self.molecule.charge
        self.num_atoms = len(self.molecule)

        self.atom_locations = [site.coords for site in self.molecule]

        self.free_energy = self.get_free_energy()

        self.non_metal_atoms = [
            i for i in range(self.num_atoms) if self.species[i] not in metals
        ]

    @classmethod
    def from_dataset_entry(
        cls,
        doc: Dict,
        use_thermo: str = "raw",
    ):
        """
        Initialize a MoleculeEntry from a document in the LIBE (Lithium-Ion
        Battery Electrolyte) or MADEIRA (MAgnesium Dataset of Electrolyte and
        Interphase ReAgents) datasets.

        Args:
            doc: Dictionary representing an entry from LIBE or MADEIRA
            use_thermo: One of "raw" (meaning raw, uncorrected thermo data will
                be used), "rrho_shifted" (meaning that a slightly modified
                Rigid-Rotor Harmonic Oscillator approximation will be used -
                see Ribiero et al., J. Phys. Chem. B 2011, 115, 14556-14562), or
                "qrrho" (meaning that Grimme's Quasi-Rigid Rotor Harmonic
                Oscillator - see Grimme, Chem. Eur. J. 2012, 18, 9955-9964) will
                be used.
        """

        thermo = use_thermo.lower()

        if thermo not in ["raw", "rrho_shifted", "qrrho"]:
            raise ValueError(
                "Only allowed values for use_thermo are 'raw', 'rrho_shifted', "
                "and 'qrrho'!"
            )
        try:
            if isinstance(doc["molecule"], Molecule):
                molecule = doc["molecule"]
            else:
                molecule = Molecule.from_dict(doc["molecule"])  # type: ignore

            if (
                thermo == "rrho_shifted"
                and doc["thermo"]["shifted_rrho_eV"] is not None
            ):
                energy = doc["thermo"]["shifted_rrho_eV"]["electronic_energy"]
                enthalpy = doc["thermo"]["shifted_rrho_eV"]["total_enthalpy"]
                entropy = doc["thermo"]["shifted_rrho_eV"]["total_entropy"]
            elif thermo == "qrrho" and doc["thermo"]["quasi_rrho_eV"] is not None:
                energy = doc["thermo"]["quasi_rrho_eV"]["electronic_energy"]
                enthalpy = doc["thermo"]["quasi_rrho_eV"]["total_enthalpy"]
                entropy = doc["thermo"]["quasi_rrho_eV"]["total_entropy"]
            else:
                energy = doc["thermo"]["raw"]["electronic_energy_Ha"] * 27.21139
                enthalpy = doc["thermo"]["raw"]["total_enthalpy_kcal/mol"] * 0.0433641
                entropy = doc["thermo"]["raw"]["total_entropy_cal/molK"] * 0.0000433641

            entry_id = doc["molecule_id"]

            if isinstance(doc["molecule_graph"], MoleculeGraph):
                mol_graph = doc["molecule_graph"]
            else:
                mol_graph = MoleculeGraph.from_dict(doc["molecule_graph"])

            partial_charges_resp = doc["partial_charges"]["resp"]
            partial_charges_mulliken = doc["partial_charges"]["mulliken"]
            spin_multiplicity = doc["spin_multiplicity"]

            if doc["number_atoms"] == 1:
                partial_charges_nbo = doc["partial_charges"]["mulliken"]
                partial_spins_nbo = doc["partial_spins"]["mulliken"]
            else:
                partial_charges_nbo = doc["partial_charges"]["nbo"]
                partial_spins_nbo = doc["partial_spins"]["nbo"]

            electron_affinity_eV = None
            ionization_energy_eV = None
            if "redox" in doc:
                if "electron_affinity_eV" in doc["redox"]:
                    electron_affinity_eV = doc["redox"]["electron_affinity_eV"]

                if "ionization_energy_eV" in doc["redox"]:
                    ionization_energy_eV = doc["redox"]["ionization_energy_eV"]

        except KeyError as e:
            raise Exception(
                "Unable to construct molecule entry from molecule document; missing "
                f"attribute {e} in `doc`."
            )

        return cls(
            molecule=molecule,
            energy=energy,
            enthalpy=enthalpy,
            entropy=entropy,
            entry_id=entry_id,
            mol_graph=mol_graph,
            partial_charges_resp=partial_charges_resp,
            partial_charges_mulliken=partial_charges_mulliken,
            partial_charges_nbo=partial_charges_nbo,
            electron_affinity=electron_affinity_eV,
            ionization_energy=ionization_energy_eV,
            spin_multiplicity=spin_multiplicity,
            partial_spins_nbo=partial_spins_nbo,
        )

    @classmethod
    def from_mp_doc(cls, doc: Dict):
        """
        Construct a MoleculeEntry based on a document generated by emmet for
            the Materials Project.

        :param doc: A dict representation of an emmet document (SummaryDoc)
        :return: MoleculeEntry
        """
        solvent_key = None
        if isinstance(doc["molecule"], Molecule):
            molecule = doc["molecule"]
        else:
            molecule = Molecule.from_dict(doc["molecule"])  # type: ignore
        if isinstance(doc["electronic_energy"], float):
            energy = doc["electronic_energy"]
            enthalpy = doc["total_enthalpy"]
            entropy = doc["total_entropy"]
        else:
            solvent_key = list(doc["electronic_energy"].keys())[0]
            energy = doc["electronic_energy"][solvent_key]
            enthalpy = doc["total_enthalpy"][solvent_key]
            entropy = doc["total_entropy"][solvent_key]
        entry_id = doc["molecule_id"]
        if "nbo" in doc["molecule_graph"]:
            if isinstance(doc["molecule_graph"]["nbo"], MoleculeGraph):
                mol_graph = doc["molecule_graph"]["nbo"]
            else:
                mol_graph = MoleculeGraph.from_dict(doc["molecule_graph"]["nbo"])
        elif "OpenBabelNN + metal_edge_extender" in doc["molecule_graph"]:
            if isinstance(
                doc["molecule_graph"]["OpenBabelNN + metal_edge_extender"],
                MoleculeGraph,
            ):
                mol_graph = doc["molecule_graph"]["OpenBabelNN + metal_edge_extender"]
            else:
                mol_graph = MoleculeGraph.from_dict(
                    doc["molecule_graph"]["OpenBabelNN + metal_edge_extender"]
                )
        elif solvent_key is not None:
            if "OpenBabelNN + metal_edge_extender" in doc["molecule_graph"][solvent_key]:
                if isinstance(
                    doc["molecule_graph"][solvent_key]["OpenBabelNN + metal_edge_extender"],
                    MoleculeGraph,
                ):
                    mol_graph = doc["molecule_graph"][solvent_key]["OpenBabelNN + metal_edge_extender"]
                else:
                    mol_graph = MoleculeGraph.from_dict(
                        doc["molecule_graph"][solvent_key]["OpenBabelNN + metal_edge_extender"]
                    )

        if solvent_key is not None:
            partial_charges_resp = doc["partial_charges"][solvent_key]["resp"]
            if "mulliken" in doc["partial_charges"][solvent_key]:
                partial_charges_mulliken = doc["partial_charges"][solvent_key]["mulliken"]
            else:
                partial_charges_mulliken = None
            if "nbo" in doc["partial_charges"][solvent_key]:
                partial_charges_nbo = doc["partial_charges"][solvent_key]["nbo"]
            else:
                partial_charges_nbo = None
        else:
            partial_charges_resp = doc["partial_charges"]["resp"]
            if "mulliken" in doc["partial_charges"]:
                partial_charges_mulliken = doc["partial_charges"]["mulliken"]
            else:
                partial_charges_mulliken = None
            if "nbo" in doc["partial_charges"]:
                partial_charges_nbo = doc["partial_charges"]["nbo"]
            else:
                partial_charges_nbo = None
        if doc.get("electron_affinity", None) is None:
            electron_affinity = 0.0
        else:
            electron_affinity = doc["electron_affinity"]
        if doc.get("ionization_energy", None) is None:
            ionization_energy = 0.0
        else:
            ionization_energy = doc["ionization_energy"]
        spin_multiplicity = doc["spin_multiplicity"]
        if int(spin_multiplicity) != 1 and "nbo" in doc["partial_spins"]:
            partial_spins_nbo = None
        else:
            partial_spins_nbo = None

        return cls(
            molecule=molecule,
            energy=energy,
            enthalpy=enthalpy,
            entropy=entropy,
            entry_id=entry_id,
            mol_graph=mol_graph,
            partial_charges_resp=partial_charges_resp,
            partial_charges_mulliken=partial_charges_mulliken,
            partial_charges_nbo=partial_charges_nbo,
            electron_affinity=electron_affinity,
            ionization_energy=ionization_energy,
            spin_multiplicity=spin_multiplicity,
            partial_spins_nbo=partial_spins_nbo,
        )

    def get_free_energy(self, temperature: float = ROOM_TEMP) -> Optional[float]:
        """
        Get the free energy at the give temperature.
        """
        if self.enthalpy is not None and self.entropy is not None:
            # TODO: fix these hard coded vals
            return self.energy + self.enthalpy - temperature * self.entropy
        else:
            return None

    def __repr__(self):

        output = [
            f"MoleculeEntry {self.entry_id} - {self.formula}",
            f"Total charge = {self.charge}",
        ]

        energies = [
            ("Energy", "eV", self.energy),
            ("Enthalpy", "eV", self.enthalpy),
            ("Entropy", "eV/K", self.entropy),
            ("Free Energy (298.15 K)", "eV", self.get_free_energy()),
        ]
        for name, unit, value in energies:
            if value is None:
                output.append(f"{name} = {value} {unit}")
            else:
                output.append(f"{name} = {value:.4f} {unit}")

        if self.ind:
            output.append("index: {}".format(self.ind))

        return "\n".join(output)

    def __str__(self):
        return self.__repr__()

    def __eq__(self, other):
        if type(self) == type(other):
            return str(self) == str(other)
        else:
            return False
