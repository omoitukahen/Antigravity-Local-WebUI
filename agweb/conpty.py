"""A suspended, Job-owned ConPTY client. No shell or inherited handles."""
import ctypes as C
from ctypes import wintypes as W
import subprocess
import threading
import pywintypes
import win32api
import win32con
import win32file
import win32job
import win32pipe
import win32process
from .config import CLI_ENV_OVERRIDES
from .windows import JobProcess


class COORD(C.Structure):
    _fields_=[('X',C.c_short),('Y',C.c_short)]


class STARTUPINFO(C.Structure):
    _fields_=[('cb',W.DWORD),('lpReserved',W.LPWSTR),('lpDesktop',W.LPWSTR),('lpTitle',W.LPWSTR),
        ('dwX',W.DWORD),('dwY',W.DWORD),('dwXSize',W.DWORD),('dwYSize',W.DWORD),
        ('dwXCountChars',W.DWORD),('dwYCountChars',W.DWORD),('dwFillAttribute',W.DWORD),
        ('dwFlags',W.DWORD),('wShowWindow',W.WORD),('cbReserved2',W.WORD),('lpReserved2',C.c_void_p),
        ('hStdInput',W.HANDLE),('hStdOutput',W.HANDLE),('hStdError',W.HANDLE)]


class STARTUPINFOEX(C.Structure):
    _fields_=[('StartupInfo',STARTUPINFO),('lpAttributeList',C.c_void_p)]


class PROCESS_INFORMATION(C.Structure):
    _fields_=[('hProcess',W.HANDLE),('hThread',W.HANDLE),('dwProcessId',W.DWORD),('dwThreadId',W.DWORD)]


k=C.WinDLL('kernel32',use_last_error=True)
k.CreatePseudoConsole.argtypes=[COORD,W.HANDLE,W.HANDLE,W.DWORD,C.POINTER(W.HANDLE)]
k.CreatePseudoConsole.restype=C.c_long
k.ClosePseudoConsole.argtypes=[W.HANDLE]
k.ClosePseudoConsole.restype=None
k.InitializeProcThreadAttributeList.argtypes=[C.c_void_p,W.DWORD,W.DWORD,C.POINTER(C.c_size_t)]
k.UpdateProcThreadAttribute.argtypes=[C.c_void_p,W.DWORD,C.c_size_t,C.c_void_p,C.c_size_t,C.c_void_p,C.c_void_p]
k.DeleteProcThreadAttributeList.argtypes=[C.c_void_p]
k.CreateProcessW.argtypes=[W.LPCWSTR,W.LPWSTR,C.c_void_p,C.c_void_p,W.BOOL,W.DWORD,
    C.c_void_p,W.LPCWSTR,C.POINTER(STARTUPINFOEX),C.POINTER(PROCESS_INFORMATION)]


def check(ok):
    if not ok:raise C.WinError(C.get_last_error())


class ConPTYProcess(JobProcess):
    def __init__(self,argv,cwd,env):
        self.job=win32job.CreateJobObject(None,'')
        limits=win32job.QueryInformationJobObject(self.job,win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags']=win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self.job,win32job.JobObjectExtendedLimitInformation,limits)
        child_in,self.stdin=win32pipe.CreatePipe(None,0)
        self.stdout,child_out=win32pipe.CreatePipe(None,0)
        self.process=None;self.hpc=W.HANDLE();thread=None;attrs=None
        try:
            hr=k.CreatePseudoConsole(COORD(140,100),int(child_in),int(child_out),0,C.byref(self.hpc))
            if hr:raise OSError('CreatePseudoConsole failed')
            size=C.c_size_t()
            k.InitializeProcThreadAttributeList(None,1,0,C.byref(size))
            attrs=C.create_string_buffer(size.value)
            check(k.InitializeProcThreadAttributeList(attrs,1,0,C.byref(size)))
            check(k.UpdateProcThreadAttribute(attrs,0,0x00020016,self.hpc,C.sizeof(self.hpc),None,None))
            startup=STARTUPINFOEX();startup.StartupInfo.cb=C.sizeof(startup)
            # Null std handles prevent Windows from duplicating the worker's
            # redirected JSON pipes into the client instead of using ConPTY.
            startup.StartupInfo.dwFlags=win32process.STARTF_USESTDHANDLES
            startup.lpAttributeList=C.cast(attrs,C.c_void_p)
            pi=PROCESS_INFORMATION()
            env=dict(env,**CLI_ENV_OVERRIDES)
            block=C.create_unicode_buffer('\0'.join(f'{key}={value}' for key,value in sorted(env.items(),key=lambda p:p[0].upper()))+'\0\0')
            command=C.create_unicode_buffer(subprocess.list2cmdline([str(a) for a in argv]))
            check(k.CreateProcessW(str(argv[0]),command,None,None,False,
                win32process.CREATE_SUSPENDED|0x00080000|win32con.CREATE_UNICODE_ENVIRONMENT,
                block,str(cwd),C.byref(startup),C.byref(pi)))
            self.pid=pi.dwProcessId;self.process=pywintypes.HANDLE(pi.hProcess);thread=pywintypes.HANDLE(pi.hThread)
            win32job.AssignProcessToJobObject(self.job,self.process)
            win32process.ResumeThread(thread)
        except BaseException:
            if self.process:win32api.TerminateProcess(self.process,1)
            self.close()
            raise
        finally:
            if thread:thread.Close()
            if attrs:k.DeleteProcThreadAttributeList(attrs)
            child_in.Close();child_out.Close()

    def write(self,data):
        win32file.WriteFile(self.stdin,data)

    def close(self):
        self.job.Close()
        # ClosePseudoConsole can emit a final frame; the independent reader stays alive.
        if self.hpc:
            hpc=self.hpc;self.hpc=W.HANDLE()
            closer=threading.Thread(target=k.ClosePseudoConsole,args=(hpc,),daemon=True)
            closer.start();closer.join(1)
        for h in (self.stdin,self.stdout,self.process):
            if h:
                try:h.Close()
                except Exception:pass
