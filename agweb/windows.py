"""Windows handles are owned by the process that creates them."""
import subprocess
import _winapi
import pywintypes
import win32api
import win32con
import win32event
import win32file
import win32job
import win32pipe
import win32process
from .config import CLI_ENV_OVERRIDES

class Mutex:
    def __init__(self, name):
        self.handle = win32event.CreateMutex(None, False, name)
        self.owned = False

    def acquire(self):
        result = win32event.WaitForSingleObject(self.handle, 0)
        self.owned = result in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED)
        return self.owned

    def close(self):
        if self.owned:
            win32event.ReleaseMutex(self.handle)
            self.owned = False
        self.handle.Close()

class JobProcess:
    def __init__(self, argv, cwd, env):
        # 管理対象の子プロセスだけに適用し、親やユーザーの設定を変更しない。
        env = dict(env, **CLI_ENV_OVERRIDES)
        self.job = win32job.CreateJobObject(None, '')
        limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
        sa = pywintypes.SECURITY_ATTRIBUTES()
        sa.bInheritHandle = True
        child_in, self.stdin = win32pipe.CreatePipe(sa, 0)
        self.stdout, child_out = win32pipe.CreatePipe(sa, 0)
        self.stderr, child_err = win32pipe.CreatePipe(sa, 0)
        self.process = None
        for h in (self.stdin, self.stdout, self.stderr):
            win32api.SetHandleInformation(h, win32con.HANDLE_FLAG_INHERIT, 0)
        # CPython's STARTUPINFOEX wrapper supplies an explicit handle allowlist.
        # bInheritHandles alone would also inherit the supervisor's server pipes.
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = win32process.STARTF_USESTDHANDLES
        startup.hStdInput, startup.hStdOutput, startup.hStdError = (int(h) for h in (child_in,child_out,child_err))
        startup.lpAttributeList={'handle_list':[int(h) for h in (child_in,child_out,child_err)]}
        thread = None
        try:
            process_handle, thread_handle, self.pid, _ = _winapi.CreateProcess(
                str(argv[0]), subprocess.list2cmdline([str(a) for a in argv]), None, None,
                True, win32process.CREATE_SUSPENDED | win32process.CREATE_NO_WINDOW,
                env, str(cwd), startup,
            )
            self.process=pywintypes.HANDLE(process_handle);thread=pywintypes.HANDLE(thread_handle)
            win32job.AssignProcessToJobObject(self.job, self.process)
            win32process.ResumeThread(thread)
        except BaseException:
            if self.process:
                win32api.TerminateProcess(self.process, 1)
                self.process.Close()
            for h in (self.stdin, self.stdout, self.stderr):
                h.Close()
            self.job.Close()
            raise
        finally:
            if thread:
                thread.Close()
            for h in (child_in, child_out, child_err):
                h.Close()

    def send(self, payload):
        win32file.WriteFile(self.stdin, payload)
        self.stdin.Close()
        self.stdin = None

    def exited(self):
        return win32event.WaitForSingleObject(self.process, 0) == win32event.WAIT_OBJECT_0

    def active(self):
        return win32job.QueryInformationJobObject(self.job, win32job.JobObjectBasicAccountingInformation)["ActiveProcesses"]

    def code(self):
        return win32process.GetExitCodeProcess(self.process)

    def kill(self):
        win32job.TerminateJobObject(self.job, 1)

    def close(self):
        # Last non-inherited job handle closes: all remaining descendants die.
        self.job.Close()
        for h in (self.stdin, self.stdout, self.stderr, self.process):
            if h:
                h.Close()
