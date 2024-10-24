print("Importing required files...")
from pymatgen.core.structure import Molecule
from pymatgen.analysis.graphs import MoleculeGraph
from pymatgen.analysis.local_env import OpenBabelNN
from monty.serialization import loadfn, dumpfn
from mol_entry import MoleculeEntry
from initial_state import find_mol_entry_by_entry_id
import copy
print("Done!")

#list of mol_ids for species you want to find with each mol_id occupying its own line. Must have an empty line
#at the end of the file, but empty lines will not be copied.
print("Opening and reading species id file...")
with open('atom_mapping_set.txt') as mol_id_doc: #open the text file and copy each line as the element of a list, removing '\n'
    mol_id_list = []
    for line in mol_id_doc:
        if line.strip(): #excludes empty lines
            mol_id_list.append(line[0:len(line)-1])
print("Done!")

print("Opening json file...")
mol_json = "032923.json"
database_entries = loadfn(mol_json) #loads the json file to a list of dictionaries
print("Done!")

print("Reading molecule entries from json file...") #converts dictionary from loaded json file to a list
mol_entries= [MoleculeEntry.from_mp_doc(e) for e in database_entries]
print("Done!")

final_mol_ids = []

print('Copying desired molecules to a new list...')
for mol_id in mol_id_list: #takes the list of ids, finds their corresponding molecules in the json file 
    for mol_entry in mol_entries:
        m_id = mol_entry.entry_id
        if m_id == mol_id:
            graph = mol_entry.mol_graph
            for entry in mol_entries: #(as well as differently charged variants) and adds them to the list
                molecule_graph = entry.mol_graph #tests if other charges found
                if molecule_graph.isomorphic_to(graph):
                    final_mol_ids.append(entry.entry_id)

to_remove = ['nbo', 'alpha', 'beta']

test_set = []
for entry in database_entries:
    if entry['molecule_id'] in final_mol_ids:
        new_entry = copy.deepcopy(entry)
        for name in entry.keys():
            for remove in to_remove:
                if remove in name:
                    val = new_entry.pop(name)
        test_set.append(new_entry)
            
dumpfn(test_set, 'atom_mapping_set.json') 

new_mol_json = loadfn('atom_mapping_set.json')
new_mol_entries= [MoleculeEntry.from_mp_doc(e) for e in new_mol_json]

new_id_list = []

for molecule in new_mol_entries:
    new_id_list.append(molecule.entry_id)

new_id_set = set(new_id_list)
mol_id_set = set(mol_id_list)
differences = mol_id_set - new_id_set
if differences == set():
    print('All desired entries successfully copied')
else:
    for elem in differences:
        print('entry ', elem, ' not copied successfully')