"""Local dashboard/API server. Uses only the Python standard library."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json, platform, shutil, subprocess
ROOT = Path(__file__).parent
STATE = {"accelerators": []}
def execute(command):
    try: return subprocess.run(command, capture_output=True, text=True, timeout=12, check=False).stdout
    except (OSError, subprocess.TimeoutExpired): return ''
def discover():
    accelerators = []
    if shutil.which('nvidia-smi'):
        for line in execute(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader,nounits']).splitlines():
            parts = [p.strip() for p in line.split(',')]
            if len(parts) == 2: accelerators.append({'vendor':'NVIDIA','model':parts[0],'memory_gb':round(int(parts[1])/1024,1),'health':'Healthy'})
    if shutil.which('rocm-smi'):
        for line in execute(['rocm-smi','--showproductname']).splitlines():
            if 'Card series' in line: accelerators.append({'vendor':'AMD','model':line.split(':')[-1].strip(),'memory_gb':0,'health':'Healthy'})
    STATE['accelerators'] = accelerators
    return {'source':platform.node() or platform.system(),'accelerators':accelerators,'count':len(accelerators)}
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT),**kwargs)
    def send_json(self,value,status=200):
        data=json.dumps(value).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == '/api/v1/health': return self.send_json({'status':'healthy'})
        if self.path == '/api/v1/accelerators': return self.send_json({'accelerators':STATE['accelerators']})
        if self.path == '/api/v1/clusters': return self.send_json({'clusters':[]})
        return super().do_GET()
    def do_POST(self):
        if self.path == '/api/v1/discovery/scan': return self.send_json(discover())
        return self.send_json({'error':'not found'},404)
    def log_message(self,fmt,*args): print('OpenMycelium API:',fmt % args)
def run():
    server=ThreadingHTTPServer(('127.0.0.1',8080),Handler); print('OpenMycelium dashboard: http://127.0.0.1:8080'); server.serve_forever()
if __name__ == '__main__': run()
