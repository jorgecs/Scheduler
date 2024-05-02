from urllib.parse import urlparse
import json
import ast
from urllib.parse import unquote
from flask import Flask, request
import socket
import requests
from divideResults import divideResults
import logging
import uuid
import re
from scheduler_policies import SchedulerPolicies
import os

class Scheduler:
    def __init__(self):
        self.app = Flask(__name__)
        self.ports = {}

        self.app.config['HOST'] = 'localhost'
        self.app.config['PORT'] = 8082
        self.app.config['TRANSLATOR'] = 'localhost'
        self.app.config['TRANSLATOR_PORT'] = 8081
        
        self.max_qubits = 29
        self.result = []
        
        self.translator = f"http://{self.app.config['TRANSLATOR']}:{self.app.config['TRANSLATOR_PORT']}/code/"
        self.policy_service = f"http://{self.app.config['HOST']}:{self.app.config['PORT']}/service/"
        

        self.scheduler_policies = SchedulerPolicies(self.app)

        self.app.route('/url', methods=['POST'])(self.store_url)
        self.app.route('/circuit', methods=['POST'])(self.store_url_circuit)
        self.app.route('/unscheduler', methods=['POST'])(self.unscheduler)
        self.app.route('/result', methods=['GET'])(self.sendResults)


        # Check if the file with the jobs is not empty, go to each job id and search the data (execute the unscheduler to each one to retrieve the divided results), then delete the job id from the file (do this on a different thread to do job.result() in the case the job has not finished yet)
        

        @self.app.errorhandler(404)
        def not_found_error(error):
            return 'This route does not exist', 404

        @self.app.errorhandler(500)
        def internal_error(error):
            return 'Internal server error', 500
        

    def run(self):
        self.updatePorts()
        print('hecho')
        self.app.run(host='0.0.0.0', port=self.app.config['PORT'], debug=True)

    def select_policy(self, url, num_qubits, shots, user, circuit_name, maxDepth, provider, policy):
        
        # TODO select policy depending on the data

        data = {"circuit": url, "num_qubits": num_qubits, "shots": shots, "user": user, "circuit_name": circuit_name, "maxDepth": maxDepth, "provider": provider}
        requests.post(self.policy_service+policy, json=data)
        

    def unscheduler(self):
        counts = request.json['counts']
        shots = request.json['shots']
        provider = request.json['provider']
        qb = request.json['qb']
        users = request.json['users']
        circuit_names = request.json['circuit_names']

        results = divideResults(counts,shots,provider,qb,users,circuit_names)

        #Save the content of results in a file
        for dividedResult in results:
            str_key_dict = {str(key): value for key, value in dividedResult.items()}
            script_dir = os.path.dirname(os.path.realpath(__file__))  # get the directory of the current script
            results_file = os.path.join(script_dir, 'results.txt')  # create the path to the results file in the script's directory
            with open(results_file, 'a') as file:
                file.write(json.dumps(str_key_dict))
                file.write('\n')
        
        # If the circuit is already present in the results, add the values to the previous circuit result, otherwise add the circuit to the results
        key_to_index = {tuple(res.keys()): i for i, res in enumerate(self.result)}

        for res in results:
            keys = tuple(res.keys())  # Convert keys to a tuple so it can be used as a dictionary key
            if keys in key_to_index:
                idx = key_to_index[keys]
                for key in keys:
                    # If the value is a dictionary, merge the dictionaries and sum the values of common keys
                    if isinstance(self.result[idx][key], dict) and isinstance(res[key], dict):
                        self.result[idx][key] = {k: self.result[idx][key].get(k, 0) + res[key].get(k, 0) for k in set(self.result[idx][key]) | set(res[key])}
                    # If the value is numeric, add the new value to the previous value
                    else:
                        self.result[idx][key] += res[key]
            else:
                self.result.append(res)
                key_to_index[keys] = len(self.result) - 1

        return "Results stored successfully", 200  # Return a response

    def store_url(self): # TODO instead of "both", use a list of providers as an input
        if request.json.get('url') is None:
            return "URL must be specified", 400
        if request.json.get('provider') is None:
            return "Provider must be specified", 400
        if request.json.get('policy') is None:
            return "Policy must be specified", 400
        url =  request.json['url']
        provider = request.json['provider']
        # To handle provider if its a string
        if isinstance(provider, str):
            provider = [provider]
        policy = request.json['policy']
        shots = None
        # TODO if the policy is time, the user "should" not specify shots
        if request.json.get('ibm_shots') is not None and request.json.get('aws_shots') is not None and ('ibm' in provider and 'aws' in provider): # If the provider is both and the shots of each provider are specified, use the ibm and aws shots
            ibmShots = request.json['ibm_shots']
            awsShots = request.json['aws_shots']
            if not isinstance(ibmShots, int) or ibmShots <= 0 or ibmShots > 20000 or not isinstance(awsShots, int) or awsShots <= 0 or awsShots > 20000:
                return "Invalid shots value", 400          
        else:
            if request.json.get('shots') is None: # If the shots are not specified or the provider is not both, use the basic shot value
                return "Shots must be specified", 400
            shots = request.json['shots']
            if not isinstance(shots, int) or shots <= 0 or shots > 20000:
                return "Invalid shots value", 400
            ibmShots = shots // 2
            awsShots = shots - ibmShots

    
        user = uuid.uuid4().int
        #user = request.headers.get('X-Forwarded-For', request.remote_addr)

        print(url)
        # Parse the URL and extract the fragment
        try:
            fragment = urlparse(url).fragment
        except Exception as e:
            print(f"Error parsing URL: {e}")
            return "Invalid URL", 400
        
        parsed_url = urlparse(url)
        if parsed_url.netloc != "algassert.com" or 'quirk' not in parsed_url.path:
                return "URL must come from quirk", 400

        # The fragment is a string like "circuit={...}". We need to remove the "circuit=" part and parse the rest as JSON.
        if not fragment.startswith('circuit='):
            print(f"No 'circuit' fragment in URL: {url}")
            return "Invalid URL", 400  # Return an error response

        else:
            # Remove the "circuit=" part and parse the rest as a Python literal
            circuit_str = fragment[len('circuit='):]
            circuit = ast.literal_eval(unquote(circuit_str))

            providers = {} # Dictionary because an execution can be executed on multiple providers

            # Count the number of qubits in the circuit
            for provider_name in provider:
                if provider_name == 'ibm':
                    num_qubits = max(len(col) for col in circuit['cols'] if 1 not in col)
                    if shots is None:
                        shots = ibmShots
                    providers['ibm'] = shots
                elif provider_name == 'aws': #In AWS Measure instruction does not exist, if the Measure instruction is in the url, that number of measure qubits are removed
                    num_qubits = max(len(col) for col in circuit['cols'] if 'Measure' not in col and 1 not in col)
                    if shots is None:
                        shots = awsShots
                    providers['aws'] = shots

            maxDepth = max(sum(1 for j in circuit['cols'] if i < len(j) and j[i] not in {1, 'Measure'}) for i in range(num_qubits))

            if num_qubits > self.max_qubits:
                return "Circuit too large", 400  # Return a response

            for provider in providers: #Iterate through the providers to add the elements to the specific provider queue in case the circuit needs to be executed on multiple providers
                shots = providers[provider]
                
                self.select_policy(url, num_qubits, shots, user, url, maxDepth, provider, policy)
    
        return "Your id is "+str(user), 200  # Return a response
    
    def store_url_circuit(self):
        if request.json.get('url') is None:
            return "URL must be specified", 400
        if request.json.get('shots') is None: # TODO if the policy is time, the user "should" not specify shots
            return "Shots must be specified", 400
        if request.json.get('policy') is None:
            return "Policy must be specified", 400
        url = request.json['url']
        shots = request.json['shots']
        policy = request.json['policy']

        if not isinstance(shots, int) or shots <= 0 or shots > 20000:
            return "Invalid shots value", 400

        user = uuid.uuid4().int
        #user = request.headers.get('X-Forwarded-For', request.remote_addr)

        # URL is a raw GitHub url, get its content
        try:
            parsed_url = urlparse(url)
            if parsed_url.netloc != "raw.githubusercontent.com":
                return "URL must come from a raw GitHub file", 400
            response = requests.get(url)
            response.raise_for_status()
            # Get the name of the file
            circuit_name = url.split('/')[-1]
        except requests.exceptions.RequestException as e:
            print(f"Error getting URL content: {e}")
            return "Invalid URL", 400
        
        circuit = response.text

        # Split the circuit string into lines once
        lines = circuit.split('\n')
        importAWS = next((line for line in lines if 'braket.circuits' in line), None)
        importIBM = next((line for line in lines if 'qiskit' in line), None)

        if importIBM:
            # Parse the circuit and extract the number of qubits
            num_qubits_line = next((line for line in lines if '= QuantumRegister(' in line), None)
            num_qubits = int(num_qubits_line.split('QuantumRegister(')[1].split(',')[0].strip(')')) if num_qubits_line else None

            if num_qubits > self.max_qubits:
                return "Circuit too large", 400

            # Get the data before the = in the line that appears QuantumCircuit(...)
            file_circuit_name_line = next((line for line in lines if '= QuantumCircuit(' in line), None)
            file_circuit_name = file_circuit_name_line.split('=')[0].strip() if file_circuit_name_line else None

            # Get the name of the quantum register
            qreg_line = next((line for line in lines if '= QuantumRegister(' in line), None)
            qreg = qreg_line.split('=')[0].strip() if qreg_line else None

            # Get the name of the classical register
            creg_line = next((line for line in lines if '= ClassicalRegister(' in line), None)
            creg = creg_line.split('=')[0].strip() if creg_line else None

            # Remove all lines that don't start with file_circuit_name and don't include the line that has file_circuit_name.add_register and line not starts with // or # (comments)
            circuit = '\n'.join([line for line in lines if line.strip().startswith(file_circuit_name+'.') and 'add_register' not in line and not line.strip().startswith('//') and not line.strip().startswith('#')])
            
            circuit = '\n'.join([line.lstrip() for line in circuit.split('\n')])
            
            # Replace all appearances of file_circuit_name, qreg, and creg
            circuit = circuit.replace(file_circuit_name+'.', 'circuit.')
            circuit = circuit.replace(f'{qreg}[', 'qreg_q[')
            circuit = circuit.replace(f'{creg}[', 'creg_c[')

            # Create an array with the same length as the number of qubits initialized to 0 to count the number of gates on each qubit
            qubits = [0] * num_qubits
            for line in circuit.split('\n'): # For each line in the circuit
                if 'measure' not in line and 'barrier' not in line: #If the line is not a measure or a barrier
                    # Check the numbers after qreg_q and add 1 to qubits on that position. It should work with whings like circuit.cx(qreg_q[0], qreg_q[3]), adding 1 to both 0 and 3
                    # This adds 1 to the number of gates used on that qubit
                    for match in re.finditer(r'qreg_q\[(\d+)\]', line):
                        qubits[int(match.group(1))] += 1
            maxDepth = max(qubits) #Get the max number of gates on a qubit
            provider = 'ibm'
        
        elif importAWS:

            # Get the data before the = in the line that appears QuantumCircuit(...)
            file_circuit_name_line = next((line for line in lines if '= Circuit(' in line), None)
            file_circuit_name = file_circuit_name_line.split('=')[0].strip() if file_circuit_name_line else None

            # Remove all lines that don't start with file_circuit_name and don't include the line that has file_circuit_name.add_register and line not starts with // or # (comments)
            circuit = '\n'.join([line for line in lines if line.strip().startswith(file_circuit_name+'.') and not line.strip().startswith('//') and not line.strip().startswith('#')])

            circuit = circuit.replace(file_circuit_name+'.', 'circuit.')
            # Remove tabs and spaces at the beginning of the lines
            circuit = '\n'.join([line.lstrip() for line in circuit.split('\n')])

            # Create an array with the same length as the number of qubits initialized to 0 to count the number of gates on each qubit
            qubits = {}
            for line in circuit.split('\n'): # For each line in the circuit
                if 'barrier' not in line and 'circuit.' in line: #If the line is not a measure or a barrier
                    numbers = re.findall(r'\d+', line)
                    for elem in numbers:
                        if elem not in qubits:
                            qubits[elem] = 0
                        else:
                            qubits[elem] += 1
            maxDepth = max(qubits.values()) #Get the max number of gates on a qubit
            num_qubits = len(qubits.values())
            provider = 'aws'

        self.select_policy(circuit, num_qubits, shots, user, circuit_name, maxDepth, provider, policy)

        return "Your id is "+str(user), 200

    
    def sendResults(self):
        id = request.args.get('id')
        if id is None or id == '':
            return "No id provided", 400
        try:
            user = int(request.args.get('id'))
        except ValueError:
            return "Invalid id value. It must be an integer.", 400
        #user = request.headers.get('X-Forwarded-For', request.remote_addr)
            
        if user <= 0:
            return "Invalid id value. It must be a positive integer.", 400

        iter = self.result.copy() #Makes a copy to not delete on search
        data = [] #Data to send
        for i,elem in enumerate(iter): #Iterate through the results to search for the elements that has the user key
            keys = list(elem.keys())
            if keys[0][0] == user: # Check if the results are from the user
                data.append({keys[0][1]:elem[keys[0]]}) #If the results are from the user, add them to the data to send, also add the circuit to better visibility
                del self.result[i][keys[0]] #Delete that result sent

        self.result = [elem for elem in self.result if elem] #After removing the elements with the user key, the empty dicts are removed

        print(self.result)

        # Check if self.result is empty. If true, it checks the results.txt file to retrieve the data
        if not data:
            # Search in result.txt for the data
            try:
                script_dir = os.path.dirname(os.path.realpath(__file__))  # get the directory of the current script
                results_file = os.path.join(script_dir, 'results.txt')  # create the path to the results file in the script's directory
                with open(results_file, 'r') as file:
                    for line in file:
                        fdata = json.loads(line)
                        keys = list(fdata.keys())
                        parts = keys[0].strip('()').split(',')
                        id_str = parts[0]
                        filename = ','.join(parts[1:]).strip().strip("'")
                        if int(id_str) == user:
                            if data: #If the file is already in the data, sum the values of the keys
                                for item in data:
                                    if filename in item:
                                        item[filename] = {k: item[filename].get(k, 0) + fdata[keys[0]].get(k, 0) for k in set(item[filename]) | set(fdata[keys[0]])}
                                        break
                            else:
                                data.append({filename:fdata[keys[0]]})
                            # Delete that element from the file
            except FileNotFoundError:
                print("File not found")

        return json.dumps(data), 200

    def updatePorts(self):
        for i in range(8083, 8182):
            a_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            location = ("0.0.0.0", i)
            result_of_check = a_socket.connect_ex(location)

            if result_of_check == 0:
                self.ports[i]=1
            else:
                self.ports[i]=0

            a_socket.close()

    
    def getFreePort(self):
        puertos=[k for k, v in self.ports.items() if v == 0]
        self.ports[puertos[0]]=1
        return puertos[0]

if __name__ == '__main__':
    app = Scheduler()
    app.run()
