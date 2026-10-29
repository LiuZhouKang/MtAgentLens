# Synthetic localhost and temporary-file capture checks; no external traffic.
import json,os,pathlib,signal,socket,subprocess,tempfile,time,threading
root=pathlib.Path(__file__).resolve().parents[2]
if os.geteuid() != 0:
 raise SystemExit('This synthetic kernel test requires root privileges.')
with tempfile.TemporaryDirectory(prefix='mtl-capture-fixture-') as d:
 p=pathlib.Path(d)
 with open(p/'events.jsonl','w') as out,open(p/'helper.stderr','w') as err:
  helper=subprocess.Popen([str(root/'bpftools/.output/bin/process'),'-m','2','-p',str(os.getpid())],stdout=out,stderr=err)
  try:
   time.sleep(1)
   assert helper.poll() is None, 'probe load failed'
   target=p/'operation.txt'
   target.write_text('fixture')
   target.read_text()
   os.close(os.open(target,os.O_WRONLY|os.O_TRUNC))
   pathlib.Path('/proc/self/status').read_text()
   worker=threading.Thread(target=lambda: target.read_text());worker.start();worker.join()
   target.read_text()
   try: os.open(p/'missing',os.O_RDONLY)
   except FileNotFoundError: pass
   child=os.fork()
   if child==0:
    (p/'fork-only.txt').write_text('child')
    os._exit(0)
   os.waitpid(child,0)
   listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
   port=listener.getsockname()[1]
   client=socket.socket();client.connect(('127.0.0.1',port));client.close();listener.close()
   refused=socket.socket()
   try: refused.connect(('127.0.0.1',port))
   except ConnectionRefusedError: pass
   refused.close()
   time.sleep(.3)
  finally:
   helper.send_signal(signal.SIGTERM)
   helper.wait(timeout=4)
 rows=[json.loads(line) for line in (p/'events.jsonl').read_text().splitlines()]
 own=[row for row in rows if row.get('pid')==os.getpid()]
 checks={
  'read_and_write_flags_preserved': len({row['flags'] for row in own if row.get('filepath')==str(target)})>=2,
  'proc_path_visible': any(row.get('filepath')=='/proc/self/status' for row in own),
  'missing_open_failed': any(row.get('filepath')==str(p/'missing') and row.get('outcome')=='failed' and row.get('syscall_ret')==-2 for row in own),
  'fork_without_exec_visible': any(row.get('filepath')==str(p/'fork-only.txt') and row.get('pid')==child and row.get('ppid')==os.getpid() for row in rows),
  'connect_succeeded': any(row.get('event')=='NETWORK_CONNECT' and row.get('outcome')=='succeeded' for row in own),
  'connect_refused': any(row.get('event')=='NETWORK_CONNECT' and row.get('outcome')=='failed' and row.get('syscall_ret')==-111 for row in own),
  'thread_identity_stable': len({row['start_time_ns'] for row in own})==1,
  'final_stats': 'PROCESS_RINGBUF_STATS' in (p/'helper.stderr').read_text(),
 }
 print(json.dumps(checks,indent=2))
 assert all(checks.values())
