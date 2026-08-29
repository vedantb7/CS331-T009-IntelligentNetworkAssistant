from fastmcp import FastMCP
from tools import block_client, unblock_client, limit_bandwidth

mcp = FastMCP("Network Assistant") #Name of server


@mcp.tool() #Decorator that turns the below function to a tool
def block(client: str) -> dict:
    #Block client
    return block_client(client)

@mcp.tool()
def unblock(client: str) -> dict:
    #Unblock client
    return unblock_client(client)

@mcp.tool()
def limit(client: str, rate: str) -> dict:
    return limit_bandwidth(client, rate)

if(__name__ == "__main__"): #Check whether the file is being run directly, if yes, run below code
    mcp.run()