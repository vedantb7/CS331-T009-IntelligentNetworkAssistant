# Intelligent Network Assistant (INA) — Setup Guide

This guide explains how to run the Intelligent Network Assistant locally from an existing project directory.

The project accepts natural-language network requests, uses an LLM to understand them, applies policy checks, executes network operations through MCP tools, validates the result, and returns a human-readable summary.

Example:

```text
network-assistant>: client1 is causing trouble, kick them off the network
```

The intended flow is:

```text
Natural-language request
        ↓
OpenRouter / LLM
        ↓
Intent parsing
        ↓
Policy engine
        ↓
MCP network tools
        ↓
Docker network
        ↓
Validation
        ↓
Human-readable response + audit information
```

## 1. Prerequisites

You need:

- Python 3
- Git
- Docker Desktop
- VS Code (recommended)
- An OpenRouter API key
- The complete INA project directory

Check Python:

```bash
python3 --version
```

Check Docker:

```bash
docker --version
docker compose version
```

Make sure Docker Desktop is running.

## 2. Get an OpenRouter API key

Create an account on OpenRouter and create an API key from its dashboard:

https://openrouter.ai/

OpenRouter supports using the key through the `OPENROUTER_API_KEY` environment variable.

Example:

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

**Never commit your real API key to Git.**

Do not hard-code it in Python source files.

## 3. Open the project

Open the existing project directory in VS Code.

```bash
cd <PROJECT_DIRECTORY>
code .
```

From the project root, check:

```bash
ls
```

You should see the project's modules, including directories such as:

```text
assistant/
mcp_server/
network/
```

## 4. Create a Python virtual environment

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

If your system uses `python` for Python 3:

```bash
python -m venv .venv
source .venv/bin/activate
```

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

## 5. Install dependencies

If the repository contains `requirements.txt`:

```bash
pip install -r requirements.txt
```

Use the dependency file included in the repository if it has a different name.

## 6. Configure the OpenRouter API key

Set the key in the terminal where you will run INA.

### macOS / Linux

```bash
export OPENROUTER_API_KEY="YOUR_OPENROUTER_API_KEY"
```

Verify without printing the secret:

```bash
python -c "import os; print('OPENROUTER_API_KEY is set' if os.getenv('OPENROUTER_API_KEY') else 'OPENROUTER_API_KEY is NOT set')"
```

### Windows PowerShell

```powershell
$env:OPENROUTER_API_KEY="YOUR_OPENROUTER_API_KEY"
```

Verify:

```powershell
python -c "import os; print('OPENROUTER_API_KEY is set' if os.getenv('OPENROUTER_API_KEY') else 'OPENROUTER_API_KEY is NOT set')"
```

## 7. Start the Docker network

From the project root:

```bash
docker compose -f network/docker-compose.yml up -d --build
```

Check the containers:

```bash
docker compose -f network/docker-compose.yml ps
```

You should see containers similar to:

```text
client1
client2
network-controller
server
```

You can also run:

```bash
docker ps
```

All required containers should have an `Up` status.

## 8. Verify the Docker network

List Docker networks:

```bash
docker network ls
```

The project network will normally have a name similar to:

```text
network_project-net
```

Inspect it:

```bash
docker network inspect network_project-net
```

Check a container's IP:

```bash
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' client1
```

Check the server:

```bash
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' server
```

## 9. Test Docker connectivity

Before running the AI agent, verify that the Docker network itself works.

If the server IP is `172.20.0.3`, for example:

```bash
docker exec client1 ping -c 4 172.20.0.3
```

You want:

```text
4 packets transmitted, 4 received, 0% packet loss
```

If you get 100% packet loss, inspect:

```bash
docker network inspect network_project-net
```

and:

```bash
docker exec network-controller iptables -L DOCKER-USER -n -v
```

Remove any stale blocking rule for `client1` before continuing.

## 10. Verify network-control tools

Check that `iptables` exists in the network controller:

```bash
docker exec network-controller which iptables
docker exec network-controller iptables --version
```

Check that `tc` exists in a client container:

```bash
docker exec client1 which tc
```

Check its current traffic-control configuration:

```bash
docker exec client1 tc qdisc show dev eth0
```

## 11. Important: where network commands execute

Different tools run in different containers.

### Blocking / unblocking

The `iptables` rules are applied through `network-controller`:

```text
Mac
 ↓
docker exec network-controller
 ↓
iptables
```

Conceptually:

```bash
docker exec network-controller iptables ...
```

### Bandwidth limiting

The current implementation applies `tc` to the target client's `eth0`:

```text
Mac
 ↓
docker exec client1
 ↓
tc qdisc ...
 ↓
client1 eth0
```

For example:

```bash
docker exec client1 tc qdisc show dev eth0
```

Do not automatically move every networking command into `network-controller`.

## 12. Run the Intelligent Network Assistant

Make sure:

1. Docker Desktop is running.
2. `.venv` is activated.
3. `OPENROUTER_API_KEY` is set.
4. Docker containers are running.

Then:

```bash
python -m assistant.client
```

You should get an interactive prompt similar to:

```text
Interactive mode. Type a command, or 'exit' / 'quit' to leave.

network-assistant>:
```

## 13. Try natural-language commands

### Block a client

```text
client1 is causing trouble, kick them off the network
```

or:

```text
block client1
```

### Unblock a client

```text
let client1 back on the network
```

or:

```text
unblock client1
```

### Limit bandwidth

```text
client1 is using too much bandwidth, limit them to 5 Mbps
```

The LLM should interpret this as approximately:

```text
action=limit_bandwidth
target=client1
rate=5mbit
```

## 14. How to interpret the output

A successful request should progress through stages similar to:

```text
Intent Parsing        OK
Policy Check          ALLOW
Execution             OK
Validation            CONFIRMED
```

The final response should explain what happened in normal language.

For example:

```text
You asked me to limit client1's bandwidth to 5 Mbps.
The policy allowed the action, the network change was applied,
and the result was successfully verified.
```

Exact wording depends on the project's response-generation logic.

## 15. Troubleshooting: "Command not found"

If you see:

```text
Execution (MCP) FAILED — Command not found
```

check where the command is being executed.

For `iptables`:

```bash
docker exec network-controller which iptables
```

The MCP implementation should invoke it through Docker:

```text
docker exec network-controller iptables ...
```

rather than trying to run:

```text
iptables ...
```

directly on macOS.

For `tc`:

```bash
docker exec client1 which tc
```

and make sure the MCP implementation invokes it through Docker.

## 16. Troubleshooting: `iperf3 failed`

You may see:

```text
Execution: OK
Validation: UNCONFIRMED — iperf3 failed
```

This means the network operation may have executed, but validation could not verify it.

Check:

```bash
docker exec client1 which iperf3
docker exec server which iperf3
```

Also:

```bash
docker exec server ps aux
```

An HTTP server is not automatically an `iperf3` server. The validation setup must have an `iperf3` listener and the appropriate client/server configuration.

Do not treat:

```text
Execution: OK
```

as equivalent to:

```text
Validation: CONFIRMED
```

Both stages matter.

## 17. Troubleshooting: LLM cannot parse requests

Check the API key:

```bash
python -c "import os; print(bool(os.getenv('OPENROUTER_API_KEY')))"
```

Expected:

```text
True
```

If it returns `False`, set the key again in the current terminal.

If OpenRouter returns a model/endpoints error, check the model configured by the project and choose a currently available model on OpenRouter.

## 18. API-key security

Never commit your real API key.

If using a `.env` file, add:

```text
.env
```

to `.gitignore`.

Safe example:

```text
OPENROUTER_API_KEY=your_key_here
```

Do not commit the real value.

Before committing:

```bash
git status
```

Make sure no secret-containing file is tracked.

## 19. Stop the Docker environment

When finished:

```bash
docker compose -f network/docker-compose.yml down
```

Start it again later with:

```bash
docker compose -f network/docker-compose.yml up -d
```

Use `--build` when images need rebuilding:

```bash
docker compose -f network/docker-compose.yml up -d --build
```

## 20. Quick-start

For a fresh setup:

```bash
cd <PROJECT_DIRECTORY>

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

export OPENROUTER_API_KEY="YOUR_OPENROUTER_API_KEY"

docker compose -f network/docker-compose.yml up -d --build

docker compose -f network/docker-compose.yml ps

python -m assistant.client
```

Then try:

```text
client1 is causing trouble, kick them off the network
```

or:

```text
client1 is using too much bandwidth, limit them to 5 Mbps
```

## 21. Recommended troubleshooting order

If something does not work, debug in this order:

```text
1. Python environment
        ↓
2. OpenRouter API key
        ↓
3. Docker Desktop
        ↓
4. Docker containers
        ↓
5. Docker network connectivity
        ↓
6. iptables / tc
        ↓
7. MCP execution
        ↓
8. Policy engine
        ↓
9. Validation / iperf3
        ↓
10. AI response generation
```

Do not debug the LLM first if the Docker network itself is not working.

## 22. What a complete successful request looks like

```text
User
 │
 │ "client1 is using too much bandwidth, limit them to 5 Mbps"
 ▼
OpenRouter / LLM
 │
 │ action=limit_bandwidth
 │ target=client1
 │ rate=5mbit
 ▼
Policy Engine
 │
 │ ALLOW
 ▼
MCP Tool
 │
 │ docker exec client1 tc ...
 ▼
Docker Network
 │
 │ bandwidth limit applied
 ▼
Validation
 │
 │ CONFIRMED
 ▼
AI Response
 │
 ▼
"client1 has been limited to 5 Mbps and the change was verified."
```

If execution succeeds but validation is `UNCONFIRMED`, the network action may have been applied but the validation environment still needs to be fixed.
