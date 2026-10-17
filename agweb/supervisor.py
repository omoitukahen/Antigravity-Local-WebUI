import json
import os
from pathlib import Path
import queue
import secrets
import sys
import threading
import time
from multiprocessing.connection import Listener
import win32api
import win32con
import win32event
import win32file
from .windows import JobProcess, Mutex
from .policy import tool_decision
from .output import finish_output, allocate_output

def main():
    first = bytearray()
    while not first.endswith(b'\n'):
        char=os.read(0,1)
        if not char: raise EOFError('missing specification')
        first.extend(char)
        if len(first)>256*1024: raise ValueError('specification too large')
    spec = json.loads(first)
    commands, output = queue.Queue(), queue.Queue(maxsize=4096)
    stdout_done = threading.Event()
    stop_lock = threading.Lock()
    stop_reason = [None]
    submitted = False
    job = None
    listener = None
    lock = Mutex(spec['mutex'] + '_run')
    parent = win32api.OpenProcess(win32con.SYNCHRONIZE, False, spec['parent_pid'])

    def stop(reason):
        with stop_lock:
            if stop_reason[0] is None:
                stop_reason[0] = reason

    def emit(kind, **fields):
        try:
            output.put_nowait({'kind':kind, **fields})
        except queue.Full:
            stop('output_backpressure')

    def writer():
        while True:
            message = output.get()
            if message is None:
                return
            try:
                data=(json.dumps(message, ensure_ascii=False)+'\n').encode('utf-8')
                while data:
                    data=data[os.write(1,data):]
            except (OSError, BrokenPipeError):
                stop('server_disconnected')
                return

    output_thread=threading.Thread(target=writer, daemon=True)
    output_thread.start()

    def controls():
        pending=b''
        try:
            while True:
                block=os.read(0,4096)
                if not block:break
                pending+=block
                while b'\n' in pending:
                    line,pending=pending.split(b'\n',1)
                    commands.put(('control', json.loads(line)))
        except Exception:
            stop('server_disconnected')
        finally:
            stop('server_disconnected')

    def hook_clients():
        while True:
            try:
                connection = listener.accept()
            except (OSError, EOFError):
                return
            def handle(connection):
                try:
                    if not connection.poll(2):
                        raise TimeoutError()
                    request = json.loads(connection.recv_bytes(1024*1024))
                    answer = queue.Queue(maxsize=1)
                    commands.put(('hook', (request, answer)))
                    reply = answer.get(timeout=2)
                    connection.send_bytes(json.dumps(reply).encode())
                except Exception:
                    stop('guard_error')
                finally:
                    connection.close()
            threading.Thread(target=handle, args=(connection,), daemon=True).start()

    def stdout_reader():
        pending = b''
        total = 0
        try:
            while True:
                _, block = win32file.ReadFile(job.stdout, 4096)
                if not block:
                    break
                total += len(block)
                if total > 16*1024*1024:
                    stop('output_limit'); break
                pending += block
                while b'\n' in pending:
                    line, pending = pending.split(b'\n',1)
                    if len(line) > 1024*1024:
                        stop('line_limit'); return
                    if line.strip():
                        commands.put(('event', json.loads(line.decode('utf-8'))))
                if len(pending) > 1024*1024:
                    stop('line_limit'); break
            if pending.strip():
                commands.put(('event', json.loads(pending.decode('utf-8'))))
        except Exception as e:
            if getattr(e, 'winerror', None) not in (109, 232, 6):
                stop('invalid_output')
            elif pending.strip() and not stop_reason[0]:
                try:
                    commands.put(('event', json.loads(pending.decode('utf-8'))))
                except Exception:
                    stop('invalid_output')
        finally:
            stdout_done.set()

    def stderr_reader():
        # Raw diagnostics never leave this process. Only category booleans are retained.
        tail = b''
        try:
            while True:
                _, block = win32file.ReadFile(job.stderr, 4096)
                if not block:
                    return
                tail = (tail + block)[-256*1024:]
                if b'authentication required' in tail.lower() or b'not logged in' in tail.lower():
                    emit('diagnostic', category='authentication_required')
        except Exception:
            return

    started = time.monotonic()
    last_server = last_browser = started
    result = None
    result_at = None
    exit_at = None
    conversation_id = None
    guard_seen = False
    tool_receipts = set()
    observed_tools = set()
    output_used=False
    pending_output_tools={}
    try:
        if not lock.acquire():
            raise RuntimeError('run_mutex_busy')
        pipe = r'\\.\pipe\AGWeb_' + secrets.token_hex(16)
        key = secrets.token_bytes(32)
        listener = Listener(pipe, family='AF_PIPE', authkey=key)
        threading.Thread(target=hook_clients, daemon=True).start()
        threading.Thread(target=controls, daemon=True).start()
        env = dict(os.environ)
        env.update(PYTHONUTF8='1', AGWEB_HOOK_PIPE=pipe, AGWEB_HOOK_KEY=key.hex())
        if spec.get('mock'):
            env['AGWEB_MOCK_SCENARIO'] = spec.get('scenario','normal')
            if spec.get('output_target'):env['AGWEB_TEXT_OUTPUT_TARGET']=spec['output_target']
        if spec.get('hook_fault'):
            env['AGWEB_HOOK_FAULT'] = spec['hook_fault']
        started = time.monotonic()
        last_server = last_browser = started
        job = JobProcess(spec['argv'], spec['cwd'], env)
        emit('spawn', pid=job.pid, supervisor_pid=os.getpid())
        threading.Thread(target=stdout_reader,daemon=True).start()
        threading.Thread(target=stderr_reader,daemon=True).start()
        payload = (json.dumps({'event':'user','message':{'content':spec['prompt']}}, ensure_ascii=False)+'\n').encode('utf-8')
        # A CLI that never reads stdin must not block the deadline watchdog.
        # Once a write begins, conservatively consider this conversation submitted.
        submitted = True
        emit('submitted')
        def input_writer():
            try:job.send(payload)
            except Exception:stop('input_failed')
        threading.Thread(target=input_writer,daemon=True).start()
        while True:
            now = time.monotonic()
            if any(now>=deadline for deadline in pending_output_tools.values()):stop('guard_not_observed')
            if now - started >= spec['timeout']:
                stop('time_limit')
            if win32event.WaitForSingleObject(parent, 0) == win32event.WAIT_OBJECT_0 or now-last_server >= spec.get('server_timeout',5):
                stop('server_disconnected')
            if now-last_browser >= spec.get('browser_timeout',15):
                stop('browser_disconnected')
            if result_at and now-result_at >= spec.get('cleanup_timeout',5):
                stop('exit_timeout')
            if stop_reason[0]:
                emit('stopping', reason=stop_reason[0])
                job.kill()
                deadline = time.monotonic()+spec.get('cleanup_timeout',5)
                while job.active() and time.monotonic() < deadline:
                    time.sleep(.02)
                if job.active():
                    emit('stop_failed',reason='stop_confirmation_failed')
                    # Keep the mutex and job alive. Do not claim cleanup or allow another run.
                    while job.active():
                        time.sleep(.1)
                break
            if job.exited() and stdout_done.is_set() and commands.empty():
                if exit_at is None:exit_at=now
                if job.active():
                    if now-exit_at >= spec.get('cleanup_timeout',5):stop('descendants_remain')
                    time.sleep(.01)
                    continue
                if not result or not conversation_id:
                    stop('missing_result')
                elif result.get('status') != 'SUCCESS' or job.code() != 0:
                    stop('cli_failed')
                elif not guard_seen or pending_output_tools:
                    stop('guard_not_observed')
                break
            try:
                kind, message = commands.get(timeout=.025)
            except queue.Empty:
                continue
            if kind == 'control':
                if message['kind'] == 'server_ping':
                    last_server = time.monotonic()
                elif message['kind'] == 'browser_ping':
                    last_browser = time.monotonic()
                elif message['kind'] == 'stop':
                    stop(message.get('reason','manual'))
            elif kind == 'hook':
                request, answer = message
                payload = request.get('payload',{})
                if request.get('kind') == 'pre_invocation':
                    guard_seen = True
                    actual = payload.get('modelName')
                    if actual != spec['model'] or (conversation_id and payload.get('conversationId')!=conversation_id):
                        stop('model_unverified')
                        reply = {'injectSteps':[]}
                    else:
                        emit('guard', stage='pre_invocation', model=actual,invocation=payload.get('invocationNum'))
                        reply = {'injectSteps':[]}
                elif request.get('kind') == 'pre_tool':
                    if not spec.get('output_root') and payload.get('toolCall',{}).get('name')=='write_to_file':
                        stop('output_mode_mismatch')
                        emit('guard',stage='denied',reason='output_mode_mismatch',tool='write_to_file')
                        answer.put({'decision':'deny','reason':'WebUI: select text_file output mode'})
                        continue
                    allowed, reason = tool_decision(payload,spec.get('output_root'),output_used)
                    idx=payload.get('stepIdx')
                    correlated=isinstance(idx,int) and not isinstance(idx,bool) and idx>=0 and payload.get('conversationId')==conversation_id
                    if allowed and guard_seen and correlated:
                        name=payload['toolCall']['name']
                        reply = {'decision':'allow','reason':'WebUI scoped tool policy'}
                        if name=='write_to_file':
                            try:
                                target=allocate_output(payload['toolCall']['args']['TargetFile'],spec['output_root'])
                            except (OSError,ValueError):
                                stop('guard_denied')
                                answer.put({'decision':'deny','reason':'WebUI: invalid output path'})
                                continue
                            spec['output_target']=str(target)
                            output_used=True
                            emit('output_assigned',name=target.name)
                            reply.update(decision='auto_approve',overwrite={'TargetFile':str(target)})
                        tool_receipts.add((name,idx))
                        pending_output_tools.pop((name,idx),None)
                        emit('guard',stage='tool',tool=name,step_index=idx,decision=reply['decision'])
                    else:
                        stop('guard_denied')
                        reply = {'decision':'deny','reason':'WebUI: '+reason}
                        emit('guard',stage='denied',reason=reason)
                else:
                    stop('guard_error')
                    reply = {'decision':'deny','reason':'unsupported hook'}
                answer.put(reply)
            elif kind == 'event':
                event = message.get('event')
                if event == 'init':
                    if conversation_id is not None:
                        stop('duplicate_init'); continue
                    conversation_id = message.get('conversation_id')
                    if not isinstance(conversation_id,str) or not conversation_id:
                        stop('invalid_conversation_id'); continue
                    if spec.get('conversation_id') and conversation_id != spec['conversation_id']:
                        stop('conversation_mismatch'); continue
                    emit('init',conversation_id=conversation_id)
                    init=message.get('init',{})
                    if init.get('model') is not None and init['model']!=spec['model']:
                        stop('model_unverified')
                    if init.get('permission_mode')=='always-proceed':stop('unsafe_permissions')
                    emit('configuration',model=init.get('model'),effort=init.get('effort'),permission_mode=init.get('permission_mode'),tools=init.get('tools'))
                elif event == 'step_update':
                    step = message.get('step_update',{})
                    if step.get('conversation_id') != conversation_id:
                        stop('conversation_mismatch'); continue
                    if step.get('subagent_info'):
                        stop('subagent_detected'); continue
                    if step.get('step_type') == 'tool':
                        if not spec.get('output_root') and step.get('tool_name')=='write_to_file':
                            stop('output_mode_mismatch');continue
                        receipt=(step.get('tool_name'),step.get('step_index'))
                        if not guard_seen or receipt not in tool_receipts:
                            # File-tool ACTIVE is a proposal notification. Its
                            # permission hook can arrive on another pipe next.
                            # Never accept a DONE/unrecognized step without a receipt.
                            index=step.get('step_index')
                            if (guard_seen and spec.get('output_target') and receipt[0]=='write_to_file'
                                and isinstance(index,int) and not isinstance(index,bool) and index>=0
                                and step.get('state')=='ACTIVE' and not output_used
                                and (not pending_output_tools or receipt in pending_output_tools)):
                                pending_output_tools.setdefault(receipt,time.monotonic()+2)
                                continue
                            emit('diagnostic',category='tool_guard_unverified',tool=step.get('tool_name'),step_index=index,state=step.get('state'))
                            stop('guard_not_observed'); continue
                        observed_tools.add(receipt)
                    delta = step.get('text_delta')
                    if delta is not None and step.get('step_type')=='agent_response':
                        if not isinstance(delta,str):
                            stop('invalid_output'); continue
                        if not spec.get('output_target'):emit('delta',text=delta)
                    tool = step.get('tool_name')
                    if tool:
                        emit('tool',name=tool)
                elif event == 'result':
                    if result is not None:
                        stop('duplicate_result'); continue
                    result = message.get('result',{})
                    if result.get('conversation_id') != conversation_id:
                        stop('conversation_mismatch')
                    if not isinstance(result.get('response',''),str):
                        stop('invalid_output')
                    result_at = time.monotonic()
                    emit('result',response='' if spec.get('output_target') else result.get('response',''),usage=result.get('usage'),status=result.get('status'))
                else:
                    stop('unsupported_event')
        reason = stop_reason[0]
        state = 'completed' if reason is None else ('timed_out' if reason == 'time_limit' else ('cancelled' if reason in {'manual','browser_disconnected','server_disconnected','app_shutdown'} else 'failed'))
        artifact=None
        if spec.get('output_target'):
            artifact=finish_output(spec['output_target'],spec.get('output_root') or spec['cwd'],state=='completed',output_used)
            if not output_used:artifact['name']=None
            else:artifact['path']=spec['output_target']
            if state=='completed' and artifact['state']!='completed':
                state='failed';reason='output_'+artifact['state']
        emit('terminal',state=state,reason=reason,submitted=submitted,conversation_id=conversation_id,
             exit_code=job.code(),active=job.active(),response='' if spec.get('output_target') else (result or {}).get('response'),usage=(result or {}).get('usage'),output=artifact)
    except Exception:
        if job:
            job.kill()
            until = time.monotonic()+5
            while job.active() and time.monotonic()<until:
                time.sleep(.02)
            if job.active():
                emit('stop_failed',reason='stop_confirmation_failed')
                while job.active():time.sleep(.1)
        emit('terminal',state='failed',reason='supervisor_startup_error',submitted=submitted,conversation_id=conversation_id,
             active=job.active() if job else 0,exit_code=None,response=None,usage=None)
    finally:
        if job:
            job.close()
        if listener:
            listener.close()
        parent.Close()
        lock.close()
        try:
            output.put(None,timeout=1)
        except queue.Full:
            pass
        # Give the output writer time to deliver terminal evidence before process exit.
        output_thread.join(1)

if __name__ == '__main__':
    main()
