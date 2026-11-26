"""Native sampling profiler for Windows x64 (ctypes + dbghelp), no admin rights.

Usage: python bench/winprof.py [--ms 1] [--top 40] OUT.json -- PYTHON TARGET.py ARGS...

TARGET.py must print READY after its imports, wait for a line on stdin, run the
work, print DONE and wait for another line (see bench/winprof_target.py). The
profiler suspends the target's main thread about every --ms milliseconds,
walks its native stack with StackWalk64 and resolves symbols with dbghelp
(PDBs next to the DLL/PYD files). It prints self and inclusive time per
function, plus an ownership split: each sample is charged to the innermost
frame that is either SimPy library code or the CPython bytecode interpreter
(Python code such as the model's generators).
"""
import collections
import ctypes as C
import ctypes.wintypes as W
import json
import os
import subprocess
import sys
import time

k32 = C.WinDLL('kernel32', use_last_error=True)
psapi = C.WinDLL('psapi', use_last_error=True)
dbg = C.WinDLL(os.path.join(os.environ['SystemRoot'], 'System32', 'dbghelp.dll'),
               use_last_error=True)

k32.OpenThread.restype = W.HANDLE
k32.OpenThread.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k32.OpenProcess.restype = W.HANDLE
k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k32.CreateToolhelp32Snapshot.restype = W.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
k32.SuspendThread.argtypes = [W.HANDLE]
k32.SuspendThread.restype = W.DWORD
k32.ResumeThread.argtypes = [W.HANDLE]
k32.ResumeThread.restype = W.DWORD
k32.GetThreadContext.argtypes = [W.HANDLE, C.c_void_p]
k32.GetThreadContext.restype = W.BOOL
dbg.SymInitializeW.argtypes = [W.HANDLE, W.LPCWSTR, W.BOOL]
dbg.SymInitializeW.restype = W.BOOL
dbg.SymSetOptions.argtypes = [W.DWORD]
dbg.SymFromAddr.argtypes = [W.HANDLE, C.c_uint64, C.POINTER(C.c_uint64), C.c_void_p]
dbg.SymFromAddr.restype = W.BOOL
dbg.SymGetModuleBase64.argtypes = [W.HANDLE, C.c_uint64]
dbg.SymGetModuleBase64.restype = C.c_uint64
dbg.StackWalk64.argtypes = [W.DWORD, W.HANDLE, W.HANDLE, C.c_void_p, C.c_void_p,
                            C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]
dbg.StackWalk64.restype = W.BOOL
psapi.EnumProcessModules.argtypes = [W.HANDLE, C.POINTER(W.HMODULE), W.DWORD,
                                     C.POINTER(W.DWORD)]
psapi.GetModuleBaseNameW.argtypes = [W.HANDLE, W.HMODULE, W.LPWSTR, W.DWORD]
psapi.GetModuleFileNameExW.argtypes = [W.HANDLE, W.HMODULE, W.LPWSTR, W.DWORD]


class THREADENTRY32(C.Structure):
    _fields_ = [('dwSize', W.DWORD), ('cntUsage', W.DWORD),
                ('th32ThreadID', W.DWORD), ('th32OwnerProcessID', W.DWORD),
                ('tpBasePri', W.LONG), ('tpDeltaPri', W.LONG),
                ('dwFlags', W.DWORD)]


CTX_SIZE = 1232
CONTEXT_FULL = 0x10000B
FTA = C.cast(dbg.SymFunctionTableAccess64, C.c_void_p)
GMB = C.cast(dbg.SymGetModuleBase64, C.c_void_p)


def aligned(size):
    raw = C.create_string_buffer(size + 16)
    return raw, (C.addressof(raw) + 15) & ~15


def main_thread_id(pid):
    snap = k32.CreateToolhelp32Snapshot(4, 0)
    te = THREADENTRY32()
    te.dwSize = C.sizeof(te)
    ok = k32.Thread32First(snap, C.byref(te))
    while ok:
        if te.th32OwnerProcessID == pid:
            k32.CloseHandle(snap)
            return te.th32ThreadID
        ok = k32.Thread32Next(snap, C.byref(te))
    raise RuntimeError('no thread')


def module_names(hproc):
    mods = (W.HMODULE * 1024)()
    need = W.DWORD()
    psapi.EnumProcessModules(hproc, mods, C.sizeof(mods), C.byref(need))
    out, dirs = {}, []
    for i in range(need.value // C.sizeof(W.HMODULE)):
        buf = C.create_unicode_buffer(1024)
        psapi.GetModuleFileNameExW(hproc, mods[i], buf, 1024)
        out[mods[i] or 0] = os.path.basename(buf.value)
        d = os.path.dirname(buf.value)
        if d not in dirs:
            dirs.append(d)
    return out, dirs


def main():
    argv = sys.argv[1:]
    ms, top = 1.0, 40
    while argv[0].startswith('--'):
        if argv[0] == '--ms':
            ms = float(argv[1])
        elif argv[0] == '--top':
            top = int(argv[1])
        argv = argv[2:]
    out_path, sep, cmd = argv[0], argv[1], argv[2:]
    assert sep == '--'
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True)
    line = proc.stdout.readline()
    assert line.strip() == 'READY', line
    hproc = k32.OpenProcess(0x0400 | 0x0010, False, proc.pid)
    hthr = k32.OpenThread(0x0002 | 0x0008 | 0x0040, False,
                          main_thread_id(proc.pid))
    dbg.SymSetOptions(0x2 | 0x4 | 0x200 | 0x80000)
    # The PDBs sit next to their DLL/PYD; dbghelp does not search there by
    # default, so the module folders form the symbol path.
    mods, dirs = module_names(hproc)
    assert dbg.SymInitializeW(hproc, ';'.join(dirs), True)

    ctx_raw, ctx = aligned(CTX_SIZE)
    walk_raw, walk = aligned(CTX_SIZE)
    sf = C.create_string_buffer(1024)
    sfa = C.addressof(sf)
    u64 = C.c_uint64.from_address
    samples = []
    proc.stdin.write('go\n')
    proc.stdin.flush()
    t0 = time.perf_counter()
    import threading
    done = threading.Event()

    def wait_done():
        proc.stdout.readline()
        done.set()

    threading.Thread(target=wait_done, daemon=True).start()
    while not done.is_set():
        if k32.SuspendThread(hthr) == 0xFFFFFFFF:
            break
        C.memset(ctx, 0, CTX_SIZE)
        C.c_uint32.from_address(ctx + 0x30).value = CONTEXT_FULL
        if k32.GetThreadContext(hthr, ctx):
            C.memmove(walk, ctx, CTX_SIZE)
            C.memset(sfa, 0, 1024)
            rip, rsp = u64(ctx + 0xF8).value, u64(ctx + 0x98).value
            for off, val in ((0, rip), (32, rsp), (48, rsp)):
                u64(sfa + off).value = val
                C.c_uint32.from_address(sfa + off + 12).value = 3
            frames = []
            while len(frames) < 96 and dbg.StackWalk64(
                    0x8664, hproc, hthr, sfa, walk, None, FTA, GMB, None):
                pc = u64(sfa).value
                if not pc:
                    break
                frames.append(pc)
            samples.append(frames or [rip])
        k32.ResumeThread(hthr)
        time.sleep(ms / 1000)
    wall = time.perf_counter() - t0

    sym_raw = C.create_string_buffer(88 + 512)
    syma = C.addressof(sym_raw)
    names = {}

    def name(pc):
        n = names.get(pc)
        if n is None:
            # Return addresses point after the call; look up pc-1.
            C.memset(syma, 0, 88)
            C.c_uint32.from_address(syma).value = 88
            C.c_uint32.from_address(syma + 80).value = 500
            disp = C.c_uint64()
            base = dbg.SymGetModuleBase64(hproc, pc)
            mod = mods.get(base, '?').rsplit('.', 1)[0].split('.')[0]
            if dbg.SymFromAddr(hproc, pc - 1, C.byref(disp), syma):
                fn = C.string_at(syma + 84).decode(errors='replace')
            else:
                fn = f'0x{pc - base:x}' if base else f'0x{pc:x}'
            n = names[pc] = f'{mod}!{fn}'
        return n

    stacks = [[name(pc) for pc in s] for s in samples]
    proc.stdin.write('bye\n')
    proc.stdin.flush()
    proc.wait()

    n = len(stacks)
    self_c = collections.Counter(s[0] for s in stacks)
    incl_c = collections.Counter()
    for s in stacks:
        for f in set(s):
            incl_c[f] += 1
    owner_c = collections.Counter()
    for s in stacks:
        owner = 'other'
        for f in s:
            if f.startswith(('core!', 'events!', 'base!', 'resource!', 'store!',
                             'container!')):
                owner = 'simpy-lib'
                break
            if f.endswith('!_PyEval_EvalFrameDefault'):
                owner = 'python-bytecode'
                break
        owner_c[owner] += 1
    print(f'samples={n} wall={wall:.2f}s interval={ms}ms')
    print('\n== ownership (innermost of simpy C code vs bytecode interpreter) ==')
    for k, v in owner_c.most_common():
        print(f'{100 * v / n:6.1f}%  {k}')
    print(f'\n== self (top {top}) ==')
    for k, v in self_c.most_common(top):
        print(f'{100 * v / n:6.1f}%  {k}')
    print(f'\n== inclusive (top {top}) ==')
    for k, v in incl_c.most_common(top):
        print(f'{100 * v / n:6.1f}%  {k}')
    with open(out_path, 'w') as f:
        json.dump({'wall': wall, 'stacks': stacks}, f)


if __name__ == '__main__':
    main()
