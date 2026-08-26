import socket
import sys

HOST = "0.0.0.0"
PORT = 5000

def run_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen(1)

    print(f"server listening on {HOST}:{PORT}")

    conn, addr = server.accept()

    print(f"server recieved form addr: {addr}")

    message = conn.recv(1024).decode()
    print(f"recieved message: {message}")

    conn.sendall(b"Hello from server")

    conn.close()
    server.close()

def run_client():
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    server_ip = "172.20.0.2"

    print(f"connecting to {server_ip}:{PORT}")

    client.connect((server_ip, PORT))

    client.sendall(b"hello form client2")
    response = client.recv(1024).decode()

    print(f"recieved : {response}")
    client.close()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: pyhthon3 client.py [server|client]")
        sys.exit(1)

    if sys.argv[1] == "server":
        run_server()
    elif sys.argv[1] == "client":
        run_client()
    else:
        print("invalid mode")
    
