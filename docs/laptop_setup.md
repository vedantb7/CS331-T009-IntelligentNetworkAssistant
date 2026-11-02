# Laptop Setup (Operator Workstation)

This guide prepares a Linux laptop (Fedora 38+, Ubuntu 22.04+ or Debian 12+) to run the INA v2 assistant:

- **Without the Pi:** against the built-in mock network, for development, tests and the evaluation.
- **With the Pi:** against the real router, over MCP Streamable HTTPS.

The router side is in [rpi_setup.md](rpi_setup.md). Do that first if you want to talk to the real Pi.

---

## 1. System packages

```bash
# Fedora
sudo dnf install -y git python3 python3-pip openssl iperf3 nftables iproute

# Ubuntu / Debian
sudo apt update && sudo apt install -y git python3 python3-venv python3-pip openssl iperf3 nftables iproute2
```

The assistant needs **Python 3.11 or newer**:

```bash
python3 --version
```

---

## 2. Get the code and create a virtual environment

Keep the folder name `inav2`: the tests and the MCP server import the package as `inav2.*`.

```bash
mkdir -p ~/Documents/ina && cd ~/Documents/ina
git clone git@github.com:vedantb7/inav2.git        # or the HTTPS URL of the repository
cd inav2

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt                    # MCP Python SDK (+ pydantic)
pip install anthropic                              # only if you will use Claude as the LLM
pip install pyyaml                                 # optional: used by network/access_point.py
```

Each time you open a new terminal:

```bash
cd ~/Documents/ina/inav2 && source .venv/bin/activate
```

---

## 3. Check that everything works (no Pi needed)

```bash
python3 tests/run_all_tests.py                     # expect: Ran 332 tests ... OK
python3 -m eval.run_eval                           # expect: 16/16 functional, 0/65 unsafe executed
python3 -m assistant "limit guest-phone to 5 mbps" # mock network: Done. Limited guest-phone to 5 Mbit/s ...
python3 -m assistant                               # interactive; type 'exit' to quit
```

**Optional: real-kernel blocking lab.** This needs unprivileged user namespaces, which are on by default on Fedora and Ubuntu.

```bash
cd .. && INA_RUN_LAB=1 python3 -m unittest inav2.tests.unit.test_router_config ; cd inav2
```

---

## 4. Configure the environment file

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` (`nano .env`). The assistant loads it on start, and exported shell variables override it.

### 4.1 LLM (optional; any provider)

Without an LLM, the rule-based parser and reply templates are used. Set one of:

```bash
# Claude (official SDK: pip install anthropic)
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...

# OpenRouter
LLM_PROVIDER=openrouter
LLM_API_KEY=sk-or-...
LLM_MODEL=<provider/model>

# Gemini (OpenAI-compatible endpoint)
LLM_PROVIDER=gemini
LLM_API_KEY=<Google AI Studio key>
LLM_MODEL=<gemini model>

# OpenAI / Groq
LLM_PROVIDER=openai          # or groq
LLM_API_KEY=...
LLM_MODEL=...

# Local model with Ollama (no key)
LLM_PROVIDER=ollama
LLM_MODEL=<model you pulled>
```

Test it against the mock network:

```bash
python3 -m assistant "slow the guest phone down to 5 megabits"
```

A missing key or model prints one warning, and the rule-based parser is used instead.

### 4.2 Connection to the Pi (filled in after section 5)

```bash
INA_MCP_URL=https://192.168.50.1:8443/mcp
INA_MCP_CA=/home/<you>/.config/ina/ina-router.crt     # absolute path: '~' is not expanded in .env
INA_ROLE_TOKEN=<your personal token from the admin>
```

---

## 5. Connect the laptop to the INA router

### 5.1 Join the Wi-Fi with a stable MAC address

Your role is bound to this laptop's registered MAC and fixed IP. Make NetworkManager keep the same MAC for `INA-Router`:

```bash
nmcli device wifi list | grep INA-Router
nmcli device wifi connect "INA-Router" password "<AP passphrase>"
nmcli connection modify "INA-Router" 802-11-wireless.cloned-mac-address stable
nmcli connection down "INA-Router" && nmcli connection up "INA-Router"
```

`stable` keeps one MAC per network. Use `permanent` to send the hardware MAC instead.

### 5.2 Give your MAC to the admin

```bash
WIFI_IF=$(nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="wifi"{print $1; exit}')
nmcli -g GENERAL.HWADDR device show "$WIFI_IF"     # the MAC the Pi sees
ip -4 addr show "$WIFI_IF"                         # a pool address (192.168.50.10–100) for now
```

The admin registers it on the Pi with a fixed IP and grants your role ([rpi_setup.md](rpi_setup.md) §8). Then reconnect so the laptop takes its reservation:

```bash
nmcli connection down "INA-Router" && nmcli connection up "INA-Router"
ip -4 addr show "$WIFI_IF"                         # now the fixed IP, e.g. 192.168.50.2
ping -c 3 192.168.50.1 && ping -c 3 8.8.8.8
```

### 5.3 Trust the Pi's TLS certificate

```bash
mkdir -p ~/.config/ina
scp team009@ina-router.local:/etc/ina/tls/server.crt ~/.config/ina/ina-router.crt
# (or: scp team009@192.168.50.1:/etc/ina/tls/server.crt ~/.config/ina/ina-router.crt)
openssl x509 -in ~/.config/ina/ina-router.crt -noout -subject -ext subjectAltName
```

### 5.4 Store your token and test

Put the token the admin gave you into `.env` as `INA_ROLE_TOKEN`, then:

```bash
curl -s -o /dev/null -w "%{http_code}\n" --cacert ~/.config/ina/ina-router.crt \
     -X POST -H "Content-Type: application/json" -d '{}' https://192.168.50.1:8443/mcp   # 401 = server reachable
python3 -m assistant --server mcp "list devices"
python3 -m assistant --server mcp          # interactive session against the real router
```

Expected failures, with the cause and the fix in [troubleshooting.md](troubleshooting.md):

| Message                          | Cause                                   | Case |
| -------------------------------- | --------------------------------------- | ---- |
| "TLS certificate is not trusted" | `INA_MCP_CA` is wrong or not absolute | 12   |
| "the server refused the request" | Wrong or missing token                  | 13   |
| `R-token-device`               | The laptop is not on its fixed IP       | 14   |
| "cannot reach"                   | Service down, or the laptop is blocked  | 15   |

---

## 6. Optional: full local stack without the Pi (MCP over HTTPS)

To develop the server and the client on one laptop, run the server with demo tokens, with `ina-netctl` running without sudo:

```bash
# terminal 1: a self-signed certificate for 127.0.0.1, then the MCP server
mkdir -p /tmp/ina-tls
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 30 -subj "/CN=localhost" \
    -addext "subjectAltName=IP:127.0.0.1,DNS:localhost" \
    -keyout /tmp/ina-tls/server.key -out /tmp/ina-tls/server.crt
cd ~/Documents/ina
INA_DEMO_TOKENS=1 INA_NETCTL_COMMAND="python3 -m inav2.network.netctl" \
INA_POLICY_AUDIT_LOG=/tmp/ina-policy.jsonl INA_COMMAND_LOG=/tmp/ina-commands.jsonl \
    inav2/.venv/bin/python -m inav2.mcp_server.server --host 127.0.0.1 --port 8443 \
    --tls-cert /tmp/ina-tls/server.crt --tls-key /tmp/ina-tls/server.key

# terminal 2: the assistant
cd ~/Documents/ina/inav2 && source .venv/bin/activate
INA_ROLE_TOKEN=operator-token python3 -m assistant --server mcp \
    --mcp-url https://127.0.0.1:8443/mcp --mcp-ca /tmp/ina-tls/server.crt "block admin-laptop"
# -> I can't do that: 'admin-laptop' is a protected device and cannot be changed.
```

Commands that need root (`nft`, `tc`) report "Failed" here: there is no router on a laptop. Policy and schema denials, and the logs, work as on the Pi. Never use `INA_DEMO_TOKENS=1` on the Pi.

---

## 7. Quick reference

```bash
source .venv/bin/activate
python3 -m assistant "<request>"                        # mock network
python3 -m assistant --server mcp "<request>"           # the Pi (uses .env)
python3 -m assistant --server mcp --no-llm "<request>"  # the Pi, rule-based parser only
python3 tests/run_all_tests.py                          # tests
python3 -m eval.run_eval                                # evaluation
```
