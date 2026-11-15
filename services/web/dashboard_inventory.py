"""One bounded, read-only snapshot of existing dashboard caches."""
import hashlib,json,time

def snapshot(base, inventory, detail_filter, extended_filter):
    def read(name):
        try:
            value=json.loads((base/'data'/name).read_text())
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError):return {}
    now=int(time.time());d=detail_filter(read('details.json'));e=extended_filter(read('extended.json'))
    def state(payload):
        at=payload.get('collected_at',0)
        age=now-at if isinstance(at,(int,float)) else None
        return {'collected_at':at,'age_seconds':age,'fresh':age is not None and 0<=age<=120}
    ids={str(n['id']) for n in inventory.get('nodes',[])}
    result={**inventory,'details':{k:v for k,v in d.get('details',{}).items() if k in ids},'extended':{k:v for k,v in e.get('nodes',{}).items() if k in ids},'collection':{'details':state(d),'extended':state(e)}}
    # Revision excludes clock-derived age so unchanged cache data stays unchanged.
    result['revision']=hashlib.sha256(json.dumps([inventory,result['details'],result['extended'],d.get('collected_at'),e.get('collected_at')],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result
