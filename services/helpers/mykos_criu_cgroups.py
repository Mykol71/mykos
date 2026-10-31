"""Scoped cgroup-v2 capture and restore ordering for native systemd guests."""
import os
from pathlib import Path
import re
import subprocess

CGROUP = Path('/sys/fs/cgroup')
PROC = Path('/proc')


def checked_root(value, container_id=None):
    path=value.get('path','')
    if not isinstance(path,str) or not path.startswith('/') or '..' in Path(path).parts or '\\' in path:
        raise ValueError('Invalid container cgroup boundary')
    name=Path(path).name
    if not re.fullmatch(r'libpod-[0-9a-f]{64}(?:\.scope)?',name):
        raise ValueError('Expected a private Podman cgroup')
    if container_id and name not in ('libpod-'+container_id,'libpod-'+container_id+'.scope'):
        raise ValueError('Container cgroup identity mismatch')
    root=CGROUP/path.lstrip('/')
    if any(p.is_symlink() for p in (root,*root.parents)):
        raise ValueError('Symlink container cgroup rejected')
    return root


def controllers(root):
    values=(root/'cgroup.subtree_control').read_text().split()
    if any(not re.fullmatch('[a-z_]+',v) for v in values):raise ValueError('Invalid cgroup controller')
    return sorted(set(values))


def namespace_root(value):
    checked_root(value)
    path=value.get('namespace_path',value['path'])
    if not isinstance(path,str) or not (path==value['path'] or path.startswith(value['path']+'/')) or '..' in Path(path).parts or '\\' in path:
        raise ValueError('Cgroup namespace escaped its container boundary')
    result=CGROUP/path.lstrip('/')
    if any(p.is_symlink() for p in (result,*result.parents)):raise ValueError('Symlink cgroup namespace rejected')
    return result


def namespace_boundary(outside,inside,boundary):
    if not inside.startswith('/') or '..' in Path(inside).parts:raise ValueError('Invalid namespace cgroup membership')
    if inside=='/':path=outside
    elif outside.endswith(inside):path=outside[:-len(inside)] or '/'
    else:raise ValueError('Cgroup namespace membership mismatch')
    if not (path==boundary or path.startswith(boundary+'/')):raise ValueError('Cgroup namespace escaped its container boundary')
    return path


def legacy_systemd_path(value):
    path=value.get('legacy_systemd_path')
    if path is None:return None
    identity=re.fullmatch(r'libpod-([0-9a-f]{64})(?:\.scope)?',checked_root(value).name).group(1)
    if path!='/mykos/'+identity:raise ValueError('Legacy cgroup escaped its container identity')
    return path


def delegation_state(value):
    root=checked_root(value);paths=[root]+[p.parent for p in root.rglob('cgroup.subtree_control') if p.parent!=root]
    if len(paths)>8192:raise ValueError('Container cgroup hierarchy is too large')
    result={}
    for node in paths:
        if node.is_symlink() or node.stat().st_dev!=root.stat().st_dev:raise ValueError('Unexpected cgroup delegation mount')
        enabled=controllers(node)
        if enabled:result[str(node.relative_to(root))]=enabled
    return result


def capture_context(state):
    if not (CGROUP/'cgroup.controllers').is_file():return None
    value={'path':state.get('State',{}).get('CgroupPath','')}
    root=checked_root(value,state['Id'])
    membership=(PROC/str(int(state['State']['Pid']))/'cgroup').read_text().splitlines()
    paths=[line[3:] for line in membership if line.startswith('0::')]
    if len(paths)!=1 or not (paths[0]==value['path'] or paths[0].startswith(value['path']+'/')):
        raise ValueError('Container process escaped its cgroup boundary')
    # Podman's resource scope and the kernel cgroup namespace root differ
    # under crun (scope/container). Query only that namespace with host tools;
    # no guest program or cgroup membership is changed.
    pid=str(int(state['State']['Pid']))
    result=subprocess.run(['/usr/bin/nsenter','--cgroup='+str(PROC/pid/'ns/cgroup'),'--','/usr/bin/cat',str(PROC/pid/'cgroup')],capture_output=True,text=True,timeout=10,check=True)
    relative=[line[3:] for line in result.stdout.splitlines() if line.startswith('0::')]
    if len(relative)!=1:raise ValueError('Missing namespace cgroup membership')
    if (PROC/pid/'cgroup').read_text().splitlines()!=membership:raise ValueError('Container cgroup membership changed during capture')
    value['namespace_path']=namespace_boundary(paths[0],relative[0],value['path'])
    value['controllers']=controllers(root)
    value['namespace_controllers']=controllers(namespace_root(value))
    value['delegation']=delegation_state(value)
    if (state.get('Config',{}).get('Labels') or {}).get('io.mykos.os')=='rh7':
        expected='/mykos/'+state['Id']
        outside=[line.split(':',2)[2] for line in membership if line.split(':',2)[1]=='name=systemd']
        inside=[line.split(':',2)[2] for line in result.stdout.splitlines() if line.split(':',2)[1]=='name=systemd']
        if len(outside)!=1 or len(inside)!=1 or namespace_boundary(outside[0],inside[0],expected)!=expected:
            raise ValueError('RH7 requires a private legacy cgroup; recreate this instance with the current runtime')
        value['legacy_systemd_path']=expected
    return value


def restore_context(state,metadata):
    if not (CGROUP/'cgroup.controllers').is_file():return None
    value=metadata.get('cgroup')
    if not value:raise ValueError('Take a fresh snapshot: this capture predates complete cgroup-v2 state')
    checked_root(value,state['Id'])
    namespace_root(value)
    if (state.get('Config',{}).get('Labels') or {}).get('io.mykos.os')=='rh7' and not legacy_systemd_path(value):
        raise ValueError('Take a fresh snapshot: this RH7 capture predates isolated legacy cgroups')
    if state.get('OCIRuntime')=='crun' and 'namespace_path' not in value:
        raise ValueError('Take a fresh snapshot: this capture predates the crun namespace boundary fix')
    current=state.get('State',{}).get('CgroupPath')
    if current and current!=value['path']:raise ValueError('Container cgroup configuration changed')
    Transaction(value,'restore')
    return value


class Transaction:
    def __init__(self,value,operation):
        self.value=value;self.operation=operation;self.root=checked_root(value)
        self.worker=None;self.peer=None;self.park=None;self.parks=[]
        self.prepared=False;self.started=False;self.succeeded=False
        self.namespace_started=False;self.disabled=[]
        values=value.get('controllers')
        if not isinstance(values,list) or any(not isinstance(v,str) or not re.fullmatch('[a-z_]+',v) for v in values):
            raise ValueError('Invalid saved cgroup controllers')
        self.namespace=namespace_root(value)
        self.legacy=legacy_systemd_path(value)
        self.delegation={}
        saved=value.get('delegation',{})
        if not isinstance(saved,dict) or len(saved)>8192:raise ValueError('Invalid saved cgroup delegation')
        for relative,enabled in saved.items():
            if (not isinstance(relative,str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts or '\x00' in relative
                    or str(Path(relative))!=relative):raise ValueError('Saved cgroup delegation escaped its container')
            if not isinstance(enabled,list) or any(not isinstance(v,str) or not re.fullmatch('[a-z_]+',v) for v in enabled) or not set(enabled)<=set(values):
                raise ValueError('Invalid saved child cgroup controllers')
            self.delegation[self.root/relative]=enabled

        namespace_values=value.get('namespace_controllers',values)
        self.namespace_values=namespace_values
        if not isinstance(namespace_values,list) or any(not isinstance(v,str) or not re.fullmatch('[a-z_]+',v) for v in namespace_values) or not set(namespace_values)<=set(values):
            raise ValueError('Invalid namespace cgroup controllers')

    def request(self,packet):
        from mykos_criu import fields,integer,replace_fields,wire_fields
        message=fields(packet);action=message.get(1)
        if action not in (1,2):return packet
        expected=1 if self.operation=='capture' else 2
        if action!=expected:raise ValueError('Unexpected CRIU transaction operation')
        options=message[2];path=os.fsencode(self.value.get('namespace_path',self.value['path']))
        roots=[fields(v) for n,w,v,_ in wire_fields(options) if n==25 and w==2]
        # Empty controller name selects only the unified hierarchy. A global
        # root would incorrectly change the separate RH7 named hierarchy.
        matched=[entry for entry in roots if entry.get(1)==b'']
        allowed={path,os.fsencode(self.value['path'])}
        if matched and any(entry.get(2) not in allowed for entry in matched):
            raise ValueError('Unexpected unified cgroup root')
        if matched:
            rewritten=[]
            for number,wire,value,raw in wire_fields(options):
                if number==25 and wire==2 and fields(value).get(1)==b'':
                    value=replace_fields(value,{2:path});raw=integer((25<<3)|2)+integer(len(value))+value
                rewritten.append(raw)
            options=b''.join(rewritten)
        if not matched:
            entry=b'\x0a\x00'+integer((2<<3)|2)+integer(len(path))+path
            options+=integer((25<<3)|2)+integer(len(entry))+entry
        if self.legacy:
            legacy=os.fsencode(self.legacy);rewritten=[];matched_legacy=False
            for number,wire,value,raw in wire_fields(options):
                if number==25 and wire==2:
                    root=fields(value);controller=root.get(1)
                    if controller is None:
                        # Replace the runtime's all-hierarchy default with the
                        # explicit unified and named roots, never a global root.
                        if root.get(2) not in allowed|{legacy}:raise ValueError('Unexpected global cgroup root')
                        continue
                    if controller in (b'systemd',b'name=systemd'):
                        if root.get(2) not in allowed|{legacy}:raise ValueError('Unexpected legacy cgroup root')
                        value=replace_fields(value,{2:legacy});raw=integer((25<<3)|2)+integer(len(value))+value;matched_legacy=True
                rewritten.append(raw)
            options=b''.join(rewritten)
            if not matched_legacy:
                controller=b'name=systemd'
                entry=b'\x0a'+integer(len(controller))+controller+b'\x12'+integer(len(legacy))+legacy
                options+=integer((25<<3)|2)+integer(len(entry))+entry
        self.started=True
        return replace_fields(packet,{2:options})

    def owned_process(self,pid):
        for _ in range(32):
            if pid in (self.worker,self.peer,os.getpid()):return True
            if pid<=1:return False
            try:pid=int((PROC/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[1])
            except (OSError,ValueError,IndexError):return False
        return False

    def begin_namespace(self):
        if self.namespace_started:raise ValueError('Duplicate restore namespace setup')
        if not self.worker:raise ValueError('Missing CRIU worker identity')
        nodes=[self.root]+sorted((p.parent for p in self.root.rglob('cgroup.subtree_control') if p.parent!=self.root),key=lambda p:len(p.parts))
        if len(nodes)>8192:raise ValueError('Restore cgroup hierarchy is too large')
        plans=[]
        for node in nodes:
            if node.is_symlink() or node.stat().st_dev!=self.root.stat().st_dev:raise ValueError('Unexpected restore cgroup mount')
            present=[int(v) for v in (node/'cgroup.procs').read_text().split()]
            if any(not self.owned_process(pid) for pid in present):raise ValueError('Unrelated process in restore cgroup; left unchanged')
            enabled=controllers(node)
            if enabled and node!=self.root and not set(enabled)<=set(self.value['controllers']):raise ValueError('Unexpected child cgroup controllers')
            if enabled:plans.append((node,enabled))
        self.namespace_started=True
        # crun can pre-enable delegation. CRIU 3.x must briefly place PID 1
        # in this parent to establish its cgroup namespace, which the kernel
        # rejects while controllers are delegated. Disable only this private
        # hierarchy, deepest first, and restore it after namespace setup.
        for node,enabled in reversed(plans):
            self.disabled.append((node,enabled))
            (node/'cgroup.subtree_control').write_text(' '.join('-'+v for v in enabled))

    def enable(self,node,values):
        if not values:return
        present=[int(v) for v in (node/'cgroup.procs').read_text().split()]
        if any(not self.owned_process(pid) for pid in present):raise ValueError('Unrelated process in restore cgroup; left unchanged')
        if present:
            park=node/('.mykos-criu-restore-'+os.urandom(8).hex());park.mkdir(mode=0o755);self.parks.append(park)
            if node==self.root:self.park=park
            for pid in present:(park/'cgroup.procs').write_text(str(pid))
        if (node/'cgroup.procs').read_text().strip():raise ValueError('Restore parent cgroup is still occupied')
        (node/'cgroup.subtree_control').write_text(' '.join('+'+v for v in sorted(values)))

    def notification(self,message):
        from mykos_criu import fields
        if self.operation!='restore':return
        if message.get(1)==2:
            self.succeeded=message.get(2)==1
            return
        if message.get(1)!=6:return
        action=fields(message.get(5,b'')).get(1)
        if action==b'setup-namespaces':
            self.begin_namespace()
            return
        if action!=b'post-setup-namespaces':return
        if self.prepared:raise ValueError('Duplicate restore namespace notification')
        if not self.worker:raise ValueError('Missing CRIU worker identity')
        present=[int(v) for v in (self.root/'cgroup.procs').read_text().split()]
        if any(not self.owned_process(pid) for pid in present):
            raise ValueError('Unrelated process in restore cgroup; left unchanged')
        needed=set(self.value['controllers'])
        available=set((self.root/'cgroup.controllers').read_text().split())
        if not needed<=available:raise ValueError('Saved cgroup controllers are unavailable')
        # PID 1 has established its cgroup namespace and moved to init.scope.
        # Move only our relay/worker descendants out of the parent before
        # enabling controllers (the cgroup-v2 no-internal-process rule).
        self.enable(self.root,needed)
        delegated={node:enabled for node,enabled in self.disabled if node!=self.root}
        if self.namespace!=self.root:delegated[self.namespace]=self.namespace_values
        delegated.update({node:enabled for node,enabled in self.delegation.items() if node!=self.root})
        for node in sorted(delegated,key=lambda p:len(p.parts)):
            if node.exists():self.enable(node,delegated[node])
        self.prepared=True

    def close(self):
        if self.disabled and not self.prepared and self.root.exists():
            for node,enabled in reversed(self.disabled):
                if node.exists():self.enable(node,enabled)
        for park in reversed(self.parks):
            try:park.rmdir()
            except (OSError,FileNotFoundError):pass
