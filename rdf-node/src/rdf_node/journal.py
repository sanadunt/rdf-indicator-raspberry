from __future__ import annotations
import json
import sqlite3
import threading
from pathlib import Path
from .util import compact, digest, now_ms

TERMINAL={'APPLIED','FAILED','REJECTED','EXPIRED','CONFLICT','PERSISTED_UNVERIFIED','OUTCOME_UNKNOWN','CANCELLED'}
class Journal:
    def __init__(self, path:Path):
        path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.db=sqlite3.connect(path,check_same_thread=False,timeout=5)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, hash TEXT NOT NULL, request TEXT NOT NULL, actor TEXT NOT NULL, stage TEXT NOT NULL, result TEXT NOT NULL, updated INTEGER NOT NULL)')
        self.db.commit()
    def close(self):
        with self.lock: self.db.close()
    def get(self,key,default=None):
        with self.lock:
            r=self.db.execute('SELECT v FROM kv WHERE k=?',(key,)).fetchone()
            return json.loads(r[0]) if r else default
    def set(self,key,value):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',(key,compact(value).decode()))
    def config_revision(self,h):
        with self.lock,self.db:
            old=self.get('config',{})
            if old.get('digest')==h: return old['rev']
            rev=old.get('rev',0)+1
            self.set('config',{'rev':rev,'digest':h})
            return rev
    def lookup(self,id):
        with self.lock:
            r=self.db.execute('SELECT hash, request, actor, stage, result, updated FROM operations WHERE id=?',(id,)).fetchone()
            if not r: return None
            return dict(id=id,hash=r[0],request=json.loads(r[1]),actor=r[2],stage=r[3],result=json.loads(r[4]),updated_ms=r[5])
    def accept(self,request,actor):
        with self.lock,self.db:
            old=self.lookup(request['id'])
            h=digest(request)
            if old:
                if old['hash']!=h: raise ValueError('COMMAND_ID_CONFLICT')
                return False,old
            self.db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?)',
                (request['id'],h,compact(request).decode(),actor,'ACCEPTED','{}',now_ms()))
            return True,self.lookup(request['id'])
    def update(self,id,stage,result=None):
        with self.lock,self.db:
            self.db.execute('UPDATE operations SET stage=?,result=?,updated=? WHERE id=?',
                (stage,compact(result or {}).decode(),now_ms(),id))
        return self.lookup(id)
    def latest(self,n=20):
        with self.lock:
            ids=[r[0] for r in self.db.execute('SELECT id FROM operations ORDER BY updated DESC LIMIT ?',(n,))]
            return [self.lookup(i) for i in ids]
    def pending_shutdowns(self):
        failures={'FAILED','REJECTED','EXPIRED','CONFLICT','CANCELLED'}
        with self.lock:
            rows=self.db.execute('SELECT id,request,stage FROM operations').fetchall()
        return [{'id':id,'stage':stage} for id,request,stage in rows
                if stage not in failures and json.loads(request).get('op')=='system.shutdown.execute']
    def pending_ppp_restarts(self):
        terminal={'APPLIED','FAILED','REJECTED','EXPIRED','CONFLICT','CANCELLED','PERSISTED_UNVERIFIED'}
        with self.lock:
            rows=self.db.execute('SELECT id,request,stage,result FROM operations').fetchall()
        pending=[]
        for id,raw_request,stage,raw_result in rows:
            request=json.loads(raw_request); result=json.loads(raw_result)
            if request.get('op')!='ppp.restart' or stage in terminal: continue
            if stage=='OUTCOME_UNKNOWN' and result.get('confirmed_by'): continue
            pending.append(dict(id=id,request=request,stage=stage,result=result))
        return pending
    def recover(self,boot):
        recovered=[]
        with self.lock:
            rows=self.db.execute('SELECT id,stage,request,result FROM operations').fetchall()
            for id,stage,r,rs in rows:
                if stage in TERMINAL: continue
                req=json.loads(r); result=json.loads(rs)
                if stage=='REBOOT_SCHEDULED' and req.get('boot')!=boot:
                    recovered.append(self.update(id,'APPLIED',{'proof':'OS_BOOT_ID_CHANGED','previous_boot':req.get('boot'),'boot':boot}))
                else:
                    recovered.append(self.update(id,'OUTCOME_UNKNOWN',{'reason':'AGENT_RESTARTED_RECONCILE_NO_REPLAY'}))
        return recovered
    def prune(self,max_rows=2000,days=30):
        cutoff=now_ms()-days*86400000
        with self.lock,self.db:
            terminal=tuple(TERMINAL-{'OUTCOME_UNKNOWN'})
            marks=','.join('?' for _ in terminal)
            self.db.execute(f'DELETE FROM operations WHERE stage IN ({marks}) AND updated<?',(*terminal,cutoff))
            self.db.execute(f'DELETE FROM operations WHERE id IN (SELECT id FROM operations WHERE stage IN ({marks}) ORDER BY updated DESC LIMIT -1 OFFSET ?)',(*terminal,max_rows))
