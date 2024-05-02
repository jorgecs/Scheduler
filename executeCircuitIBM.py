#!/usr/bin/env python
# coding: utf-8

# import libraries
import numpy as np
import math
import matplotlib.pyplot as plt
from math import pi
from fractions import Fraction
from math import gcd
import time
import os
import json

# Importar las bibliotecas de Qiskit
from qiskit import QuantumCircuit, Aer, execute
from qiskit.visualization import plot_histogram
from qiskit.circuit.library import QFT, CU1Gate
from qiskit import transpile
from qiskit_ibm_provider import least_busy, IBMProvider
from time import sleep

def least_busy_backend_ibm(qb):
    provider = IBMProvider('ibm-q', 'open', 'main')
    backend = provider.backends(filters=lambda x: x.configuration().n_qubits >= qb and
                               not x.configuration().simulator and x.status().operational==True)
    backend = least_busy(backend)
    return backend

# Ejecutar el circuito
def runIBM(machine, circuit, shots):
    
    if machine == "local":
        backend = Aer.get_backend('qasm_simulator')
        x = int(shots)
        job = execute(circuit, backend, shots=x)
        result = job.result()
        # After the execution, delete in the file the line with that id, its no longer needed (we just need it because there is a possibility of the machine to stop working in the middle)
        counts = result.get_counts()
        #print(counts)
        #x, y = factor(counts)
        #return [x, y]
        return counts
    else:

        # Load your IBM Quantum account
        provider = IBMProvider()
        backend = provider.get_backend(machine)

        qc_basis = transpile(circuit, backend)
        x = int(shots)
        job = execute(qc_basis, backend, shots=x)
        result = job.result()
        counts = result.get_counts()

        print(counts)
        return counts
    

def runIBM_save(machine,circuit,shots,users,qubit_number):
        
    if machine == "local":
        backend = Aer.get_backend('qasm_simulator')
        x = int(shots)
        job = execute(circuit, backend, shots=x)
        result = job.result()
        # After the execution, delete in the file the line with that id, its no longer needed (we just need it because there is a possibility of the machine to stop working in the middle)
        counts = result.get_counts()
        #print(counts)
        #x, y = factor(counts)
        #return [x, y]
        return counts
    else:

        # Load your IBM Quantum account
        provider = IBMProvider()
        backend = provider.get_backend(machine)

        qc_basis = transpile(circuit, backend)
        x = int(shots)
        job = execute(qc_basis, backend, shots=x) #TODO almacenar el identificador de la ejecucion en in fichero por si la máquina se revienta. Cuando se inicie la API, comprueba ese fichero y recupera los circuitos para hacerles el unscheduler (deberia almacenar el identificador del circuito con los datos de los usuarios(id), qubits de cada usuario)
        # -----------------------------------------------------#

        id = job.job_id() # Get the job id
        script_dir = os.path.dirname(os.path.realpath(__file__))
        ids_file = os.path.join(script_dir, 'ids.txt')  # create the path to the results file in the script's directory
        with open(ids_file, 'a') as file:
            file.write(json.dumps({id:(users,qubit_number)}))
            file.write('\n')
        # Write the id in a file, along with the users, and their qubit numbers

        # -----------------------------------------------------#
        result = job.result()
        counts = result.get_counts()

        # -----------------------------------------------------#

        #Seach for the id in the file and delete the line
        with open(ids_file, 'r') as file:
            lines = file.readlines()
        with open(ids_file, 'w') as file:
            for line in lines:
                line_dict = json.loads(line.strip())
                if list(line_dict.keys())[0] != id:
                    file.write(line)
                
        # -----------------------------------------------------#

        print(counts)
        return counts
