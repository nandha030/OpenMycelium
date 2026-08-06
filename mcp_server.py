#!/usr/bin/env python3
"""Minimal stdio MCP server exposing local OpenMycelium inventory tools."""
import json, sys
from server import discover, STATE
TOOLS=[{"name":"openmycelium_scan_hardware","description":"Scan accelerators visible to this host.","inputSchema":{"type":"object","properties":{}}},{"name":"openmycelium_list_accelerators","description":"List accelerators found by the last scan.","inputSchema":{"type":"object","properties":{}}},{"name":"openmycelium_k8s_install_plan","description":"Return safe Kubernetes agent installation steps.","inputSchema":{"type":"object","properties":{}}}]
for line in sys.stdin:
    try:
        msg=json.loads(line); method=msg.get('method')
        if method=='initialize': result={"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"openmycelium","version":"0.1.0"}}
        elif method=='tools/list': result={"tools":TOOLS}
        elif method=='tools/call':
            name=msg.get('params',{}).get('name'); value=discover() if name=='openmycelium_scan_hardware' else {"accelerators":STATE['accelerators']} if name=='openmycelium_list_accelerators' else {"command":"kubectl apply -f k8s/openmycelium.yaml"}; result={"content":[{"type":"text","text":json.dumps(value,indent=2)}]}
        else: raise ValueError('Method not found')
        print(json.dumps({"jsonrpc":"2.0","id":msg.get('id'),"result":result}),flush=True)
    except Exception as error: print(json.dumps({"jsonrpc":"2.0","id":None,"error":{"code":-32601,"message":str(error)}}),flush=True)
