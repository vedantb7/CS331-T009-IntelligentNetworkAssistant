import subprocess #Allows running system commands

#Client IPs in the docker network
CLIENT_IPS = {
    "client1": "172.20.0.2",
    "client2": "172.20.0.4"
}

#Function to block a client
def block_client(client: str) -> dict:
    try:
        if client not in CLIENT_IPS:
            return {
                "status" : "failure",
                "action" : "block_client",
                "client" : client,
                "message" : f"Client {client} not found in the network."
            }

        client_ip = CLIENT_IPS[client] #Retrieve the client IP from Mapping

        command = [
            "iptables", "-I", "DOCKER-USER",
            "-s", client_ip, "-j", "DROP"
        ]
        result = subprocess.run(
            command,   #Runs system command, -A means Append
            capture_output = True,   #Capture the output
            check = False, #Avoid raising unecessary exception
            text = True,   #Return output as normal text
        )

        if(result.returncode == 0):
            return {
            "status" : "success",
            "action" : "block_client",
            "client" : client,
            "message": f"Client {client} blocked successfully."
        }

        #Handling Failure of Blocking
        return {
            "status" : "failure",
            "action" : "block_client",
            "client" : client,
            "message" : result.stderr.strip()
        }
    #Handle the case where the command is not found
    except FileNotFoundError:
        return{
            "status" : "failure",
            "action" : "block_client",
            "client" : client,
            "message" : "Command not found. Please ensure the command is available on the system."
        }

    #Catch any other error
    except Exception as error:
        return {
            "status" : "failure",
            "action" : "block_client",
            "client" : client,
            "message" : str(error)
        }

#Function to unblock a client
def unblock_client(client: str) -> dict:
    if client not in CLIENT_IPS:
        return{
            "status": "failure",
            "action": "unblock_client",
            "client": client,
            "message": f"Client {client} not found in the network."
        }

    client_ip = CLIENT_IPS[client]

    try:
        command = [
            "iptables", "-D", "DOCKER-USER",
            "-s", client_ip, "-j", "DROP"
        ]
        
        result = subprocess.run(
            command, #-D Means Delete
            capture_output = True,
            text = True,
            check = False
        )

        if(result.returncode == 0):
            return {
                "status": "success",
                "action": "unblock_client",
                "client": client,
                "message": f"Client {client} unblocked successfully."
            }

        #Failure Handling
        return{
            "status": "failure",
            "action": "unblock_client",
            "client": client,
            "message": result.stderr.strip()
        }
    
    except FileNotFoundError:
        return {
            "status": "failure",
            "action": "unblock_client",
            "client": client,
            "message": "iptables command not found."
        }
    
    except Exception as error:
        return {
            "status": "failure",
            "action": "unblock_client",
            "client": client,
            "message": str(error)
        }    
        
def limit_bandwidth(client: str, rate: str) -> dict:
    if client not in CLIENT_IPS:
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": rate,
            "message": f"Unknown client: {client}."
        }
    
    client_ip = CLIENT_IPS[client]
    
    try:
        command = [
            "docker", "exec", client,
            "tc", "qdisc", "replace", "dev", "eth0",
            "root", "tbf",
            "rate", rate,
            "burst", "32kbit",
            "latency", "400ms"
        ]

        result = subprocess.run(
        #tc controls traffic, qdisc controls packet queuing
        # apply it to dev eth0 interface
        # tbf used for rate limiting
        # latency tell max time packets can with in the queue
            command,
            capture_output=True,
            text=True,
            check=False
        )     
        
        
        if(result.returncode == 0):
            return {
                "status": "success",
                "action": "limit_bandwidth",
                "client": client,
                "rate": rate,
                "message": f"Bandwidth limited to {rate} successfully."
            }

        #Failure
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": rate,
            "message": result.stderr.strip()
        }
    
    except FileNotFoundError:
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": rate,
            "message": "tc command not found."
        }
    
    except Exception as error:
        return {
            "status": "failure",
            "action": "limit_bandwidth",
            "client": client,
            "rate": rate,
            "message": str(error)
        }
        